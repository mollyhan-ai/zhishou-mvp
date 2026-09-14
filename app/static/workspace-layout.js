/* Tabs only change visibility: editable evidence retains its original nodes. */
'use strict';
let documentTab = 'transcript';
let reviewOpen = false;
function selectDocumentTab(name, focus = false) {
  if (!['patient', 'history', 'transcript', 'soap'].includes(name)) return;
  documentTab = name;
  document.querySelectorAll('[data-document-tab]').forEach(button => {
    const selected = button.dataset.documentTab === name;
    button.classList.toggle('is-active', selected);
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected || (name === 'patient' && button.dataset.documentTab === 'history') ? 0 : -1;
    $('panel-' + button.dataset.documentTab).hidden = !selected;
  });
  $('panel-patient').hidden = name !== 'patient';
  $('btnSessionPet').setAttribute('aria-pressed', String(name === 'patient'));
  $('btnSessionPet').classList.toggle('is-active', name === 'patient');
  document.querySelector('.scribe-center').classList.toggle('viewing-patient', name === 'patient');
  if (name === 'history') $('historyPanel').open = true;
  renderReviewPanel();
  if (typeof renderDocumentChrome === 'function') renderDocumentChrome();
  if (focus) $(name === 'patient' ? 'btnSessionPet' : 'tab-' + name).focus();
}
function resetDocumentWorkspace() {
  reviewOpen = false;
  $('reviewSources').hidden = true;
  selectDocumentTab('transcript');
}
function showSoapWorkspace() {
  reviewOpen = !!session?.flags?.length;
  selectDocumentTab('soap');
}
function renderReviewPanel() {
  const visible = reviewOpen && documentTab === 'soap';
  $('reviewPanel').hidden = !visible;
  $('view-scribe').classList.toggle('has-review', visible);
  $('btnToggleReview').setAttribute('aria-expanded', String(visible));
  $('btnToggleReview').textContent = (session?.flags?.length ? `核对清单 · ${session.flags.length}` : '核对清单');
  $('reviewPending').hidden = clinicalEdits.size === 0;
  $('reviewTranscript').textContent = $('transcript').value || '尚无逐字稿。';
  const stale = session?.history_summary_stale || clinicalEdits.has('historyRaw');
  const history = historyInputs().map(el => ({label:el.previousElementSibling.textContent, text:el.value.trim()})).filter(x => x.text);
  $('reviewHistory').textContent = session?.effective_history || (stale ? '' : history.map(x => `${x.label}：${x.text}`).join('\n\n'));
  $('reviewHistoryState').textContent = session?.patient_context?.pet_id ? '包含兽医确认的档案背景快照；档案后续修改不会自动改写此依据。' : stale ? '既往原文已变化，旧摘要暂不参与核对。' : history.length ? '' : '未填写既往摘要，本次仅对照逐字稿。';
  if (activeFlagId && !clinicalEdits.size) {
    const flag = session?.flags?.find(f => f.id === activeFlagId);
    if (flag) locateTerm(flag.id, flag.term, false);
    else { activeFlagId = null; $('transcriptHighlight').hidden = true; }
  }
}
function toggleReview() {
  if (documentTab !== 'soap') { reviewOpen = true; selectDocumentTab('soap'); }
  else { reviewOpen = !reviewOpen; renderReviewPanel(); }
}
document.querySelectorAll('[data-document-tab]').forEach(button => {
  button.addEventListener('click', () => selectDocumentTab(button.dataset.documentTab));
  button.addEventListener('keydown', event => {
    const keys = ['history','transcript','soap'];let index = keys.indexOf(documentTab);
    if (event.key === 'ArrowRight') index = (index + 1) % keys.length;
    else if (event.key === 'ArrowLeft') index = (index + 2) % keys.length;
    else if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = 2;
    else return;
    event.preventDefault();selectDocumentTab(keys[index], true);
  });
});
$('btnToggleReview').addEventListener('click', toggleReview);
$('btnCloseReview').addEventListener('click', () => { reviewOpen = false;renderReviewPanel();$('btnToggleReview').focus(); });
document.querySelectorAll('[data-edit-source]').forEach(button => button.addEventListener('click', () => {
  selectDocumentTab(button.dataset.editSource);
  $(button.dataset.editSource === 'transcript' ? 'transcript' : 'historyMedications').focus();
}));
selectDocumentTab('transcript');
