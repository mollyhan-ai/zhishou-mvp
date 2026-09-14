/* 知兽 MVP frontend.
   Rules encoded here mirror the server:
   - a config error is reported as a config error, never as a fake result
   - copy/export stay disabled until the vet confirms (server enforces too)
   - editing the note revokes confirmation */
'use strict';

const $ = (id) => document.getElementById(id);
const FIELDS = [
  ['subjective', 'S 主观'],
  ['objective', 'O 客观'],
  ['assessment', 'A 评估'],
  ['plan', 'P 计划'],
];
const SUBJECTIVE_FIELDS = [
  ['chief_complaint', '主诉'], ['past_history', '既往病史'], ['present_illness', '现病史'],
];
function hasSubjectiveSections(note) {
  return !!note?.subjective_sections && SUBJECTIVE_FIELDS.every(([key]) => typeof note.subjective_sections[key] === 'string');
}
function noteFieldValue(note, key) {
  return (SUBJECTIVE_FIELDS.some(([field]) => field === key) ? note?.subjective_sections?.[key] : note?.[key]) || '';
}
function readNoteForm() {
  const note = {};
  document.querySelectorAll('[data-note-field]').forEach(el => { note[el.dataset.noteField] = el.value; });
  if (hasSubjectiveSections(session.note)) {
    note.subjective_sections = Object.fromEntries(SUBJECTIVE_FIELDS.map(([key]) => [key, note[key]]));
    SUBJECTIVE_FIELDS.forEach(([key]) => { delete note[key]; });
  }
  note.uncertain = session.note.uncertain || [];
  return note;
}
function noteFieldCard(key, label, count = 0) {
  return `<div class="note-field ${count ? 'has-flag' : ''}" data-field="${key}">
    <div class="note-field-head"><label for="note-${key}">${label}</label>${count ? `<span class="fl">${count} 项待核对</span>` : ''}<button class="btn btn-ghost btn-inline copy-field" data-copy-field="${key}" aria-label="复制 ${label}" disabled>复制</button></div>
    <textarea id="note-${key}" data-note-field="${key}">${escapeHtml(noteFieldValue(session.note, key))}</textarea>
  </div>`;
}
const KIND_LABEL = {
  drug: '药名', dose: '剂量', frequency: '频次',
  duration: '疗程', source: '来源标注', negation: '否定关系', placeholder: '留空',
};

let sessionId = null;
let session = null;
let recorder = null;
let activeRecordingStartedAt = null, recordingClock = null, defaultAsrLanguage = null;
let chunks = [];
let activeFlagId = null;
let kbMode = 'placeholder';
let asrProvider = 'whisper';
let sessionRequests = 0;
let clinicalEdits = new Set();
let saveQueue = Promise.resolve();
let sessionCreation = null;
let generationBusy = false;
function queueSave(task) {
  const next = saveQueue.then(task, task);
  saveQueue = next.catch(() => false);
  return next;
}
function markClinicalEdit(name) {
  clinicalEdits.add(name);
  renderSigning();
  if (typeof renderReviewPanel === 'function') renderReviewPanel();
  if (typeof renderDocumentChrome === 'function') renderDocumentChrome();
}

/* ── helpers ────────────────────────────────────────── */
function toast(msg, isError) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.toggle('is-error', !!isError);
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.hidden = true; }, 5200);
}

