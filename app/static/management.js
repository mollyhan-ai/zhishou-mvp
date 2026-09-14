'use strict';
let historyOffset = 0;
let historyLoading = false;
let openingSession = false;
let settingsBusy = false;

function showView(view) {
  if(view!=='scribe' && typeof patientInlineEditing!=='undefined' && patientInlineEditing){toast('档案资料正在编辑，请先保存或取消。',true);return;}
  ['scribe', 'kb', 'manage', 'pets'].forEach((name) => { if ($('view-' + name)) $('view-' + name).hidden = name !== view; });
  $('signbar').hidden = view !== 'scribe';
  $('scribeLayout').hidden = view !== 'scribe';
  if (view === 'scribe') { renderSessionPanels(); loadSessionSidebar(); }
  document.querySelectorAll('.tab').forEach((tab) => {
    const selected = tab.dataset.view === view;
    tab.classList.toggle('is-active', selected);
    tab.setAttribute('aria-selected', String(selected));
  });
  if (view === 'pets' && typeof loadPetPage === 'function') loadPetPage();
  if (view === 'kb') loadKb();
  if (view === 'manage') { loadSettings(); loadHistory(); }
}

function rememberSession() {
  const url = new URL(location.href);
  if (sessionId) url.searchParams.set('session', sessionId);
  else url.searchParams.delete('session');
  history.replaceState(null, '', url);
}

function canSwitchSession() {
  if(typeof patientInlineEditing!=='undefined' && patientInlineEditing){toast('档案资料正在编辑，请先保存或取消。',true);return false;}
  if(typeof summaryEditPending==='function' && summaryEditPending()){toast('问诊摘要还在编辑，请先保存或取消。',true);return false;}
  if ((typeof petBusy !== 'undefined' && petBusy) || openingSession || sessionRequests || generationBusy || (recorder && recorder.state === 'recording') || $('btnRecord').disabled) {
    toast('正在录音、处理或保存，请完成后再切换会话。', true);
    return false;
  }
  const dirtyTranscript = $('transcript').value !== (session?.transcript || '');
  const dirtyNote = [...document.querySelectorAll('[data-note-field]')].some((el) => el.value !== noteFieldValue(session?.note, el.dataset.noteField));
  if (dirtyTranscript || dirtyNote || clinicalEdits.size) {
    toast('当前修改还未保存。请在病历助手中完成保存后再切换。', true);
    return false;
  }
  return true;
}

function clearPlayer() {
  const player = $('player');
  player.pause();
  if (player.src.startsWith('blob:')) URL.revokeObjectURL(player.src);
  player.removeAttribute('src');
  player.load();
  player.hidden = true;
}

function resetSession() {
  clearPlayer();
  sessionId = null;
  session = {};
  clinicalEdits.clear();
  $('historyPanel').open = false;
  $('historyStatus').textContent = '';
  activeFlagId = null;
  $('transcript').value = '';
  $('confirmedBy').value = '';
  $('statusStrip').hidden = true;
  $('transcriptHighlight').hidden = true;
  if (typeof resetDocumentWorkspace === 'function') resetDocumentWorkspace();
  render();
  rememberSession();
}

async function openSession(id) {
  if (!canSwitchSession()) return;
  openingSession = true;
  document.querySelector('.workspace-toolbar').inert = true;
  $('view-scribe').inert = true;
  $('signbar').inert = true;
  try {
    const r = await api('/api/sessions/' + encodeURIComponent(id));
    clearPlayer();
    sessionId = r.session.id;
    session = r.session;
    clinicalEdits.clear();
    $('historyPanel').open = !!(session.history_raw || Object.values(session.history_summary || {}).some(Boolean));
    $('historyStatus').textContent = '';
    activeFlagId = null;
    $('transcriptHighlight').hidden = true;
    $('transcript').value = session.transcript || '';
    $('confirmedBy').value = session.confirmed_by || '';
    if (session.has_audio) {
      $('player').src = '/api/sessions/' + encodeURIComponent(sessionId) + '/audio';
      $('player').hidden = false;
    }
    if (typeof showSoapWorkspace === 'function') { if (session.note) showSoapWorkspace(); else resetDocumentWorkspace(); }
    if ($('reviewSources')) $('reviewSources').hidden = true;
    render();
    rememberSession();
    showView('scribe');
    if (session.error_message) setStatus(session.error_message, 'failed');
    else if (['transcribing', 'generating'].includes(session.status)) setStatus('这次会话上次仍在处理中。请稍后从历史列表重新打开；若服务曾重启，可重新操作。', 'working');
    else $('statusStrip').hidden = true;
    if (window.matchMedia('(max-width: 1100px)').matches) closeSessionPanels();
  } catch (e) { showError(e); }
  finally { document.querySelector('.workspace-toolbar').inert = false; openingSession = false; $('view-scribe').inert = false; $('signbar').inert = false; }
}

