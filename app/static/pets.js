'use strict';
let petBusy=false, petOffset=0, petQuery='', petTicket=0, selectedPet=null;
let petSessionsOffset=0, petUnassigned=false, petSessionsTicket=0, pickerOffset=0, pickerTicket=0, pickerMode='current';
const petSpeciesLabel={dog:'犬',cat:'猫',other:'异宠',unknown:'未填写'};
function petApi(path,method,body){return api(path,{method,headers:{'Content-Type':'application/json','X-Management-Token':document.querySelector('meta[name="management-token"]').content},body:JSON.stringify(body)});}
function petStatus(s){return s.confirmed_at?'已确认':s.has_note?'待确认':({created:'待转写',transcribing:'转写中',transcribed:'已转写',generating:'生成中',failed:'上次未完成'}[s.status]||'未完成');}
function petPages(prefix,offset,data){$(prefix+'Prev').disabled=offset===0;$(prefix+'Next').disabled=!data.has_more;$(prefix+'Page').textContent=data.total?`${offset/20+1} / ${Math.ceil(data.total/20)}`:'0 / 0';}
function renderPetSelection(){
 renderPatientCard();
 $('btnSessionPet').dataset.initial=session?.pet?Array.from(session.pet.name||session.pet.record_number||'宠')[0]:'＋';
 $('btnSessionPet').classList.toggle('has-patient',!!session?.pet);
 $('btnSessionPet').textContent=session?.pet?.name || session?.pet?.record_number || '添加宠物';
 $('btnSessionPet').title=session?.pet?`${session.pet.name} · ${petSpeciesLabel[session.pet.species]} · ${session.pet.record_number} · 打开档案`:'选择或新建宠物档案';
 if(typeof renderPatientWorkspace==='function')renderPatientWorkspace();
}
async function loadPetPage(offset=petOffset){
 const ticket=++petTicket;petOffset=offset;$('petListStatus').textContent='正在读取档案…';$('petPrev').disabled=$('petNext').disabled=true;
 try{
  const r=await petApi('/api/pets/search','POST',{query:petQuery,offset});if(ticket!==petTicket)return;
  $('petListStatus').textContent=r.total?`共 ${r.total} 个档案`:(petQuery?'没有找到匹配的宠物。':'还没有宠物档案。新建后，可将以前的会话手动归入。');
  $('petRows').innerHTML=r.pets.map(p=>`<tr><td><strong>${escapeHtml(p.name || p.record_number)}</strong><small>${escapeHtml(p.record_number || p.id)}</small></td><td data-label="种类">${petSpeciesLabel[p.species]}</td><td data-label="最近问诊">${p.last_visit?escapeHtml(displayTime(p.last_visit,true)):'尚无问诊'}</td><td data-label="会话数量">${p.session_count}</td><td><button class="btn btn-ghost btn-inline" data-pet-open="${p.id}">查看档案</button></td></tr>`).join('');
  $('petRows').querySelectorAll('[data-pet-open]').forEach(b=>b.onclick=()=>openPetDetail(r.pets.find(p=>p.id===b.dataset.petOpen)));
  petPages('pet',offset,r);
 }catch(e){if(ticket===petTicket){$('petRows').textContent='';$('petListStatus').textContent=e.message+' 请重新搜索。';}}
}
function openPetDetail(p){selectedPet=p;petUnassigned=false;$('petDetail').hidden=false;$('petDetailTitle').textContent=(p.name || p.record_number)+' · '+petSpeciesLabel[p.species];$('petDetailId').textContent='档案编号：'+p.record_number;$('petProfileDetail').innerHTML=patientProfileHTML(p);$('petAudit').hidden=true;$('petDetail').scrollIntoView({block:'nearest'});loadPetSessions(0);}
async function loadPetSessions(offset=petSessionsOffset){
 if(!selectedPet)return;const ticket=++petSessionsTicket;petSessionsOffset=offset;
 $('petSessionsStatus').textContent='正在读取会话…';$('petSessionsPrev').disabled=$('petSessionsNext').disabled=true;
 $('btnPetSessions').setAttribute('aria-pressed',String(!petUnassigned));$('btnUnassignedSessions').setAttribute('aria-pressed',String(petUnassigned));
 try{
  const r=await api('/api/history?scope=all&pet_id='+encodeURIComponent(petUnassigned?'unassigned':selectedPet.id)+'&offset='+offset);if(ticket!==petSessionsTicket)return;
  $('petSessionsStatus').textContent=(petUnassigned?'未关联档案的历史会话':'此宠物的全部会话')+` · ${r.total} 次`;
  $('petSessions').innerHTML=r.sessions.map(s=>`<div class="history-row"><div><strong>${escapeHtml(displayTime(s.created_at,true))} · ${petStatus(s)}</strong><p class="hint">${escapeHtml(s.preview || (s.audio_name?'已保存录音':'尚未生成病历'))}</p></div><div class="controls"><button class="btn btn-ghost btn-inline" data-pet-session="${s.id}">打开会话</button>${petUnassigned?`<button class="btn btn-ghost btn-inline" data-pet-assign="${s.id}">归入此档案</button>`:''}</div></div>`).join('') || '<p class="hint">暂无会话。</p>';
  $('petSessions').querySelectorAll('[data-pet-session]').forEach(b=>b.onclick=()=>openSession(b.dataset.petSession));
  $('petSessions').querySelectorAll('[data-pet-assign]').forEach(b=>b.onclick=()=>assignFromList(b.dataset.petAssign,selectedPet));
  petPages('petSessions',offset,r);
 }catch(e){if(ticket===petSessionsTicket){$('petSessions').textContent='';$('petSessionsStatus').textContent=e.message;}}
}