async function api(path, opts = {}) {
  const tracked = path.startsWith('/api/sessions');
  if (tracked) sessionRequests++;
  try {
  const res = await fetch(path, opts);
  let body = {};
  try { body = await res.json(); } catch (e) { /* non-JSON (download) */ }
  if (!res.ok) {
    const err = new Error(body.message || `请求失败 (${res.status})`);
    Object.assign(err, body, { status: res.status });
    throw err;
  }
  return body;
  } finally { if (tracked) sessionRequests--; }
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function showError(err) {
  if (err.error_kind === 'config') {
    const vars = (err.missing || []).map((m) => `<code>${escapeHtml(m)}</code>`).join('、');
    const b = $('configBanner');
    b.innerHTML = `配置错误：${escapeHtml(err.message)}` +
      (vars ? `<br>缺少：${vars}` : '') +
      `<br>这不是模型故障，重试不会有帮助。请到「设置与管理」保存配置。`;
    b.hidden = false;
  }
  toast(err.message || '出错了', true);
}

/* ── status ─────────────────────────────────────────── */
async function loadStatus() {
  try {
    const s = await api('/api/status');
    asrProvider = s.asr_provider || 'whisper';
    defaultAsrLanguage=s.asr_language||null;
    if(typeof renderSessionDetails==='function')renderSessionDetails();
    const bit = (ok, name, model) => ok
      ? `<span class="ok">●</span> ${name}${model ? ' ' + escapeHtml(model) : ''}`
      : `<span class="bad">●</span> ${name} 未配置`;
    $('configStatus').innerHTML =
      bit(s.asr_ready, '转写', s.asr_model) + '　' +
      bit(s.llm_ready, '模型', s.llm_model);

    const missing = [...(s.asr_missing || []), ...(s.llm_missing || [])];
    $('configBanner').hidden = missing.length === 0;
    if (missing.length) {
      const b = $('configBanner');
      b.innerHTML = '尚未配置：' + missing.map((m) => `<code>${escapeHtml(m)}</code>`).join('、') +
        '。转写和病历生成会返回配置错误——系统不会用假结果冒充成功。';
      b.hidden = false;
    }
    $('kbPrivacy').textContent = s.privacy_notice || '';
  } catch (e) { /* status is best effort */ }
}

/* ── session ────────────────────────────────────────── */
async function ensureSession() {
  if (sessionId) return sessionId;
  if (!sessionCreation) sessionCreation = api('/api/sessions', {method:'POST'}).then((r) => {
    sessionId = r.session.id; session = r.session; rememberSession(); return sessionId;
  }).finally(() => { sessionCreation = null; });
  return sessionCreation;
}

function setStatus(text, kind, retry) {
  const strip = $('statusStrip');
  strip.hidden = false;
  strip.className = 'status-strip' + (kind ? ' is-' + kind : '');
  $('statusText').textContent = text;
  $('btnRetry').hidden = !retry;
  $('btnRetry').dataset.action = retry || '';
}

function render() {
  if (!session) return;

  renderHistory();
  renderSessionPanels();
  $('evidenceChanged').hidden = !session.note_needs_review;

  // audio
  $('audioMeta').textContent = session.has_audio
    ? `${session.audio_name || '录音'} · ${(session.audio_bytes / 1024).toFixed(0)} KB`
    : '';
  $('btnDeleteAudio').hidden = !session.has_audio;

  // transcript
  const ta = $('transcript');
  if (!clinicalEdits.has('transcript')) ta.value = session.transcript || '';
  $('transcriptMeta').textContent = session.transcript
    ? `${session.transcript.length} 字` : '';

  // note
  const wrap = $('noteFields');
  if (clinicalEdits.has('note')) {
    // Keep the vet's unsaved edits while another input is being saved.
  } else if (!session.note) {
    wrap.innerHTML = '<div class="note-empty">还没有病历。先转写或粘贴逐字稿，再点「生成病历」。</div>';
    $('uncertainBox').hidden = true;
  } else {
    const byField = {};
    (session.flags || []).forEach((f) => {
      (byField[f.field] = byField[f.field] || []).push(f);
    });
    if (hasSubjectiveSections(session.note)) {
      const count = (byField.subjective || []).length;
      wrap.innerHTML = `<div class="subjective-group" data-field="subjective">
        <div class="note-field-head"><span>S 主观</span>${count ? `<span class="fl">${count} 项待核对（整个 S 段）</span>` : ''}</div>
        ${SUBJECTIVE_FIELDS.map(([key, label]) => noteFieldCard(key, label)).join('')}
      </div>` + FIELDS.slice(1).map(([key, label]) => noteFieldCard(key, label, (byField[key] || []).length)).join('');
    } else {
      wrap.innerHTML = '<p class="hint legacy-subjective">旧版 S 保留原文，未自动拆分；重新生成病历后使用三个子字段。</p>' +
        FIELDS.map(([key, label]) => noteFieldCard(key, key === 'subjective' ? 'S 主观（旧版原文）' : label, (byField[key] || []).length)).join('');
    }
    wrap.querySelectorAll('[data-copy-field]').forEach(el => el.addEventListener('click', () => copyNoteField(el.dataset.copyField)));
    wrap.querySelectorAll('textarea').forEach((el) => {
      el.addEventListener('input', () => markClinicalEdit('note'));
      el.addEventListener('change', saveNote);
    });
    const unc = session.note.uncertain || [];
    $('uncertainBox').hidden = unc.length === 0;
    if (unc.length) {
      $('uncertainBox').innerHTML = '<strong>模型标记为待确认：</strong><ul>' +
        unc.map((u) => `<li>${escapeHtml(u)}</li>`).join('') + '</ul>';
    }
  }

  // flags
  const flags = session.flags || [];
  const s = session.flag_summary || { total: 0, high: 0 };
  $('flagCount').textContent = session.note
    ? (s.total ? `　${s.total} 项（${s.high} 项高优先）` : '　全部通过')
    : '';
  const list = $('flagList');
  if (!session.note) {
    list.innerHTML = '';
  } else if (!flags.length) {
    list.innerHTML = '<div class="flags-clear">在当前逐字稿与有效既往摘要范围内，未发现药名、剂量、频次、疗程或来源、否定关系问题。' +
      '这不代表内容一定正确，仍需兽医通读。</div>';
  } else {
    list.innerHTML = flags.map((f) => `
      <li role="button" tabindex="0" class="flag sev-${f.severity} ${f.id === activeFlagId ? 'is-active' : ''}" data-flag="${f.id}" data-term="${escapeHtml(f.term)}">
        <div class="flag-head">
          <span class="flag-kind">${KIND_LABEL[f.kind] || f.kind}</span>
          ${f.term ? `<span class="flag-term">${escapeHtml(f.term)}</span>` : ''}
        </div>
        <div class="flag-msg">${escapeHtml(f.message)}</div>
      </li>`).join('');
    list.querySelectorAll('.flag').forEach((el) => {
      el.addEventListener('click', () => locateTerm(el.dataset.flag, el.dataset.term));
      el.addEventListener('keydown', event => { if (['Enter',' '].includes(event.key)) { event.preventDefault();locateTerm(el.dataset.flag, el.dataset.term); } });
    });
  }

  renderSigning();
  if (typeof renderReviewPanel === 'function') renderReviewPanel();
  if (typeof renderDocumentChrome === 'function') renderDocumentChrome();
}

function renderSigning() {
  const confirmed = !!session?.confirmed && !session?.patient_review_needed && clinicalEdits.size === 0;
  $('signBadge').className = 'badge ' + (confirmed ? 'badge-signed' : 'badge-pending');
  $('signBadge').textContent = confirmed ? '已确认' : '待兽医确认';
  $('signNote').textContent = confirmed
    ? `由 ${session?.confirmed_by} 确认。任何修改都会撤销确认。`
    : '病历未经确认前不能复制或导出。';
  $('btnCopy').disabled = !confirmed;
  $('btnExport').disabled = !confirmed;
  document.querySelectorAll('[data-copy-field]').forEach(el => { el.disabled = !confirmed; el.title = confirmed ? '复制此段病历' : '兽医确认后可复制'; });
  $('btnConfirm').hidden = confirmed;
  $('btnConfirm').disabled = clinicalEdits.size > 0 || generationBusy || !!session?.patient_review_needed;
  if (session?.patient_review_needed) $('signNote').textContent='逐字稿已变化，请先重新核对本次就诊对象。';
  $('confirmedBy').hidden = confirmed;
  $('btnUnconfirm').hidden = !confirmed;
  if(typeof renderSessionSummary==='function')renderSessionSummary();
}

/* ── flag → transcript ──────────────────────────────── */
function locateTerm(flagId, term, reveal = true) {
  activeFlagId = flagId;
  const box = $('transcriptHighlight');
  if (!term) { box.hidden = true; return; }
  const summaries = [...document.querySelectorAll('[data-history-field]')];
  const candidates = [{label:'本次逐字稿', text:$('transcript').value, el:$('transcript')},
    {label:'已确认档案背景（本次快照）',text:session?.patient_context?.summary ? Object.values(session.patient_context.summary).join('\n') : '',el:$('historyPanel')},
    ...summaries.map(el => ({label:'既往摘要 · ' + el.previousElementSibling.textContent,
      text:session?.history_summary_stale ? '' : el.value, el}))];
  const matches = candidates.filter(item => item.text.includes(term));
  box.hidden = false;
  box.innerHTML = matches.length ? matches.map(item => {
    const i = item.text.indexOf(term);
    return `<div><strong>${escapeHtml(item.label)}</strong>：…${escapeHtml(item.text.slice(Math.max(0,i-30),i))}<mark>${escapeHtml(term)}</mark>${escapeHtml(item.text.slice(i+term.length,i+term.length+30))}…</div>`;
  }).join('') : `逐字稿与有效既往摘要中没有找到「${escapeHtml(term)}」的原文匹配，请核对两路依据。`;
  // A running server may still serve the previous cached template until restart.
  if (!$('reviewSources')) {
    const preferred = matches[0];
    if (preferred) {
      if (preferred.el !== $('transcript')) $('historyPanel').open = true;
      preferred.el.focus();
      const i = preferred.text.indexOf(term); preferred.el.setSelectionRange?.(i, i + term.length);
    }
    return;
  }
  $('reviewSources').hidden = false;
  document.querySelectorAll('[data-flag]').forEach(el => el.classList.toggle('is-active', el.dataset.flag === flagId));
  if (reveal) {
    reviewOpen = true;
    selectDocumentTab('soap');
    const flag = (session?.flags || []).find(item => item.id === flagId);
    const field = document.querySelector(`[data-field="${flag?.field}"]`);
    if (field) field.scrollIntoView({block:'nearest'});
    $('reviewSources').scrollIntoView({block:'nearest'});
  }
}

/* ── actions ────────────────────────────────────────── */
async function uploadBlob(blob, name, metadata = {}) {
  await ensureSession();
  const fd = new FormData();
  fd.append('file', blob, name);
  fd.append('audio_origin',metadata.recorded_at?'recording':'upload');
  if(metadata.recorded_at)fd.append('recorded_at',String(metadata.recorded_at));
  setStatus('正在上传音频…', 'working');
  try {
    const r = await api(`/api/sessions/${sessionId}/audio`, { method: 'POST', body: fd });
    session = r.session;
    if ($('player').src.startsWith('blob:')) URL.revokeObjectURL($('player').src);
    $('player').src = URL.createObjectURL(blob);
    $('player').hidden = false;
    if (typeof selectDocumentTab === 'function') selectDocumentTab('transcript');
    setStatus('音频已就绪，可以开始转写。', 'done');
    render();
    return true;
  } catch (e) { setStatus(e.message, 'failed'); showError(e); return false; }
}

async function transcribe() {
  if (generationBusy) return;
  if (!sessionId) { toast('请先录音或上传音频', true); return; }
  if ($('btnRecord').disabled || (recorder && recorder.state === 'recording')) { toast('请先完成录音或音频整理。', true); return; }
  setGenerationBusy(true); setAudioBusy(true);
  setStatus('正在转写，较长录音会分段处理；全部片段成功后才更新逐字稿…', 'working');
  let completed = false, completedSid = sessionId, completedText = '';
  try {
    const r = await api(`/api/sessions/${sessionId}/transcribe`, { method: 'POST' });
    session = r.session;
    if (typeof selectDocumentTab === 'function') selectDocumentTab('transcript');
    const meta = r.transcription_meta;
    const received = meta ? `已收到 ${meta.completed_chunks}/${meta.chunk_count} 段转写结果（音频 ${Math.round(meta.audio_seconds)} 秒）。` : '已收到转写结果。';
    completed = true; completedText = session.transcript || '';
    setStatus(received + '正在继续生成 SOAP 病历…', 'working');
    render();
  } catch (e) {
    setStatus(e.message, 'failed', e.retryable ? 'transcribe' : '');
    showError(e);
  } finally { setGenerationBusy(false); setAudioBusy(false); }
  if (!completed || completedSid !== sessionId || completedText !== session?.transcript) return false;

  // Match the recording-first workflow: a successful transcript immediately
  // produces the note, then prepares an unconfirmed patient proposal. Either
  // downstream failure leaves the saved transcript available for a retry.
  await generateNote();
  if (completedSid === sessionId && completedText === session?.transcript &&
      typeof extractPatientCandidates === 'function') await extractPatientCandidates();
  return true;
}

function saveTranscript() { return queueSave(saveTranscriptNow); }
async function saveTranscriptNow() {
  if (!$('transcript').value.trim() && !sessionId) return false;
  if (sessionId && $('transcript').value === (session.transcript || '')) { clinicalEdits.delete('transcript'); renderSigning(); return true; }
  try {
    await ensureSession();
    const submitted = $('transcript').value;
    const r = await api(`/api/sessions/${sessionId}/transcript`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcript: submitted }),
    });
    if ($('transcript').value === submitted) clinicalEdits.delete('transcript');
    session = r.session;
    render();
    return true;
  } catch (e) { showError(e); return false; }
}

