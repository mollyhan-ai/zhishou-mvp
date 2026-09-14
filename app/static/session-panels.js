'use strict';
let sidebarOffset = 0;
let sidebarRequest = 0;
let sidebarRows = [];
let sidebarSeenSession = null;
let sidebarRefreshTimer;
const narrowPanels = window.matchMedia('(max-width: 1100px)');

function displayTime(seconds, compact = false) {
  if (!Number.isFinite(seconds)) return '未记录';
  return new Date(seconds * 1000).toLocaleString('zh-CN', compact
    ? {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}
    : {year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
}
function sessionTitle(item) {
  return (item.display_name || item.pet?.name || '未命名会话') + ' · ' + displayTime(item.created_at, true);
}
function updatePanelControls() {
  $('btnToggleSessions').setAttribute('aria-expanded', String(!$('sessionSidebar').hidden));
  $('btnToggleDetails').setAttribute('aria-expanded', String(!$('sessionDetails').hidden));
  const overlaid = narrowPanels.matches && (!$('sessionSidebar').hidden || !$('sessionDetails').hidden);
  $('sidebarBackdrop').hidden = !overlaid;
  $('scribeLayout').querySelector('.scribe-center').inert = overlaid;
  document.querySelector('.workspace-toolbar').inert = overlaid;
}
function closeSessionPanels() {
  $('sessionSidebar').hidden = true; $('sessionDetails').hidden = true; updatePanelControls();
}
function toggleSessions() {
  const opening = $('sessionSidebar').hidden;
  if (opening && narrowPanels.matches) $('sessionDetails').hidden = true;
  $('sessionSidebar').hidden = !opening;
  updatePanelControls();
  if (opening) { loadSessionSidebar(); if (narrowPanels.matches) $('btnCloseSessions').focus(); }
}
function toggleDetails() {
  const opening = $('sessionDetails').hidden;
  if (opening && narrowPanels.matches) $('sessionSidebar').hidden = true;
  $('sessionDetails').hidden = !opening;
  updatePanelControls(); renderSessionDetails();
  if (opening && narrowPanels.matches) $('btnCloseDetails').focus();
}
function renderSessionDetails() {
  $('detailCreated').textContent = sessionId ? displayTime(session?.created_at) : '尚未创建';
  $('detailEdited').textContent = session?.last_content_edited_at ? displayTime(session.last_content_edited_at)
    : sessionId && (session?.transcript || session?.note || session?.history_raw || session?.has_audio)
      ? '旧记录未记录；最后更新：' + displayTime(session.updated_at) : '尚无编辑';
  $('detailGenerated').textContent = session?.note_generated_at ? displayTime(session.note_generated_at)
    : session?.note ? '未记录（已有病历）' : '尚未生成';
  const engines = {doubao:'豆包',whisper:'Whisper 兼容接口'};
  $('detailAsr').textContent = session?.asr_used_model
    ? (engines[session.asr_used_provider] || session.asr_used_provider || '转写') + ' · ' + session.asr_used_model
    : session?.transcript ? '未记录（手动输入或旧会话）' : '尚未转写';
  $('detailModel').textContent = session?.llm_used_model || (session?.note ? '未记录（旧会话或手动病历）' : '尚未生成');
  const player = $('player');
  const duration = session?.audio_duration_seconds ?? (player.currentSrc && player.currentSrc === player.src ? player.duration : NaN);
  const seconds = Number.isFinite(duration) && duration >= 0 ? Math.round(duration) : null;
  $('detailDuration').textContent = !session?.has_audio ? '无音频（文字会话）'
    : seconds === null ? '未获取（等待音频加载或格式不支持）'
      : `${Math.floor(seconds / 60)} 分 ${String(seconds % 60).padStart(2, '0')} 秒`;
  if(typeof renderSessionSummary==='function')renderSessionSummary();
}
function renderSessionPanels() {
  renderSessionDetails();
  if (typeof renderPetSelection === 'function') renderPetSelection();
  $('currentSessionTitle').textContent = sessionId ? sessionTitle(session) : '新会话';
  const signature = JSON.stringify([sessionId,session?.note_revision,session?.confirmed,!!session?.note,session?.pet?.id,session?.status,session?.has_audio]);
  if (signature !== sidebarSeenSession) {
    sidebarSeenSession = signature;
    renderSidebarRows();
    clearTimeout(sidebarRefreshTimer);
    sidebarRefreshTimer = setTimeout(() => loadSessionSidebar(), 180);
  }
}
function renderSidebarRows() {
  if (!sidebarRows.length) return;
  let previousGroup = '';
  const today = new Date();today.setHours(0,0,0,0);
  const yesterday = new Date(today);yesterday.setDate(today.getDate()-1);
  $('sessionList').innerHTML = sidebarRows.map(item => {
    const generated = item.created_at;
    const date = new Date(generated * 1000);
    const group = date >= today ? '今天' : date >= yesterday ? '昨天' : '更早';
    const heading = group !== previousGroup ? `<div class="session-day">${group}</div>` : '';
    previousGroup = group;
    const time = group === '更早' ? displayTime(generated, true) : date.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false});
    return heading + `<button class="session-card ${item.id === sessionId ? 'is-current' : ''}" data-sidebar-session="${escapeHtml(item.id)}" aria-current="${item.id === sessionId ? 'true' : 'false'}" title="${escapeHtml(sessionTitle(item))}">
      <span class="session-row-top"><strong>${escapeHtml(item.display_name || item.pet?.name || '未命名会话')}</strong><time>${escapeHtml(time)}</time></span>
      <span class="session-preview">${escapeHtml(item.preview || (item.audio_name ? '已保存录音' : '尚未生成病历'))}</span>
      <span class="session-card-meta"><span class="session-confirm-dot ${item.confirmed_at ? 'is-confirmed' : ''}"></span>${item.confirmed_at ? '已确认' : item.has_note ? '待确认' : ({created:'待转写',transcribing:'转写中',transcribed:'已转写',generating:'生成中',failed:'上次未完成'}[item.status] || '未完成')}</span>
    </button>`;
  }).join('');
  $('sessionList').querySelectorAll('[data-sidebar-session]').forEach(button => {
    button.addEventListener('click', () => openSession(button.dataset.sidebarSession));
  });
}
async function loadSessionSidebar(offset = sidebarOffset) {
  const ticket = ++sidebarRequest;
  $('btnSidebarPrev').disabled = true; $('btnSidebarNext').disabled = true;
  if (!sidebarRows.length) $('sessionList').textContent = '正在读取会话…';
  try {
    let r = await api('/api/history?scope=all&offset=' + offset);
    if (ticket !== sidebarRequest) return;
    if (!r.sessions.length && offset > 0) {
      offset = Math.max(0, Math.ceil(r.total / 20) - 1) * 20;
      r = await api('/api/history?scope=all&offset=' + offset);
      if (ticket !== sidebarRequest) return;
    }
    sidebarOffset = offset; sidebarRows = r.sessions;
    $('sidebarCount').textContent = `${r.total} 次会话`;
    $('sidebarPage').textContent = r.total ? `${offset / 20 + 1} / ${Math.ceil(r.total / 20)}` : '';
    $('btnSidebarPrev').disabled = offset === 0; $('btnSidebarNext').disabled = !r.has_more;
    if (sidebarRows.length) renderSidebarRows();
    else $('sessionList').innerHTML = '<p class="sidebar-empty">还没有会话。<br>新建会话后开始录音或输入逐字稿。</p>';
  } catch (e) {
    if (ticket !== sidebarRequest) return;
    sidebarRows = [];
    $('sessionList').textContent = '会话读取失败，请点击刷新重试。';
  }
}
function startNewSession() {
  if (!canSwitchSession()) return;
  resetSession(); showView('scribe');
  if (narrowPanels.matches) closeSessionPanels();
  $('transcript').focus(); toast('已开始新会话，之前的会话仍保留。');
}
async function copyNoteField(field) {
  if (!sessionId || !session?.confirmed || clinicalEdits.size) { toast('请先保存修改并由兽医确认。', true); return; }
  try {
    const r = await api(`/api/sessions/${sessionId}/export?field=${encodeURIComponent(field)}`);
    await navigator.clipboard.writeText(r.text);
    toast('此段病历已复制。');
  } catch (e) { showError(e); }
}
$('btnToggleSessions').addEventListener('click', toggleSessions);
$('btnCloseSessions').addEventListener('click', () => { $('sessionSidebar').hidden = true; updatePanelControls(); $('btnToggleSessions').focus(); });
$('btnToggleDetails').addEventListener('click', toggleDetails);
$('btnCloseDetails').addEventListener('click', () => { $('sessionDetails').hidden = true; updatePanelControls(); $('btnToggleDetails').focus(); });
$('btnSidebarNew').addEventListener('click', startNewSession);
$('btnToolbarNew').addEventListener('click', startNewSession);
$('btnSidebarRefresh').addEventListener('click', () => loadSessionSidebar(0));
$('btnSidebarPrev').addEventListener('click', () => loadSessionSidebar(Math.max(0, sidebarOffset - 20)));
$('btnSidebarNext').addEventListener('click', () => loadSessionSidebar(sidebarOffset + 20));
$('sidebarBackdrop').addEventListener('click', closeSessionPanels);
['loadedmetadata','durationchange','emptied','error'].forEach(event => $('player').addEventListener(event, renderSessionDetails));
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && (!$('sessionDetails').hidden || (narrowPanels.matches && !$('sessionSidebar').hidden))) {
    const detailsWereOpen = !$('sessionDetails').hidden;
    if (detailsWereOpen) $('sessionDetails').hidden = true;
    else $('sessionSidebar').hidden = true;
    updatePanelControls(); $(detailsWereOpen ? 'btnToggleDetails' : 'btnToggleSessions').focus();
  }
});
narrowPanels.addEventListener('change', () => { if (narrowPanels.matches) closeSessionPanels(); else updatePanelControls(); });
if (narrowPanels.matches) { $('sessionSidebar').hidden = true;$('sessionDetails').hidden=true; }
updatePanelControls();
loadSessionSidebar();
