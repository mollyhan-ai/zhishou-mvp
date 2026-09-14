/* Paper-like text fields keep the same input/save/confirmation pipeline. */
'use strict';
let paperFrame = 0;
function resizePaperFields() {
  cancelAnimationFrame(paperFrame);
  paperFrame = requestAnimationFrame(() => {
    document.querySelectorAll('#noteFields textarea').forEach(field => {
      if (!field.getClientRects().length) return;
      field.style.height = '0px';
      field.style.height = Math.max(30, field.scrollHeight + 2) + 'px';
    });
  });
}
function renderDocumentChrome() {
  const hasNote = !!session?.note;
  document.querySelector('.document-actions').hidden = documentTab === 'patient';
  $('btnCopy').hidden = documentTab !== 'soap';
  $('btnExport').hidden = documentTab !== 'soap';
  $('btnDeleteNote').hidden = !hasNote;
  $('documentState').textContent = documentTab === 'history' ? '既往背景 · 选填'
    : documentTab === 'transcript' ? (session?.has_audio ? '对照音频，核对逐字稿' : '录制问诊，或直接粘贴文字')
      : hasNote ? (session?.confirmed && !clinicalEdits.size ? '已确认病历' : '病历草稿 · 待核对') : '尚未生成病历';
  document.querySelector('.audio-block').hidden = !session?.has_audio;
  resizePaperFields();
}
new MutationObserver(renderDocumentChrome).observe($('noteFields'),{childList:true,subtree:true});
$('noteFields').addEventListener('input',resizePaperFields);
window.addEventListener('resize',resizePaperFields);
let paperWidth = 0;
if (typeof ResizeObserver !== 'undefined') new ResizeObserver(entries => {
  const width = entries[0].contentRect.width;
  if (width !== paperWidth) { paperWidth = width;resizePaperFields(); }
}).observe(document.querySelector('.document-area'));
document.addEventListener('click',event => {
  const more = document.querySelector('.document-more');
  if (more.open && !more.contains(event.target)) more.open = false;
});
document.querySelector('.document-more-menu').addEventListener('click',()=>{document.querySelector('.document-more').open=false;});
document.addEventListener('keydown',event=>{if(event.key==='Escape')document.querySelector('.document-more').open=false;});
renderDocumentChrome();

// Upload remains a direct user gesture; the menu adds no recording prerequisite.
$('btnUploadAudio').onclick=()=>{$('recordMenu').open=false;$('fileInput').click();};
$('recordMenu').addEventListener('keydown',event=>{
 if(event.key==='Escape') {event.preventDefault();event.stopPropagation();$('recordMenu').open=false;$('recordMenu').querySelector('summary').focus();}
});
document.addEventListener('click',event=>{if(!$('recordMenu').contains(event.target))$('recordMenu').open=false;});