let patientOptions={species:petSpeciesLabel,breeds:{},sex_neuter:{unknown:'未填写'},labels:{}}, patientOptionsReady=false;
let patientCandidate=null, patientCandidateBusy=false, patientCandidateError='', patientCandidateTicket=0;
let patientEditor=null;
const patientProfileKeys=['name','species','breed','sex_neuter','medications','diagnoses','allergies','other'];
const patientObservationKeys=['age','weight','immunization','deworming'];
const patientHistoryKeys=['medications','diagnoses','allergies','other'];
const patientText=v=>escapeHtml(String(v??''));
function patientLabel(k){return patientOptions.labels[k] || ({name:'宠物名',species:'物种',breed:'品种',sex_neuter:'性别/绝育'}[k]) || k;}
function patientValue(k,v){return k==='species'?petSpeciesLabel[v]||v:k==='sex_neuter'?patientOptions.sex_neuter[v]||v:v;}
function candidateForSession(){
 const text=session?.transcript||'';
 if(patientCandidate?.sid===sessionId && patientCandidate.transcript===text)return patientCandidate;
 // The server only exposes a saved proposal when its transcript hash still
 // matches, so a refresh can safely rehydrate the review without another call.
 if(sessionId&&text&&session?.patient_candidate){
  patientCandidate={sid:sessionId,transcript:text,data:{ok:true,saved:false,cached:true,...session.patient_candidate}};
  return patientCandidate;
 }
 return null;
}
function patientKnown(k,v){return !!v && (!['species','sex_neuter'].includes(k) || v!=='unknown');}
function patientProfileHTML(p){
 const f=p.profile||{};let html=patientProfileKeys.filter(k=>patientKnown(k,f[k])).map(k=>`<div class="patient-fact"><span>${patientText(patientLabel(k))}</span><p>${patientText(patientValue(k,f[k]))}</p></div>`).join('');
 for(const [key,entry] of Object.entries(p.recent_observations||{}))html+=`<div class="patient-fact"><span>${patientText(patientLabel(key))} · ${patientText(displayTime(entry.observed_at,true))}报告</span><p>${patientText(entry.value)}</p></div>`;
 if(f.aliases?.length)html+=`<div class="patient-fact"><span>已确认别名</span><p>${patientText(f.aliases.join('、'))}</p></div>`;
 html+=`<p class="hint">上次就诊：${p.last_visit?patientText(displayTime(p.last_visit,true)):'暂无记录'}</p>`;return html;
}
function renderPatientCard(){
 const card=$('patientCard');if(!card)return;
 const pending=!session?.pet && !!session?.transcript?.trim();
 card.classList.toggle('is-pending-patient',pending);
 $('patientReviewDetails').hidden=pending;
 if(pending){
  const anchor=$('btnQuickPatient').parentElement;
  if(card.previousElementSibling!==anchor)anchor.after(card);
 }else if(card.parentElement!==$('patientReviewDetails'))$('patientReviewDetails').append(card);
 card.hidden=!session?.transcript && !session?.pet;
 if(card.hidden)return;
 const p=session?.pet,c=candidateForSession();
 const conflicts=p&&c?patientProfileKeys.filter(k=>patientKnown(k,p.profile?.[k])&&patientKnown(k,c.data.candidate[k]?.value)&&p.profile[k]!==c.data.candidate[k].value):[];
 $('btnPatientReview').hidden=!p;$('btnPatientMatch').textContent=p?'更换关联档案':'匹配到已有档案';
 $('btnPatientExtract').disabled=patientCandidateBusy || generationBusy || !session?.transcript || clinicalEdits.size>0;
 $('btnPatientExtract').textContent=patientCandidateBusy?'正在读取…':c?'重新读取疑似资料':'读取疑似资料';
 let state=p?`${p.record_number} · 已匹配`:'未绑定 · 疑似资料未保存';
 if(pending && patientCandidateBusy)state='正在从逐字稿提取资料…';
 if(pending && c)state='已提取疑似资料 · 请核对后关联或建档';
 if(pending && patientCandidateError)state='资料读取失败 · 尚未创建档案';
 if(conflicts.length)state='存在疑似差异 · '+conflicts.map(patientLabel).join('、');
 if(session?.patient_review_needed)state='待裁决 · 逐字稿已变化，请重新核对';
 if(p && !session?.patient_context?.pet_id)state+=' · 尚未带入档案背景';
 const stale=p && session?.patient_context?.pet_id && (p.id!==session.patient_context.pet_id || p.profile_revision!==session.patient_context.profile_revision);
 if(stale)state+=' · 档案已更新，本次仍使用原快照';
 $('patientCardState').textContent=state;
 let html=p?`<strong>${patientText(p.name||p.record_number)}</strong>`+patientProfileHTML(p):'';
 if(session?.patient_context?.pet_id){const snap=session.patient_context;html+=`<details><summary>本次已带入的既往背景（快照）</summary><p class="hint">由 ${patientText(snap.reviewer||'兽医')} 于 ${patientText(displayTime(snap.reviewed_at,true))} 核对</p><p class="patient-snapshot">${patientText(Object.entries(snap.summary||{}).filter(([,v])=>v).map(([k,v])=>`${patientLabel(k)}：${v}`).join('\n')||'本次没有带入既往病史。')}</p></details>`;}
 if(c){
  const candidates=c.data.candidate;
  // Identity must stay visible regardless of JSON key order or the number of history fields.
  const orderedKeys=['name','species','breed','sex_neuter','age','weight','allergies','medications','diagnoses','immunization','deworming','other'];
  const entries=orderedKeys.filter(k=>candidates[k]).map(k=>[k,candidates[k]]);
  const identity=['species','breed','sex_neuter','age','weight'].filter(k=>patientKnown(k,candidates[k]?.value)).map(k=>patientValue(k,candidates[k].value));
  html+=`<div class="patient-candidate-identity"><strong>${patientText(candidates.name?.value||'尚未提取到宠物名')}</strong><span class="hint">疑似资料 · 未保存</span><p>${patientText(identity.join(' · ')||'其他基本资料暂未提取到')}</p><p class="hint">核对后，可关联已有档案或创建新档案。</p></div>`;
  html+=`<details><summary>查看提取资料与原文（${entries.length} 项）</summary>`+(entries.length?entries.map(([k,v])=>`<div class="patient-fact"><span>${patientText(patientLabel(k))} · 疑似</span><p>${patientText(patientValue(k,v.value))}</p><small>原文：${patientText(v.source)}</small></div>`).join(''):'<p class="hint">没有明确候选，可手动核对后建档。</p>')+'</details>';
 }
 if(c?.data.warnings?.length)html+=`<p class="hint hint-warn">部分字段未预填，请手动核对：${patientText(c.data.warnings.map(w=>patientLabel(w.field)+'（'+w.reason+'）').join('；'))}。已通过校验的候选仍需你确认。</p>`;
 if(patientCandidateError)html+=`<p class="hint hint-warn">${patientText(patientCandidateError)}</p>`;
 if(!p&&!c&&!patientCandidateBusy&&!patientCandidateError)html+='<p class="hint">将根据已保存的逐字稿自动提取资料。</p>';
 if(pending){$('btnPatientMatch').textContent='关联已有档案';$('btnPatientCreate').textContent='核对并新建档案';}
 $('btnPatientMatch').disabled=$('btnPatientCreate').disabled=patientCandidateBusy||generationBusy||clinicalEdits.size>0;
 $('patientCardBody').innerHTML=html;
 if(typeof renderSessionSummary==='function')renderSessionSummary();
}
let patientCandidateRequest=null,patientAutoTimer=null;
const patientAutoAttempts=new Map();
function scheduleAutomaticPatientCandidates(){
 clearTimeout(patientAutoTimer);
 const sid=sessionId,text=session?.transcript||'';
 const needsProposal=!session?.pet||session?.patient_review_needed;
 if(!sid||!text.trim()||!needsProposal||candidateForSession()||patientCandidateBusy||generationBusy||petBusy||clinicalEdits.size||['transcribing','generating'].includes(session?.status)||patientAutoAttempts.get(sid)===text)return;
 patientAutoTimer=setTimeout(()=>{
  if(sessionId!==sid||session?.transcript!==text||(!session?.patient_review_needed&&session?.pet)||patientCandidateBusy||generationBusy||petBusy||clinicalEdits.size||$('transcript').value!==text||patientAutoAttempts.get(sid)===text)return;
  extractPatientCandidates();
 },150);
}

