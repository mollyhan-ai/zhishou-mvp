'use strict';
let patientInlineEditing=false,unlinkPatientState=null,unlinkPatientBusy=false;
// The same popover must work when the right sidebar is collapsed.
document.body.append($('quickPatientMenu'),$('linkedPatientMenu'));
function closeLinkedPatientMenu(){
 $('linkedPatientMenu').hidden=true;$('btnQuickPatient').setAttribute('aria-expanded','false');
}
function openLinkedPatientMenu(){
 if(!session?.pet)return;
 if(!$('linkedPatientMenu').hidden){closeLinkedPatientMenu();return;}
 closeQuickPatient();
 const rect=$('btnQuickPatient').getBoundingClientRect(),width=Math.min(250,window.innerWidth-16);
 Object.assign($('linkedPatientMenu').style,{position:'fixed',width:width+'px',left:Math.max(8,Math.min(rect.left,window.innerWidth-width-8))+'px',right:'auto',top:Math.max(8,Math.min(rect.bottom+4,window.innerHeight-115))+'px'});
 $('linkedPatientMenu').hidden=false;$('btnQuickPatient').setAttribute('aria-expanded','true');$('btnLinkedProfile').focus();
}
let patientPrefillState=null;
async function openCompactPatientReview(p,typedName=''){
 if(!canSwitchSession())return;
 if(!session?.transcript?.trim() || candidateForSession()){
  renderCompactPatientReview(p,typedName);return;
 }
 const state={sid:sessionId,text:session.transcript};patientPrefillState=state;patientEditor=null;
 $('petCreateForm').hidden=true;$('petPickerExisting').hidden=true;
 $('petPickerTitle').textContent=p?'关联宠物':'新建宠物';
 $('petPickerStatus').textContent='正在根据逐字稿填写疑似资料… 尚未保存档案。';
 if(!$('petPicker').open)$('petPicker').showModal();
 await extractPatientCandidates();
 if(patientPrefillState!==state || !$('petPicker').open)return;
 if(sessionId!==state.sid || session?.transcript!==state.text || clinicalEdits.size){
  patientPrefillState=null;$('petPickerStatus').textContent='逐字稿已变化，请关闭窗口后重新打开，读取最新资料。';return;
 }
 renderCompactPatientReview(p,typedName);
 if(!candidateForSession()){
  $('petPickerStatus').textContent=patientCandidateError||'未读取到候选资料，可手动填写。';
 }
}
function renderCompactPatientReview(p,typedName=''){
 // Candidate values are editable proposals; the existing signed review endpoint owns persistence.
 startPatientEditor('review',p);
 if(!patientEditor)return;
 const c=patientEditor.candidate,fields=$('patientEditorFields');
 if(!p){if(typedName)$('pf-name').value=typedName;$('petPickerTitle').textContent='新建宠物';}
 else $('petPickerTitle').textContent='关联宠物';
 const details=document.createElement('details');details.id='patientExtraFields';
 const summary=document.createElement('summary');summary.textContent=p?'查看并核对档案资料':'补充宠物资料（选填）';details.append(summary);
 for(const child of [...fields.children]){
  if(!p&&(child.contains($('pf-name'))||child.contains($('pf-species'))))continue;
  details.append(child);
 }
 fields.append(details);
 if(p){
  const preview=document.createElement('div');preview.id='patientCompactPreview';preview.className='patient-compact-preview';
  preview.innerHTML=`<strong>${patientText(p.name||p.record_number)}</strong><span class="hint">${patientText(p.record_number)} · ${patientText(petSpeciesLabel[p.species])}</span>`;
  // Show the background that will become clinical evidence before confirmation.
  preview.innerHTML+=patientHistoryKeys.filter(k=>p.profile?.[k]).map(k=>`<p><span>${patientText(patientLabel(k))}：</span>${patientText(p.profile[k])}</p>`).join('');
  fields.before(preview);
 }
 details.open=!!c;
 $('patientVisitDetails').open=patientObservationKeys.some(k=>c?.data.candidate?.[k]||session?.patient_observations?.[k]);
 $('patientEditorHint').textContent=p?'核对后关联到本次问诊，并带入此档案的既往背景。已有病历将撤销确认并重新核对。':'先填写名称即可建档，其他资料可以稍后补充；名称和资料由你确认后才保存。';
 // Keep candidate provenance and conflict decisions visible whenever there are proposals.
 if(c && Object.keys(c.data.candidate).length===0)$('patientEditorHint').textContent='逐字稿中未找到明确的档案资料，可手动填写并核对后保存。';
 if(c && Object.keys(c.data.candidate).length)$('patientEditorHint').textContent='已根据逐字稿自动填写疑似资料，请对照每项原文核对。未提及的内容保持空白，点击保存后才写入档案。'+(p?' 与已有档案不一致的字段须逐项裁决。':'');
 if(c?.data.warnings?.length)$('patientEditorHint').textContent+=' 部分字段未预填，请手动核对：'+c.data.warnings.map(w=>patientLabel(w.field)+'（'+w.reason+'）').join('；')+'。';
 $('btnCreatePet').textContent=p?'确认关联':'保存并关联';
 if(!p){$('pf-name').required=true;$('pf-name').focus();}else $('patientReviewer').focus();
 renderPatientDecisions();
}
function beginInlinePatientEdit(){
 if(!session?.pet||!canSwitchSession())return;
 startPatientEditor('edit',session.pet);
 if(!patientEditor)return;
 $('petPicker').close();patientInlineEditing=true;
 $('patientInlineFormHost').append($('petCreateForm'),$('petPickerStatus'));
 $('patientWorkspaceProfile').hidden=true;$('patientInlineEditor').hidden=false;$('btnPatientProfileEdit').hidden=true;
 selectPatientArchiveTab('profile');$('pf-name').focus();
}
function finishInlinePatientEdit(){
 $('petPicker').append($('petCreateForm'),$('petPickerStatus'));
 $('petCreateForm').hidden=true;patientEditor=null;patientInlineEditing=false;
 $('patientInlineEditor').hidden=true;$('patientWorkspaceProfile').hidden=false;$('btnPatientProfileEdit').hidden=false;
 $('petPickerStatus').textContent='';$('btnPatientProfileEdit').focus();
}
function openUnlinkPatient(){
 if(!session?.pet||!canSwitchSession())return;
 closeLinkedPatientMenu();closeQuickPatient();
 unlinkPatientState={sid:sessionId,pet_id:session.pet.id,evidence_revision:session.evidence_revision,note_revision:session.note_revision};
 $('unlinkPatientTitle').textContent='解除与「'+(session.pet.name||session.pet.record_number)+'」的关联';
 $('unlinkPatientReviewer').value=$('confirmedBy').value||session.patient_review_by||'';
 $('unlinkPatientAccepted').checked=false;$('unlinkPatientError').textContent='';
 $('unlinkPatientDialog').showModal();$('btnCancelUnlink').focus();
}
function cancelUnlinkPatient(){if(unlinkPatientBusy)return;unlinkPatientState=null;$('unlinkPatientDialog').close();$('btnQuickPatient').focus();}
async function saveUnlinkPatient(event){
 event.preventDefault();const state=unlinkPatientState;if(!state||unlinkPatientBusy)return;
 if(state.sid!==sessionId||clinicalEdits.size){$('unlinkPatientError').textContent='问诊内容已变化，请关闭后重新核对。';return;}
 unlinkPatientBusy=true;petBusy=true;
 $('unlinkPatientForm').querySelectorAll('button,input').forEach(e=>e.disabled=true);
 let saved=false;
 try{
  const r=await petApi(`/api/sessions/${state.sid}/patient-unlink`,'POST',{...state,reviewer:$('unlinkPatientReviewer').value.trim(),reviewed:$('unlinkPatientAccepted').checked});
  if(!r.saved)throw new Error('未解除关联，请重试。');
  saved=true;session=r.session;patientCandidate=null;patientCandidateError='';
  unlinkPatientState=null;$('unlinkPatientDialog').close();render();
  toast('已解除本次关联，档案和问诊内容已保留；请重新核对病历。');
  await loadSessionSidebar();
 }catch(e){if(saved)toast('关联已解除，会话列表刷新失败，请点击刷新。',true);else $('unlinkPatientError').textContent=e.message;}
 finally{unlinkPatientBusy=false;petBusy=false;$('unlinkPatientForm').querySelectorAll('button,input').forEach(e=>e.disabled=false);}
}
$('btnLinkedProfile').onclick=()=>{closeLinkedPatientMenu();openPatientWorkspace();};
$('btnLinkedUnlink').onclick=$('btnPatientUnlink').onclick=openUnlinkPatient;
$('btnClearQuickPatient').onclick=()=>{$('quickPatientSearch').value='';clearTimeout(quickPatientTimer);searchQuickPatient();$('quickPatientSearch').focus();};
$('btnCancelInlinePatient').onclick=()=>{if(!petBusy)finishInlinePatientEdit();};
$('btnCancelUnlink').onclick=cancelUnlinkPatient;$('unlinkPatientForm').onsubmit=saveUnlinkPatient;
$('unlinkPatientDialog').addEventListener('cancel',e=>{if(unlinkPatientBusy)e.preventDefault();else unlinkPatientState=null;});
$('linkedPatientMenu').addEventListener('keydown',e=>{
 if(e.key==='Escape'){e.preventDefault();closeLinkedPatientMenu();$('btnQuickPatient').focus();}
 if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();(e.key==='Home'?$('btnLinkedProfile'):e.key==='End'?$('btnLinkedUnlink'):document.activeElement===$('btnLinkedProfile')?$('btnLinkedUnlink'):$('btnLinkedProfile')).focus();}
});
document.addEventListener('click',e=>{if(!$('linkedPatientMenu').contains(e.target)&&!e.target.closest('#btnQuickPatient'))closeLinkedPatientMenu();});
window.addEventListener('resize',closeLinkedPatientMenu);
$('sessionDetails').addEventListener('scroll',closeLinkedPatientMenu);
$('consultSessionCard').addEventListener('toggle',()=>{if(!$('consultSessionCard').open)closeLinkedPatientMenu();});
window.addEventListener('beforeunload',e=>{if(patientInlineEditing||unlinkPatientBusy){e.preventDefault();e.returnValue='';}});

$('petPicker').addEventListener('cancel',()=>{if(!petBusy)patientPrefillState=null;});
$('btnClosePetPicker').addEventListener('click',()=>{if(!petBusy)patientPrefillState=null;});
