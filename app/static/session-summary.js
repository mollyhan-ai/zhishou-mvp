'use strict';
let summaryEditor=null,summarySaving=false;
let quickPatientAnchor=null;
let quickPatientTicket=0,quickPatientTimer=null,summarySessionSeen=null;
function sessionLanguageLabel(value){return ({zh:'中文（普通话）','zh-CN':'中文（普通话）',en:'英语',ja:'日语',ko:'韩语',auto:'自动'})[value]||value||'未记录';}
function formatAudioClock(value){if(!Number.isFinite(value)||value<0)return '等待音频加载';const n=Math.floor(value);return `${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;}
function canExportSummary(){return !!session?.confirmed && !!session?.note && !session?.patient_review_needed && !clinicalEdits.size && !generationBusy;}
function renderSessionSummary(){
 if(!$('sessionTime'))return;
 const p=session?.pet,c=candidateForSession(),candidateName=c?.data.candidate.name?.value||'';
 $('quickPatientAvatar').textContent=p?Array.from(p.name||p.record_number||'宠')[0]:candidateName?Array.from(candidateName)[0]:'＋';
 $('quickPatientAvatar').classList.toggle('is-linked',!!p);
 const complaint=hasSubjectiveSections(session?.note)?session.note.subjective_sections.chief_complaint:session?.note?.subjective;
 const preview=(session?.custom_summary||complaint||'').replace(/\s+/g,' ').trim();
 $('sessionComplaint').textContent=preview?preview.slice(0,60)+(preview.length>60?'…':''):'添加问诊摘要';
 $('sessionComplaintRow').hidden=!sessionId || !!summaryEditor;
 $('btnSummaryPatientArchive').hidden=!p;
 $('quickPatientName').textContent=p?(p.name||p.record_number):candidateName?`${candidateName} · 待关联`:'添加宠物';
 $('btnQuickPatient').title=p?`${p.name||'未命名'} · ${p.record_number}`:candidateName?'已从逐字稿读取疑似姓名；点击匹配已有档案或新建':'搜索已有宠物或创建新档案';
 let time=session?.created_at,label='创建时间';
 if(activeRecordingStartedAt){time=activeRecordingStartedAt;label='录制时间';}
 else if(session?.has_audio){
  if(session.recorded_at){time=session.recorded_at;label='录制时间';}
  else if(session.audio_uploaded_at){time=session.audio_uploaded_at;label='上传时间';}
  else {time=null;label='录制时间';}
 }
 $('sessionTimeLabel').textContent=label;$('sessionTime').textContent=time?displayTime(time,true):session?.has_audio?'旧记录未保存':activeRecordingStartedAt?'正在录音':'尚未创建';
 const player=$('player');const seconds=session?.audio_duration_seconds ?? (player.currentSrc&&player.currentSrc===player.src?player.duration:NaN);
 $('sessionDurationLabel').textContent=session?.audio_origin==='upload'?'音频时长':'录制时长';
 $('sessionDuration').textContent=activeRecordingStartedAt?formatAudioClock(Date.now()/1000-activeRecordingStartedAt)+' · 录制中':session?.has_audio?formatAudioClock(seconds):session?.transcript?'文字会话':'尚无录音';
 $('sessionLanguage').textContent=session?.asr_used_language?sessionLanguageLabel(session.asr_used_language)+' · 本次转写':session?.transcript?'未记录':sessionLanguageLabel(defaultAsrLanguage)+' · 当前设置';
 const allowed=canExportSummary();$('btnSummaryExport').disabled=!allowed;$('btnSummaryCopy').disabled=!allowed;
 $('sessionNoteState').textContent=!session?.note?'尚未生成':allowed?'已确认':session?.patient_review_needed?'就诊对象待重新核对':'待兽医确认';
 $('patientReviewCount').textContent=session?.patient_review_needed?'· 待核对':c?'· 有疑似资料':'';
 if(typeof scheduleAutomaticPatientCandidates==='function')scheduleAutomaticPatientCandidates();
 if(summarySessionSeen!==sessionId){summarySessionSeen=sessionId;if(typeof closeLinkedPatientMenu==='function')closeLinkedPatientMenu();closeSummaryEditor(false);$('sessionDetails').scrollTop=0;closeQuickPatient();$('patientReviewDetails').open=false;$('consultMore').open=false;}
}
function closeQuickPatient(){if(!$('quickPatientMenu'))return;quickPatientTicket++;clearTimeout(quickPatientTimer);$('quickPatientMenu').hidden=true;(quickPatientAnchor||$('btnQuickPatient')).setAttribute('aria-expanded','false');}
function openQuickPatient(anchor=$('btnQuickPatient')){
 if(!canSwitchSession())return;
 if(!$('quickPatientMenu').hidden){closeQuickPatient();return;}
 quickPatientAnchor=anchor;closeLinkedPatientMenu();
 $('quickPatientMenu').hidden=false;anchor.setAttribute('aria-expanded','true');
 const rect=anchor.getBoundingClientRect(),width=Math.min(330,window.innerWidth-32);
 Object.assign($('quickPatientMenu').style,{position:'fixed',width:width+'px',left:Math.max(8,Math.min(rect.left,window.innerWidth-width-8))+'px',right:'auto',top:Math.max(8,Math.min(rect.bottom+4,window.innerHeight-300))+'px',maxHeight:Math.max(120,Math.min(window.innerHeight-32,window.innerHeight-Math.max(8,Math.min(rect.bottom+4,window.innerHeight-300))-12))+'px'});
 $('quickPatientSearch').value=candidateForSession()?.data.candidate.name?.value||'';
 $('btnQuickUnlinked').textContent=session?.pet?'保持当前关联':'暂不关联，继续本次会话';
 searchQuickPatient();$('quickPatientSearch').focus();
}
async function searchQuickPatient(){
 const ticket=++quickPatientTicket,query=$('quickPatientSearch').value.trim();
 $('btnQuickCreate').textContent=query?`＋ 新建「${query}」的档案`:'＋ 新建宠物档案';
 $('quickPatientResults').textContent='正在查找…';$('btnQuickMore').hidden=true;$('btnClearQuickPatient').hidden=!query;
 if(!query){$('quickPatientResults').textContent='输入宠物名称、编号或已确认别名查找。';return;}
 try{
  const r=await petApi('/api/pets/search','POST',{query,offset:0});if(ticket!==quickPatientTicket||$('quickPatientMenu').hidden)return;
  $('quickPatientResults').innerHTML=r.pets.slice(0,6).map(p=>`<button class="quick-patient-result" type="button" data-quick-pet="${patientText(p.id)}"><strong>${patientText(p.name||p.record_number)}</strong><span>${patientText(p.record_number)} · ${patientText(petSpeciesLabel[p.species])}</span><small>${p.last_visit?'上次就诊 '+patientText(displayTime(p.last_visit,true)):'尚无就诊记录'}</small></button>`).join('')||'<p class="hint">没有找到匹配档案，可用此名字新建。</p>';
  $('quickPatientResults').querySelectorAll('[data-quick-pet]').forEach(b=>b.onclick=()=>{if(!canSwitchSession())return;const p=r.pets.find(p=>p.id===b.dataset.quickPet);closeQuickPatient();openCompactPatientReview(p);});
  $('btnQuickMore').hidden=r.total<=6;
 }catch(e){if(ticket===quickPatientTicket)$('quickPatientResults').textContent=e.message;}
}
function createQuickPatient(){
 if(!canSwitchSession())return;const name=$('quickPatientSearch').value.trim();closeQuickPatient();openCompactPatientReview(null,name);
 if(name&&patientEditor){$('pf-name').value=name;$('patientAccepted').checked=false;renderPatientDecisions();}
}
window.addEventListener('resize',closeQuickPatient);
$('sessionDetails').addEventListener('scroll',closeQuickPatient);
$('btnQuickPatient').onclick=()=>session?.pet?openLinkedPatientMenu():openQuickPatient($('btnQuickPatient'));
// The top entry navigates; the right entry changes the current association.
$('btnSessionPet').onclick=()=>{if(session?.pet){openPatientWorkspace();return;}openQuickPatient($('btnSessionPet'));};
$('btnCloseQuickPatient').onclick=()=>{closeQuickPatient();(quickPatientAnchor||$('btnQuickPatient')).focus();};
$('quickPatientSearch').addEventListener('input',()=>{quickPatientTicket++;clearTimeout(quickPatientTimer);quickPatientTimer=setTimeout(searchQuickPatient,180);});
$('quickPatientSearch').addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closeQuickPatient();(quickPatientAnchor||$('btnQuickPatient')).focus();}if(e.key==='Enter'){e.preventDefault();clearTimeout(quickPatientTimer);searchQuickPatient();}});
$('btnQuickCreate').onclick=createQuickPatient;$('btnQuickUnlinked').onclick=closeQuickPatient;
$('btnQuickMore').onclick=()=>{const q=$('quickPatientSearch').value;closeQuickPatient();openPetPicker();$('petPickerSearch').value=q;loadPicker(0);};
document.addEventListener('click',e=>{if(!$('quickPatientMenu').hidden&&!$('quickPatientMenu').contains(e.target)&&!e.target.closest('#btnQuickPatient')&&!e.target.closest('#btnSessionPet'))closeQuickPatient();});
$('btnSummaryExport').onclick=()=>{if(canExportSummary())$('btnExport').click();};
$('btnSummaryPatientArchive').onclick=()=>{if(session?.pet)openPatientWorkspace();};
$('btnSummaryCopy').onclick=()=>{if(canExportSummary())$('btnCopy').click();};
$('btnSummaryReview').onclick=()=>{reviewOpen=true;selectDocumentTab('soap');};
$('consultSessionCard').addEventListener('toggle',()=>{if(!$('consultSessionCard').open)closeQuickPatient();});
renderSessionSummary();

function summaryEditPending(){return !!summaryEditor || summarySaving;}
function openSummaryEditor(){
 if(!sessionId || summarySaving)return;
 const automatic=hasSubjectiveSections(session?.note)?session.note.subjective_sections.chief_complaint:session?.note?.subjective;
 summaryEditor={sid:sessionId,revision:session?.summary_revision||0};
 $('sessionSummaryInput').value=(session?.custom_summary||automatic||'').slice(0,200);
 $('sessionSummaryError').textContent='';$('sessionSummaryForm').hidden=false;
 $('sessionComplaintRow').hidden=true;$('sessionComplaintRow').setAttribute('aria-expanded','true');
 $('sessionSummaryInput').focus();
}
function closeSummaryEditor(focus=true){
 summaryEditor=null;$('sessionSummaryForm').hidden=true;$('sessionSummaryError').textContent='';
 $('sessionComplaintRow').hidden=!sessionId;$('sessionComplaintRow').setAttribute('aria-expanded','false');
 if(focus)$('sessionComplaintRow').focus();
}
async function saveSummaryEditor(event){
 event.preventDefault();if(!summaryEditor || summarySaving)return;
 const editor=summaryEditor,value=$('sessionSummaryInput').value;
 if(editor.sid!==sessionId){closeSummaryEditor(false);return;}
 summarySaving=true;$('btnSaveSessionSummary').disabled=$('btnCancelSessionSummary').disabled=$('sessionSummaryInput').disabled=true;
 $('sessionSummaryError').textContent='';
 try{
  const r=await api('/api/sessions/'+encodeURIComponent(editor.sid)+'/summary',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({summary:value,revision:editor.revision})});
  if(!r.saved)throw new Error('摘要未保存，请重试。');
  if(editor.sid!==sessionId)return;
  // Merge only metadata so concurrent edits to transcript/SOAP keep their nodes and values.
  session.custom_summary=r.custom_summary;session.summary_revision=r.summary_revision;
  closeSummaryEditor();renderSessionSummary();
  if(typeof renderPatientWorkspace==='function')renderPatientWorkspace();
  toast('问诊摘要已保存。');
  loadSessionSidebar().catch(()=>toast('摘要已保存，会话列表刷新失败，请点击列表中的刷新。',true));
 }catch(e){if(editor.sid===sessionId)$('sessionSummaryError').textContent=e.message;}
 finally{summarySaving=false;$('btnSaveSessionSummary').disabled=$('btnCancelSessionSummary').disabled=$('sessionSummaryInput').disabled=false;}
}
$('sessionComplaintRow').onclick=openSummaryEditor;
$('sessionSummaryForm').onsubmit=saveSummaryEditor;
$('btnCancelSessionSummary').onclick=()=>{if(!summarySaving)closeSummaryEditor();};
$('sessionSummaryInput').addEventListener('keydown',e=>{
 if(e.key==='Escape'&&!summarySaving){e.preventDefault();e.stopPropagation();closeSummaryEditor();}
 if(e.key==='Enter'&&(e.metaKey||e.ctrlKey)){e.preventDefault();$('sessionSummaryForm').requestSubmit();}
});
window.addEventListener('beforeunload',e=>{if(summaryEditPending()){e.preventDefault();e.returnValue='';}});