function extractPatientCandidates(refresh=false){
 if(patientCandidateBusy)return patientCandidateRequest;
 if(!sessionId || !session?.transcript || clinicalEdits.size)return Promise.resolve();
 const sid=sessionId,text=session.transcript,ticket=++patientCandidateTicket;
 patientAutoAttempts.set(sid,text);if(patientAutoAttempts.size>50)patientAutoAttempts.delete(patientAutoAttempts.keys().next().value);
 patientCandidateBusy=true;patientCandidateError='';renderPatientCard();
 patientCandidateRequest=(async()=>{
  try{
   const r=await petApi(`/api/sessions/${sid}/patient-candidates`,'POST',{refresh});
   if(sid!==sessionId || text!==session?.transcript || ticket!==patientCandidateTicket)return;
   patientCandidate={sid,transcript:text,data:r};
  }catch(e){if(sid===sessionId && text===session?.transcript)patientCandidateError=e.message;}
  finally{patientCandidateBusy=false;renderPatientCard();}
 })();
 return patientCandidateRequest;
}
function pickerBusy(value){petBusy=value;document.querySelectorAll('#petPicker button,#petPicker input,#petPicker select,#petPicker textarea,#patientInlineEditor button,#patientInlineEditor input,#patientInlineEditor select,#patientInlineEditor textarea').forEach(x=>x.disabled=value);}
function openPetPicker(mode='current'){
 if(typeof patientPrefillState!=='undefined')patientPrefillState=null;
 if(!canSwitchSession())return;
 pickerMode=mode;patientEditor=null;$('petPickerStatus').textContent='';$('petCreateForm').hidden=true;$('petPickerExisting').hidden=false;$('petPickerSearch').value=mode==='current'?(candidateForSession()?.data.candidate.name?.value||''):'';
 $('petPickerTitle').textContent=mode==='merge'?'选择要保留的档案':'匹配到已有档案';
 if(!$('petPicker').open)$('petPicker').showModal();
 if(mode==='create'){startPatientEditor('create',null);return;}
 if(mode==='new-current'){openCompactPatientReview(null);return;}
 loadPicker(0);
}
async function loadPicker(offset=pickerOffset){
 const ticket=++pickerTicket;pickerOffset=offset;$('petPickerResults').textContent='正在搜索…';$('petPickerPrev').disabled=$('petPickerNext').disabled=true;
 try{
  const r=await petApi('/api/pets/search','POST',{query:$('petPickerSearch').value,offset});if(ticket!==pickerTicket)return;
  $('petPickerResults').innerHTML=r.pets.filter(p=>pickerMode!=='merge'||p.id!==selectedPet?.id).map(p=>`<div class="pet-picker-row"><div><strong>${patientText(p.name||p.record_number)} · ${patientText(petSpeciesLabel[p.species])}</strong><div class="hint">${patientText(p.record_number)} · ${p.session_count} 次会话</div></div><button class="btn btn-ghost btn-inline" data-pet-choose="${p.id}">选择并核对</button></div>`).join('')||'<p class="hint">没有匹配档案。</p>';
  $('petPickerResults').querySelectorAll('[data-pet-choose]').forEach(b=>{b.disabled=petBusy;b.onclick=()=>choosePet(r.pets.find(p=>p.id===b.dataset.petChoose));});
  petPages('petPicker',offset,r);
 }catch(e){if(ticket===pickerTicket)$('petPickerResults').textContent=e.message;}
}
function choosePet(p){if(pickerMode==='merge')startPatientEditor('merge',p,selectedPet);else openCompactPatientReview(p);}
function patientSelect(key,value){const opts=key==='species'?patientOptions.species:patientOptions.sex_neuter;return `<select id="pf-${key}" class="input">${Object.entries(opts).map(([v,label])=>`<option value="${v}" ${v===value?'selected':''}>${patientText(label)}</option>`).join('')}</select>`;}
function startPatientEditor(mode,target,source=null,manual=false){
 if(typeof patientPrefillState!=='undefined')patientPrefillState=null;
 if(!patientOptionsReady){toast('档案选项尚未读取完成，请稍后重试或刷新页面。',true);return;}
 const c=mode==='review'&&!manual?candidateForSession():null;
 const proposed=source?.profile || Object.fromEntries(Object.entries(c?.data.candidate||{}).map(([k,v])=>[k,v.value]));
 const base=target?.profile||{};const values={...base};
 if(mode!=='edit')for(const k of patientProfileKeys){if(patientKnown(k,proposed[k]))values[k]=proposed[k];}
 patientEditor={mode,target,source,candidate:c,proposed,base,decisions:{},sid:sessionId,evidenceRevision:session?.evidence_revision,noteRevision:session?.note_revision};
 $('patientCompactPreview')?.remove();
 $('petPickerExisting').hidden=true;$('petCreateForm').hidden=false;
 $('petPickerTitle').textContent=({create:'新建宠物档案',review:target?'核对本次就诊对象':'核对并新建档案',edit:'纠正档案',merge:'合并重复档案'})[mode];
 $('patientEditorHint').textContent=mode==='merge'?`把 ${source.name||source.record_number}（${source.record_number}）合并到 ${target.name||target.record_number}（${target.record_number}）。原编号与历史病历保留。`:mode==='edit'?'修改不会自动覆盖以前病历或本次已带入的快照。空白表示未记录，不代表无异常。':`资料可暂缺。${target?'保留编号 '+target.record_number:'确认后自动分配 Z 编号'}。所有语音候选均为疑似，请对照原文核对，特别注意姓名及绝育状态。`;
 $('patientEditorFields').innerHTML=patientProfileKeys.map(k=>{
  const val=values[k]||(['species','sex_neuter'].includes(k)?'unknown':'');const cand=c?.data.candidate?.[k];
  const control=['species','sex_neuter'].includes(k)?patientSelect(k,val):patientHistoryKeys.includes(k)?`<textarea id="pf-${k}" class="input" maxlength="6000" rows="3">${patientText(val)}</textarea>`:`<input id="pf-${k}" class="input" maxlength="80" value="${patientText(val)}" ${k==='breed'?'list="patientBreeds"':''}>`;
  return `<div class="patient-editor-field"><label for="pf-${k}">${patientText(patientLabel(k))}${cand?' · 疑似，请核对':''}</label>${control}${cand?`<small>原文：${patientText(cand.source)}</small>`:''}<div data-patient-decision="${k}"></div></div>`;
 }).join('')+`<datalist id="patientBreeds"></datalist><label for="pf-aliases">已确认别名（用逗号分隔）</label><input id="pf-aliases" class="input" value="${patientText((values.aliases||[]).join('，'))}" placeholder="仅填写确认属于同一只动物的别名">`;
 $('patientVisitDetails').hidden=mode!=='review';$('patientVisitDetails').open=false;
 $('patientObservationFields').hidden=mode!=='review';
 $('patientObservationFields').innerHTML=mode==='review'?'<h3>本次就诊记录</h3><p class="hint">年龄按本次报告保存，不推算生日。免疫和驱虫请保留日期及否定表述。</p>'+patientObservationKeys.map(k=>`<label for="po-${k}">${patientText(patientLabel(k))}${proposed[k]?' · 疑似':''}</label><textarea id="po-${k}" class="input" rows="2" maxlength="2000">${patientText(proposed[k]||session?.patient_observations?.[k]||'')}</textarea>${c?.data.candidate?.[k]?`<small>原文：${patientText(c.data.candidate[k].source)}</small>`:''}`).join(''):'';
 $('patientObservationFields').insertAdjacentHTML('beforeend',mode==='review'&&!session?.history_summary_stale&&Object.values(session?.history_summary||{}).some(Boolean)?'<button type="button" id="btnAdoptHistory" class="btn btn-ghost">将本次已编辑的既往摘要填入档案（仍需确认）</button>':'');
 if($('btnAdoptHistory'))$('btnAdoptHistory').onclick=()=>{for(const k of patientHistoryKeys){if(session.history_summary[k])$('pf-'+k).value=session.history_summary[k];}$('patientAccepted').checked=false;renderPatientDecisions();};
 $('patientReviewer').value=$('confirmedBy').value || session?.patient_review_by || ''; $('patientAccepted').checked=false;$('petPickerStatus').textContent='';
 $('btnCreatePet').textContent=mode==='merge'?'确认差异并合并':mode==='review'?(target?'确认对象并带入背景':'确认建档并带入背景'):'确认保存';
 $('pf-species').onchange=()=>{$('pf-breed').value='';updatePatientBreeds();renderPatientDecisions();};
 document.querySelectorAll('#patientEditorFields input,#patientEditorFields textarea,#patientEditorFields select,#patientObservationFields textarea').forEach(el=>el.addEventListener('input',()=>{$('patientAccepted').checked=false;renderPatientDecisions();}));
 updatePatientBreeds();renderPatientDecisions();
 if(!$('petPicker').open)$('petPicker').showModal();
}
function updatePatientBreeds(){$('patientBreeds').innerHTML=(patientOptions.breeds[$('pf-species').value]||[]).map(v=>`<option value="${patientText(v)}"></option>`).join('');$('pf-breed').disabled=$('pf-species').value==='unknown';}
function readPatientProfile(){const f=Object.fromEntries(patientProfileKeys.map(k=>[k,$('pf-'+k).value.trim()]));f.aliases=$('pf-aliases').value.split(/[,，]/).map(s=>s.trim()).filter(Boolean);return f;}
function renderPatientDecisions(){
 const e=patientEditor;if(!e)return;const values=readPatientProfile();let count=0;
 for(const k of patientProfileKeys){
  const node=document.querySelector(`[data-patient-decision="${k}"]`);const old=e.base[k];const proposed=e.proposed[k];
  const conflict=patientKnown(k,old)&&((patientKnown(k,proposed)&&proposed!==old)||(patientKnown(k,values[k])&&values[k]!==old));
  if(!conflict){node.innerHTML='';delete e.decisions[k];continue;}
  count++;if(node.querySelector('select'))continue;
  node.innerHTML=`<div class="patient-conflict"><p>档案：${patientText(patientValue(k,old))}<br>${e.mode==='merge'?'待合并档案':'本次候选 / 修改'}：${patientText(patientValue(k,proposed||values[k]))}</p><label>裁决<select class="input"><option value="">请选择处理方式</option><option value="keep">保留原档案值</option><option value="replace">采用上方已核对的值（可编辑）</option><option value="unknown">设为未知 / 清空</option></select></label></div>`;
  node.querySelector('select').onchange=ev=>{e.decisions[k]=ev.target.value;$('patientAccepted').checked=false;};
 }
 $('patientDecisions').textContent=count?`有 ${count} 项差异，须逐项裁决后保存。`:'';
}
async function savePatientEditor(event){
 const inline=typeof patientInlineEditing!=='undefined' && patientInlineEditing;
 event.preventDefault();const e=patientEditor;if(!e||petBusy)return;
 const body={profile:readPatientProfile(),reviewer:$('patientReviewer').value.trim(),reviewed:$('patientAccepted').checked,decisions:e.decisions};
 if(e.mode==='review' && (e.sid!==sessionId || clinicalEdits.size)){ $('petPickerStatus').textContent='会话或文字已变化，请关闭窗口重新核对。';return; }
 if(e.mode==='review')body.observations=Object.fromEntries(patientObservationKeys.map(k=>[k,$('po-'+k).value.trim()]));
 pickerBusy(true);let saved=false;
 try{
  let r;
  if(e.mode==='review'){
   const sid=await ensureSession();Object.assign(body,{pet_id:e.target?.id,create_new:!e.target,profile_revision:e.target?.profile_revision,
    evidence_revision:e.evidenceRevision??session.evidence_revision,note_revision:e.noteRevision??session.note_revision,
    candidate_token:e.candidate?.data.token,manual_review:!e.candidate});
   r=await petApi(`/api/sessions/${sid}/patient-review`,'POST',body);
  }else if(e.mode==='create')r=await petApi('/api/patient-records','POST',body);
  else if(e.mode==='edit')r=await petApi(`/api/patient-records/${e.target.id}`,'PUT',{...body,profile_revision:e.target.profile_revision});
  else r=await petApi(`/api/patient-records/${e.source.id}/merge`,'POST',{...body,target_id:e.target.id,source_revision:e.source.profile_revision,target_revision:e.target.profile_revision});
  if(r.saved!==true)throw new Error('资料未保存，请重试。');
  saved=true;$('petPicker').close();patientEditor=null;if(inline)finishInlinePatientEdit();
  if(e.mode==='review'){session=r.session;patientCandidate=null;patientCandidateError='';render();toast('对象与背景已确认保存；已有病历需重新核对、确认。');await loadSessionSidebar();}
  else{toast('档案已保存。');await loadPetPage(0);if(r.pet&&!inline)openPetDetail(r.pet);if(sessionId){const fresh=await api(`/api/sessions/${sessionId}`);session=fresh.session;render();await loadSessionSidebar();}}
 }catch(err){if(saved)toast('资料已保存，页面刷新失败，请刷新后查看。',true);else $('petPickerStatus').textContent=err.message;}
 finally{pickerBusy(false);if(patientEditor)updatePatientBreeds();}
}
async function assignFromList(sid,p){if(!canSwitchSession())return;await openSession(sid);if(sessionId===sid)startPatientEditor('review',p);}
async function startConsultForPet(p){
 if(!p||!canSwitchSession())return;
 resetSession();showView('scribe');
 try{await ensureSession();openCompactPatientReview(p);}catch(e){showError(e);}
}
async function newPetConsult(){await startConsultForPet(selectedPet);}
function describePatientAudit(raw){
 try{
  const data=JSON.parse(raw);let f=data.profile||data;
  if(data.source&&data.target)return `合并前：${data.source.name||'未命名'}（${data.source.record_number}）→ ${data.target.name||'未命名'}（${data.target.record_number}）`;
  if(f.profile_json)f={...JSON.parse(f.profile_json),name:f.name,species:f.species};
  return [...patientProfileKeys,...patientObservationKeys].filter(k=>f[k]).map(k=>`${patientLabel(k)}：${patientValue(k,f[k])}`).join('\n') || '尚未记录';
 }catch{return '此条为旧版记录。';}
}
async function showPatientAudit(){
 if(!selectedPet)return;
 try{const r=await api(`/api/patient-records/${selectedPet.id}/audit`);const labels={create:'创建档案',correct:'纠正档案','review-correct':'问诊核对后更新','bind-context':'确认就诊背景','visit-observations':'保存本次观察',merge:'合并档案','merge-profile':'合并后资料'};
 $('petAudit').innerHTML=r.changes.map(a=>`<details><summary>${patientText(displayTime(a.created_at,true))} · ${patientText(a.reviewer)} · ${patientText(labels[a.action]||a.action)}</summary><p class="hint">修改前</p><pre>${patientText(describePatientAudit(a.before_json))}</pre><p class="hint">修改后</p><pre>${patientText(describePatientAudit(a.after_json))}</pre></details>`).join('')||'<p class="hint">旧档案尚无变更记录。</p>';$('petAudit').hidden=false;
 }catch(e){toast(e.message,true);}
}
$('btnSessionPet').onclick=()=>{if(session?.pet && canSwitchSession())startPatientEditor('review',session.pet);else openPetPicker();};
$('btnNewPet').onclick=()=>openPetPicker('create');$('btnClosePetPicker').onclick=()=>{if(!petBusy){$('petPicker').close();patientEditor=null;}};
$('petPicker').addEventListener('cancel',e=>{if(petBusy)e.preventDefault();else patientEditor=null;});
$('petSearchForm').onsubmit=e=>{e.preventDefault();petQuery=$('petSearch').value;loadPetPage(0);};
$('btnClearPetSearch').onclick=()=>{$('petSearch').value='';petQuery='';loadPetPage(0);};
$('petPrev').onclick=()=>loadPetPage(Math.max(0,petOffset-20));$('petNext').onclick=()=>loadPetPage(petOffset+20);
$('petPickerSearchForm').onsubmit=e=>{e.preventDefault();loadPicker(0);};$('petPickerPrev').onclick=()=>loadPicker(Math.max(0,pickerOffset-20));$('petPickerNext').onclick=()=>loadPicker(pickerOffset+20);
$('petCreateForm').onsubmit=savePatientEditor;
$('btnPetSessions').onclick=()=>{petUnassigned=false;loadPetSessions(0);};$('btnUnassignedSessions').onclick=()=>{petUnassigned=true;loadPetSessions(0);};
$('petSessionsPrev').onclick=()=>loadPetSessions(Math.max(0,petSessionsOffset-20));$('petSessionsNext').onclick=()=>loadPetSessions(petSessionsOffset+20);
$('btnClosePetDetail').onclick=()=>{$('petDetail').hidden=true;};$('btnPetConsult').onclick=newPetConsult;
$('btnPatientExtract').onclick=()=>extractPatientCandidates(true);$('btnPatientMatch').onclick=()=>openPetPicker();$('btnPatientCreate').onclick=()=>openPetPicker('new-current');
$('btnPatientReview').onclick=()=>{if(canSwitchSession())startPatientEditor('review',session.pet);};
$('btnEditPet').onclick=()=>{if(selectedPet && canSwitchSession())startPatientEditor('edit',selectedPet);};$('btnMergePet').onclick=()=>{if(selectedPet)openPetPicker('merge');};$('btnPetAudit').onclick=showPatientAudit;
api('/api/patient-options').then(r=>{patientOptions=r;patientOptionsReady=true;renderPetSelection();}).catch(e=>{patientCandidateError='档案选项读取失败，请刷新页面：'+e.message;renderPatientCard();});
renderPetSelection();