async function loadSettings() {
  try {
    const r = await api('/api/settings');
    $('settingsSummary').textContent = `密钥${r.key_configured ? '已配置' : '未配置'} · 转写：${r.asr_model} · 病历：${r.llm_model}`;
    $('settingsModel').textContent = r.recommended_model;
  } catch (e) { $('settingsSummary').textContent = e.message; }
}

async function settingsAction(save) {
  if (settingsBusy) return;
  if (save && !canSwitchSession()) return;
  settingsBusy = true;
  $('btnSaveSettings').disabled = true;
  $('btnTestConnection').disabled = true;
  const result = $('settingsResult');
  result.hidden = false;
  result.className = 'request-result';
  result.textContent = save ? '正在保存配置…' : '正在连接豆包，请稍候…';
  try {
    const body = save ? {api_key: $('settingsKey').value} : {};
    const r = await api('/api/settings/' + (save ? 'doubao' : 'test'), {
      method: save ? 'PUT' : 'POST',
      headers: {'Content-Type': 'application/json', 'X-Management-Token': document.querySelector('meta[name="management-token"]').content},
      body: JSON.stringify(body),
    });
    if (save && !r.saved) throw new Error('配置没有保存。');
    result.classList.add('is-saved');
    result.textContent = r.message;
    if (save) { await loadSettings(); await loadStatus(); }
  } catch (e) {
    result.classList.add('is-failed');
    result.textContent = e.message;
  } finally {
    if (save) $('settingsKey').value = '';
    settingsBusy = false;
    $('btnSaveSettings').disabled = false;
    $('btnTestConnection').disabled = false;
  }
}

async function loadHistory(offset = historyOffset) {
  if (historyLoading) return;
  historyLoading = true;
  $('btnRefreshHistory').disabled = true;
  $('btnHistoryPrev').disabled = true;
  $('btnHistoryNext').disabled = true;
  $('historyList').textContent = '正在读取历史会话…';
  try {
    let r = await api('/api/history?offset=' + offset);
    if (!r.sessions.length && offset > 0) {
      offset = Math.max(0, Math.ceil(r.total / 20) - 1) * 20;
      r = await api('/api/history?offset=' + offset);
    }
    historyOffset = offset;
    $('historyCount').textContent = `共 ${r.total} 次`;
    const labels = {created: '待转写', transcribing: '转写中', transcribed: '已转写', generating: '生成中', drafted: '待确认', failed: '上次未完成'};
    $('historyList').innerHTML = r.sessions.length ? r.sessions.map((s) => {
      const date = new Date(s.created_at * 1000).toLocaleString('zh-CN', {hour12: false});
      const status = s.confirmed_at ? '已确认' : labels[s.status] || '未确认';
      return `<div class="history-row"><div><strong>${escapeHtml(date)}</strong><div class="hint">${escapeHtml(s.audio_name || '文字会话')} · ${escapeHtml(status)}</div></div><button class="btn btn-ghost" data-open-session="${escapeHtml(s.id)}" aria-label="打开 ${escapeHtml(date)} 的会话">打开会话</button></div>`;
    }).join('') : '<div class="note-empty">还没有会话。点击「新建会话」开始录音或输入逐字稿。</div>';
    document.querySelectorAll('[data-open-session]').forEach((button) => button.addEventListener('click', () => openSession(button.dataset.openSession)));
    $('historyPage').textContent = r.total ? `第 ${offset / 20 + 1} / ${Math.ceil(r.total / 20)} 页` : '';
    $('btnHistoryPrev').disabled = offset === 0;
    $('btnHistoryNext').disabled = !r.has_more;
  } catch (e) { $('historyList').textContent = e.message + ' 请点击刷新列表重试。'; }
  finally { historyLoading = false; $('btnRefreshHistory').disabled = false; }
}

document.querySelectorAll('.tab').forEach((tab) => tab.addEventListener('click', () => showView(tab.dataset.view)));
$('settingsForm').addEventListener('submit', (e) => { e.preventDefault(); settingsAction(true); });
$('btnTestConnection').addEventListener('click', () => settingsAction(false));
$('btnRefreshHistory').addEventListener('click', () => loadHistory());
$('btnHistoryPrev').addEventListener('click', () => loadHistory(historyOffset - 20));
$('btnHistoryNext').addEventListener('click', () => loadHistory(historyOffset + 20));
$('btnNewSession').addEventListener('click', startNewSession);
$('player').addEventListener('error', () => {
  if ($('player').getAttribute('src')) toast('录音无法播放，文件可能已删除或格式不受浏览器支持。', true);
});
const rememberedSession = new URL(location.href).searchParams.get('session');
if (rememberedSession) openSession(rememberedSession);
