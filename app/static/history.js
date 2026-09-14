'use strict';
const historyInputs = () => [...document.querySelectorAll('[data-history-field]')];
function readHistorySummary() {
  return Object.fromEntries(historyInputs().map(el => [el.dataset.historyField, el.value]));
}
function renderHistory() {
  if (!clinicalEdits.has('historyRaw')) $('historyRaw').value = session?.history_raw || '';
  if (!clinicalEdits.has('historySummary')) historyInputs().forEach(el => {
    el.value = session?.history_summary?.[el.dataset.historyField] || '';
  });
  $('historyStale').hidden = !session?.history_summary_stale;
}
function historyMessage(text, failed = false) {
  $('historyStatus').textContent = text;
  $('historyStatus').classList.toggle('hint-warn', failed);
}
function setGenerationBusy(busy) {
  generationBusy = busy;
  ['btnHistoryGenerate', 'btnGenerate', 'btnTranscribe'].forEach(id => { $(id).disabled = busy; });
  renderSigning();
}
function saveHistoryRaw() {
  return queueSave(async () => {
    const raw = $('historyRaw').value;
    if (raw === (session?.history_raw || '')) { clinicalEdits.delete('historyRaw'); renderSigning(); return true; }
    try {
      await ensureSession();
      const r = await api(`/api/sessions/${sessionId}/history`, {method:'PUT',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({raw})});
      if ($('historyRaw').value === raw) clinicalEdits.delete('historyRaw');
      session = r.session; render();
      historyMessage(raw.trim() ? '原文已保存。请生成或核对既往摘要后使用。' : '既往内容已清空，本次仅使用逐字稿作为依据。');
      return true;
    } catch (e) { historyMessage('原文未保存：' + e.message, true); return false; }
  });
}
function saveHistorySummary(force = false) {
  return queueSave(async () => {
    if (!force && !clinicalEdits.has('historySummary')) return true;
    const summary = readHistorySummary();
    try {
      await ensureSession();
      const r = await api(`/api/sessions/${sessionId}/history-summary`, {method:'PUT',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({summary})});
      if (JSON.stringify(readHistorySummary()) === JSON.stringify(summary)) clinicalEdits.delete('historySummary');
      session = r.session; render();
      historyMessage('摘要已保存。已有病历的核对清单已更新，需重新确认。');
      return true;
    } catch (e) { historyMessage('摘要未保存：' + e.message, true); return false; }
  });
}
async function saveClinicalInputs() {
  await saveQueue;
  if (!await saveHistoryRaw()) return false;
  if (!await saveHistorySummary()) return false;
  if (!await saveTranscript()) { toast('请先填写并保存逐字稿。', true); return false; }
  if (clinicalEdits.has('note')) await saveNote();
  if (clinicalEdits.size) { toast('仍有修改未保存，请先完成保存。', true); return false; }
  return true;
}
async function generateHistorySummary() {
  if (generationBusy) return;
  setGenerationBusy(true);
  try {
    await saveQueue;
    if (!await saveHistoryRaw()) return;
    if (!session?.history_raw?.trim()) { historyMessage('请先填写既往病历原文。', true); return; }
    // A new summary deliberately replaces the old one, including saved vet edits.
    if (Object.values(session.history_summary || {}).some(Boolean) && !window.confirm('重新生成会替换现有摘要（包括手动修改），继续吗？')) return;
    if (clinicalEdits.has('historySummary')) {
      historyMessage('请先保存摘要修改，再重新生成。', true); return;
    }
    historyMessage('正在生成既往摘要…');
    const r = await api(`/api/sessions/${sessionId}/history-summary`, {method:'POST'});
    session = r.session; render();
    historyMessage('摘要已生成，请对照原文核对并修改；只有摘要会参与本次病历生成。');
  } catch (e) { historyMessage('摘要未更新：' + e.message, true); }
  finally { setGenerationBusy(false); }
}
$('historyRaw').addEventListener('input', () => markClinicalEdit('historyRaw'));
$('historyRaw').addEventListener('change', saveHistoryRaw);
historyInputs().forEach(el => {
  el.addEventListener('input', () => markClinicalEdit('historySummary'));
  el.addEventListener('change', () => saveHistorySummary());
});
$('btnHistoryGenerate').addEventListener('click', generateHistorySummary);
$('btnHistorySaveRaw').addEventListener('click', saveHistoryRaw);
$('btnHistorySaveSummary').addEventListener('click', async () => {
  if (await saveHistoryRaw()) await saveHistorySummary(true);
});
window.addEventListener('beforeunload', (event) => {
  if (clinicalEdits.size) { event.preventDefault(); event.returnValue = ''; }
});