async function generateNote() {
  if (generationBusy) return;
  setGenerationBusy(true);
  try {
    if (!await saveClinicalInputs()) return false;
    setStatus('正在生成病历…', 'working');
    const r = await api(`/api/sessions/${sessionId}/note`, { method: 'POST' });
    session = r.session;
    if (typeof showSoapWorkspace === 'function') showSoapWorkspace();
    const n = session.flag_summary.total;
    setStatus(n ? `病历已生成，有 ${n} 项需要核对。` : '病历已生成，自动核对未发现问题。', 'done');
    render();
    return true;
  } catch (e) {
    setStatus(e.message, 'failed', e.retryable ? 'generate' : '');
    showError(e);
    return false;
  } finally { setGenerationBusy(false); }
}

function saveNote() { return queueSave(saveNoteNow); }
async function saveNoteNow() {
  if (!sessionId || !session.note) return;
  const note = readNoteForm();
  try {
    const r = await api(`/api/sessions/${sessionId}/note`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    });
    if ([...document.querySelectorAll('[data-note-field]')].every(el => el.value === noteFieldValue(note, el.dataset.noteField))) clinicalEdits.delete('note');
    session = r.session;
    render();
    toast('已保存修改，核对清单已重新生成，确认状态已撤销。');
  } catch (e) { showError(e); }
}

async function confirmNote() {
  if (generationBusy || clinicalEdits.size) { toast('请先保存修改并核对病历。', true); return; }
  const who = $('confirmedBy').value.trim();
  if (!who) { toast('请填写确认人', true); $('confirmedBy').focus(); return; }
  try {
    const r = await api(`/api/sessions/${sessionId}/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed_by: who, evidence_revision: session.evidence_revision, note_revision: session.note_revision }),
    });
    session = r.session;
    render();
    toast('已确认，现在可以复制或导出。');
  } catch (e) { showError(e); }
}

async function del(part) {
  if (generationBusy) { toast('请等待当前处理完成后再删除。', true); return; }
  await saveQueue;
  if (!sessionId) return;
  const what = { audio: '音频', transcript: '逐字稿', note: '病历', all: '整个会话' }[part];
  if (!window.confirm(`确定删除${what}？此操作不可恢复。`)) return;
  try {
    if (part === 'all') {
      await api(`/api/sessions/${sessionId}`, { method: 'DELETE' });
      resetSession();
      toast('会话已删除。');
      return;
    }
    const r = await api(`/api/sessions/${sessionId}/${part}`, { method: 'DELETE' });
    session = r.session;
    if (part === 'transcript') { clinicalEdits.delete('transcript'); $('transcript').value = ''; }
    if (part === 'note') clinicalEdits.delete('note');
    if (part === 'audio') { $('player').hidden = true; $('player').removeAttribute('src'); }
    render();
    toast(`${what}已删除。`);
  } catch (e) { showError(e); }
}

/* ── recording ──────────────────────────────────────── */
// Convert locally so browser-specific recording containers never reach the audio API.
function encodeMonoWav(samples, sampleRate) {
  const bytes = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(bytes);
  const label = (offset, value) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  label(0, 'RIFF'); view.setUint32(4, bytes.byteLength - 8, true);
  label(8, 'WAVE'); label(12, 'fmt '); view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  label(36, 'data'); view.setUint32(40, samples.length * 2, true);
  samples.forEach((value, i) => {
    const n = Math.max(-1, Math.min(1, value));
    view.setInt16(44 + i * 2, Math.round(n * (n < 0 ? 32768 : 32767)), true);
  });
  return new Blob([bytes], { type: 'audio/wav' });
}

async function recordingToWav(blob) {
  const Context = window.AudioContext || window.webkitAudioContext;
  if (!Context || !window.OfflineAudioContext) throw new Error('当前浏览器无法整理录音，请上传 WAV / MP3 / M4A 文件。');
  const context = new Context();
  try {
    const decoded = await context.decodeAudioData(await blob.arrayBuffer());
    // Bound decoding/resampling memory; this is not a recognition guarantee.
    if (decoded.duration > 600) throw new Error('当前音频最多支持 10 分钟，请缩短录音或文件后重试。');
    const offline = new OfflineAudioContext(1, Math.max(1, Math.ceil(decoded.duration * 16000)), 16000);
    const source = offline.createBufferSource();
    source.buffer = decoded; source.connect(offline.destination); source.start();
    const mono = await offline.startRendering();
    return encodeMonoWav(mono.getChannelData(0), 16000);
  } finally { await context.close(); }
}

async function toggleRecord() {
  const btn = $('btnRecord');
  if (recorder && recorder.state === 'recording') {
    recorder.stop();
    return;
  }
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    toast('当前浏览器不支持录音，请改用「上传音频」。', true);
    return;
  }
  if (generationBusy) { toast('请等待当前处理完成后再录音。', true); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    let recordedAt;
    recorder.onstop = async () => {
      clearInterval(recordingClock);recordingClock=null;activeRecordingStartedAt=null;
      if(typeof renderSessionDetails==='function')renderSessionDetails();
      stream.getTracks().forEach((t) => t.stop());
      btn.textContent = '开始录音';
      btn.classList.remove('is-recording');
      setAudioBusy(true);
      const mime = recorder.mimeType || 'audio/webm';
      const blob = new Blob(chunks, { type: mime });
      let uploaded = false;
      try {
        if (blob.size && asrProvider === 'doubao') {
          setStatus('正在整理录音…', 'working');
          uploaded = await uploadBlob(await recordingToWav(blob), 'recording.wav', {recorded_at:recordedAt});
        } else if (blob.size) {
          const ext = mime.includes('mp4') ? 'm4a' : mime.includes('ogg') ? 'ogg' : 'webm';
          uploaded = await uploadBlob(blob, `recording.${ext}`, {recorded_at:recordedAt});
        }
      } catch (e) {
        setStatus('录音处理失败，请重录或上传 WAV / MP3 / M4A 音频。', 'error');
        showError(e);
      } finally { setAudioBusy(false); }
      if (uploaded) transcribe();
    };
    recorder.start();
    recordedAt=Date.now()/1000;activeRecordingStartedAt=recordedAt;
    recordingClock=setInterval(()=>{if(typeof renderSessionDetails==='function')renderSessionDetails();},1000);
    if(typeof renderSessionDetails==='function')renderSessionDetails();
    btn.textContent = '停止录音';
    btn.classList.add('is-recording');
    setStatus('录音中…', 'working');
  } catch (e) {
    toast('无法访问麦克风：' + e.message, true);
  }
}

/* ── knowledge assistant ────────────────────────────── */
async function loadKb() {
  try {
    const s = await api('/api/kb/status');
    kbMode = s.mode;
    const card = $('kbModeCard');
    card.className = 'kb-mode mode-' + s.mode;
    const scope = `<div class="scope">
        <div><h3>计划覆盖</h3><ul>${s.planned_scope.map((x) => `<li>${escapeHtml(x)}</li>`).join('')}</ul></div>
        <div class="no"><h3>明确不覆盖</h3><ul>${s.non_scope.map((x) => `<li>${escapeHtml(x)}</li>`).join('')}</ul></div>
      </div>`;

    $('kbRequestBox').hidden = true;
    $('kbRequestResult').hidden = true;

    if (s.mode === 'off') {
      card.innerHTML = '<h2>知识问答已关闭</h2><p>当前部署关闭了这个模块。把 <code>ZS_KB_MODE</code> 改为 <code>placeholder</code> 或 <code>rag</code> 可以开启。</p>';
    } else if (s.mode === 'placeholder') {
      const c = s.request_copy;
      card.innerHTML = '<h2>为什么还没开放</h2>' +
        '<p>这个功能要等有合法授权、可标注来源的兽医文献入库后才会开放。在那之前，' +
        '系统不会用模型的泛化知识临时拼一个答案——一个听起来可信但无法验证的医学回答，比没有回答更危险。</p>' + scope;
      $('kbRequestIntro').textContent = c.intro;
      $('kbQuestion').placeholder = c.placeholder;
      $('kbQuestion').previousElementSibling.textContent = '需求内容';
      $('btnSubmitRequest').textContent = c.submit_label;
      $('kbPrivacy').textContent = c.pii_warning;
      $('kbRetention').textContent = `提交内容保留 ${s.retention_days} 天后自动删除。`;
      $('kbRequestBox').hidden = false;
    } else {
      const cov = s.coverage || { doc_count: 0 };
      card.innerHTML = `<h2>知识问答（限知识库范围）</h2>
        <p>已入库 ${cov.doc_count} 份文档。只依据这些文档回答，每条结论都带可打开的引用；
        证据不足、互相冲突或超出范围时会明确拒答。</p>` + scope;
      renderCoverage(cov);
      if (cov.doc_count > 0) {
        $('kbRequestIntro').textContent =
          '只依据已入库的授权文档回答。每条结论都带可打开的引用，证据不足时会明确拒答。';
        $('kbQuestion').placeholder = '例如：院内规范里初始评估要记录哪些项目？';
        $('kbQuestion').previousElementSibling.textContent = '问题内容';
        $('btnSubmitRequest').textContent = '提问';
        $('kbPrivacy').textContent = s.privacy_notice || '';
        $('kbRetention').textContent = '';
        $('kbRequestBox').hidden = false;
      }
    }
  } catch (e) { showError(e); }
}

/* Demand collection. No chat bubbles, no typing animation, no simulated
   answer. The success line appears only when the server confirms the row
   was written. */
async function submitRequest() {
  const btn = $('btnSubmitRequest');
  const box = $('kbRequestResult');
  const text = $('kbQuestion').value.trim();

  box.hidden = true;
  box.className = 'request-result';

  if (!text) {
    box.hidden = false;
    box.classList.add('is-failed');
    box.textContent = '请先写下你希望助手帮你查什么，再提交。';
    $('kbQuestion').focus();
    return;
  }

  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = '提交中…';
  try {
    const r = await api('/api/kb/requests', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!r.saved) throw new Error(r.message || '提交失败，需求没有保存。');

    box.hidden = false;
    box.classList.add('is-saved');
    let extra = `<span class="sub">保留 ${r.retention_days} 天后自动删除。不会生成任何答案。</span>`;
    if (r.scrubbed && r.scrubbed.length) {
      extra = `<span class="sub">已自动移除疑似可识别信息后保存。保留 ${r.retention_days} 天后自动删除。</span>`;
    }
    box.innerHTML = escapeHtml(r.message) + extra;
    $('kbQuestion').value = '';
  } catch (e) {
    box.hidden = false;
    box.classList.add('is-failed');
    box.innerHTML = escapeHtml(e.message || '提交失败，需求没有保存。') +
      '<span class="sub">需求没有保存，可以稍后重试。</span>';
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

function renderCoverage(c) {
  const box = $('kbCoverage');
  if (!c.documents || !c.documents.length) {
    box.innerHTML = '<p>知识库为空。用 <code>python3 tools/import_docs.py import kb_docs/</code> 导入你有权使用的文档。</p>';
    return;
  }
  box.innerHTML = '<strong>当前知识库</strong><table><tr><th>标题</th><th>来源</th><th>版本</th><th>发布日期</th><th>主题</th></tr>' +
    c.documents.map((d) => `<tr>
      <td>${escapeHtml(d.title)}</td><td>${escapeHtml(d.source)}</td>
      <td>${escapeHtml(d.version || '—')}</td><td>${escapeHtml(d.published_on || '—')}</td>
      <td>${escapeHtml((d.topics || []).join('、') || '—')}</td></tr>`).join('') + '</table>';
}

async function ask() {
  const q = $('kbQuestion').value.trim();
  const box = $('kbAnswer');
  box.hidden = false;
  box.className = 'kb-answer';
  box.innerHTML = '查询中…';
  try {
    const r = await api('/api/kb/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q }),
    });

    if (r.mode === 'off') {
      box.className = 'kb-answer is-refused';
      box.innerHTML = `<p class="refuse-title">${escapeHtml(r.message)}</p>`;
      return;
    }

    if (!r.answerable) {
      const why = {
        no_documents: '知识库里还没有文档。',
        no_evidence: '知识库里没有足以支撑回答的内容。',
        conflict: '检索到的资料互相冲突，无法给出单一结论。',
        unverified_citation: '生成的结论没有全部通过引用校验，已整体丢弃。',
      }[r.reason] || '无法回答。';
      box.className = 'kb-answer is-refused';
      box.innerHTML = `<p class="refuse-title">当前知识库无法回答这个问题。</p><p>${escapeHtml(why)}</p>` +
        (r.detail ? `<p class="hint">${escapeHtml(r.detail)}</p>` : '') +
        renderSources(r.sources, '检索到但未采用的片段');
      bindCites();
      return;
    }

    box.innerHTML = r.claims.map((c) => `<div class="claim">${escapeHtml(c.text)}` +
      c.citations.map((ct) => `<button class="cite" data-chunk="${ct.chunk_id}" title="${escapeHtml(ct.title + ' · ' + ct.locator)}">${escapeHtml(ct.tag)}</button>`).join('') +
      '</div>').join('') +
      (r.caveats.length ? `<div class="uncertain" style="margin-top:12px"><strong>资料中写明的注意事项：</strong><ul>${r.caveats.map((x) => `<li>${escapeHtml(x)}</li>`).join('')}</ul></div>` : '') +
      renderSources(r.sources, '本次引用的文档');
    bindCites();
  } catch (e) {
    box.className = 'kb-answer is-refused';
    box.innerHTML = `<p class="refuse-title">查询失败</p><p>${escapeHtml(e.message)}</p>`;
    showError(e);
  }
}

function renderSources(sources, label) {
  if (!sources || !sources.length) return '';
  return `<div class="kb-sources"><strong>${label}</strong><ul>` +
    sources.map((s) => `<li>${escapeHtml(s.title)}${s.version ? ' ' + escapeHtml(s.version) : ''} · ${escapeHtml(s.locator)} · ${escapeHtml(s.source)}（相关度 ${s.score}）</li>`).join('') +
    '</ul></div>';
}

function bindCites() {
  document.querySelectorAll('.cite').forEach((el) => {
    el.addEventListener('click', () => openSource(el.dataset.chunk));
  });
}

async function openSource(chunkId) {
  try {
    const r = await api('/api/kb/source/' + chunkId);
    $('drawerTitle').textContent = r.title;
    $('drawerMeta').innerHTML =
      `来源：${escapeHtml(r.source)}<br>` +
      `版本：${escapeHtml(r.version || '—')}　发布：${escapeHtml(r.published_on || '—')}<br>` +
      `位置：${escapeHtml(r.locator)}<br>` +
      `使用权：${escapeHtml(r.license_note)}`;
    $('drawerText').textContent = r.text;
    $('sourceDrawer').hidden = false;
  } catch (e) { showError(e); }
}

/* ── wiring ─────────────────────────────────────────── */
$('btnRecord').addEventListener('click', toggleRecord);
function setAudioBusy(busy) {
  ['btnRecord', 'fileInput', 'btnDeleteAudio'].forEach(id => { $(id).disabled = busy; });
}
async function prepareAudioUpload(file) {
  if (generationBusy || $('btnRecord').disabled || (recorder && recorder.state === 'recording')) {
    toast('请先完成当前录音或处理，再上传音频。', true); return;
  }
  setAudioBusy(true);
  let uploaded = false;
  try {
    if (file.size > 100 * 1024 * 1024) throw new Error('文件超过 100MB，请缩小文件后上传。');
    if (asrProvider === 'doubao') {
      setStatus('正在本机整理音频，完成后可分段转写…', 'working');
      const wav = await recordingToWav(file);
      uploaded = await uploadBlob(wav, file.name.replace(/\.[^.]+$/, '') + '.wav');
    } else uploaded = await uploadBlob(file, file.name);
  } catch (e) {
    setStatus('音频整理失败，原会话音频未更换。请检查格式、时长，或导出标准 WAV 后上传。', 'failed');
    showError(e);
  } finally { setAudioBusy(false); }
  if (uploaded) transcribe();
}
$('fileInput').addEventListener('change', (e) => {
  const f = e.target.files[0];
  if (f) prepareAudioUpload(f);
  e.target.value = '';
});
$('btnTranscribe').addEventListener('click', transcribe);
$('btnGenerate').addEventListener('click', generateNote);
$('transcript').addEventListener('input', () => markClinicalEdit('transcript'));
$('transcript').addEventListener('change', saveTranscript);
$('btnConfirm').addEventListener('click', confirmNote);
$('btnUnconfirm').addEventListener('click', async () => {
  const r = await api(`/api/sessions/${sessionId}/unconfirm`, { method: 'POST' });
  session = r.session; render();
});
$('btnRetry').addEventListener('click', (e) => {
  const a = e.target.dataset.action;
  if (a === 'transcribe') transcribe();
  if (a === 'generate') generateNote();
});
$('btnCopy').addEventListener('click', async () => {
  if (clinicalEdits.size) return;
  try {
    const r = await api(`/api/sessions/${sessionId}/export`);
    await navigator.clipboard.writeText(r.text);
    toast('已复制到剪贴板。');
  } catch (e) { showError(e); }
});
$('btnExport').addEventListener('click', () => {
  if (clinicalEdits.size) return;
  window.location = `/api/sessions/${sessionId}/export?fmt=download`;
});
$('btnDeleteAudio').addEventListener('click', () => del('audio'));
$('btnDeleteTranscript').addEventListener('click', () => del('transcript'));
$('btnDeleteNote').addEventListener('click', () => del('note'));
$('btnDeleteAll').addEventListener('click', () => del('all'));
$('btnSubmitRequest').addEventListener('click', () => {
  if (kbMode === 'rag') ask(); else submitRequest();
});
$('drawerClose').addEventListener('click', () => { $('sourceDrawer').hidden = true; });
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') $('sourceDrawer').hidden = true;
});

loadStatus();
