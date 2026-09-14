/* Archive reads have their own state; they never replace the active consultation. */
'use strict';
let patientWorkspaceKey = '', patientWorkspaceRevision = '', patientVisitsOffset = 0;
let patientVisitsTicket = 0, patientArchiveTab = 'visits', patientReturnTab = 'transcript';
function renderPatientWorkspace() {
  const pet = session?.pet;
  const key = pet ? `${sessionId}:${pet.id}` : '';
  if (key !== patientWorkspaceKey) {
    patientWorkspaceKey = key;
    patientVisitsTicket++;
    patientVisitsOffset = 0;
    patientWorkspaceRevision = '';
    $('patientVisitsList').replaceChildren();
    selectPatientArchiveTab('visits');
    // A switch of consultation must not leave the previous animal visible.
    if (documentTab === 'patient') selectDocumentTab('transcript');
  }
  if (!pet) return;
  $('patientWorkspaceName').textContent = pet.name || pet.record_number;
  $('patientWorkspaceNumber').textContent = `${pet.record_number} · ${petSpeciesLabel[pet.species] || '未填写物种'}`;
  $('patientWorkspaceProfile').innerHTML = patientProfileHTML(pet);
  const revision = JSON.stringify([pet.profile_revision, session.note_revision, session.summary_revision, session.confirmed, session.updated_at]);
  if (documentTab === 'patient' && revision !== patientWorkspaceRevision) loadPatientVisits(patientVisitsOffset);
  patientWorkspaceRevision = revision;
}
function openPatientWorkspace() {
  if (!session?.pet) return;
  closeQuickPatient();
  if (documentTab !== 'patient') patientReturnTab = documentTab;
  renderPatientWorkspace();
  selectDocumentTab('patient');
  loadPatientVisits(0);
}
function returnToPatientConsultation() {
  selectDocumentTab(patientReturnTab === 'soap' && !session?.note ? 'transcript' : patientReturnTab, true);
}
function selectPatientArchiveTab(name, focus = false) {
  patientArchiveTab = name;
  for (const tab of ['visits', 'profile']) {
    const selected = name === tab, button = $('patient-tab-' + tab);
    button.classList.toggle('is-active', selected);
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1;
    $('patient-' + tab).hidden = !selected;
  }
  if (focus) $('patient-tab-' + name).focus();
}
function savedPatientNoteHTML(note) {
  if (!note) return '<p class="hint">此次问诊尚未生成病历。</p>';
  const fields = hasSubjectiveSections(note) ? [...SUBJECTIVE_FIELDS, ...FIELDS.slice(1)] : FIELDS;
  return fields.map(([key, label]) => `<div class="patient-saved-field"><strong>${patientText(label)}</strong><p>${patientText(noteFieldValue(note, key) || '未提及')}</p></div>`).join('');
}
async function loadPatientVisits(offset = patientVisitsOffset) {
  const petId = session?.pet?.id, currentId = sessionId;
  if (!petId) return;
  const ticket = ++patientVisitsTicket;
  patientVisitsOffset = offset;
  $('patientVisitsStatus').textContent = '正在读取问诊记录…';
  $('patientVisitsList').replaceChildren();
  $('patientVisitsPrev').disabled = $('patientVisitsNext').disabled = true;
  $('patientVisitsPage').textContent = '';
  try {
    const r = await api('/api/history?scope=all&pet_id=' + encodeURIComponent(petId) + '&offset=' + offset);
    if (ticket !== patientVisitsTicket || sessionId !== currentId || session?.pet?.id !== petId) return;
    $('patientVisitsStatus').textContent = `此宠物的问诊记录 · ${r.total} 次`;
    for (const item of r.sessions) {
      const current = item.id === currentId;
      const card = document.createElement('details');
      card.className = 'patient-visit-card';
      const summary = document.createElement('summary');
      summary.innerHTML = `<span>${patientText(displayTime(item.created_at, true))} <span class="count">${current ? '本次问诊' : patientText(petStatus(item))}</span><strong>${patientText(item.preview || '尚无病历摘要')}</strong></span>`;
      card.append(summary);
      const body = document.createElement('div');
      body.className = 'patient-visit-body';
      card.append(body);
      const button = document.createElement('button');
      button.className = 'btn btn-ghost btn-inline';
      button.type = 'button';
      button.textContent = current ? '返回本次问诊' : '打开此问诊';
      button.onclick = () => current ? returnToPatientConsultation() : openSession(item.id);
      const preview = document.createElement('div');
      body.append(preview, button);
      let loaded = false, loading = false;
      card.addEventListener('toggle', async () => {
        if (!card.open || loaded || loading) return;
        if (current) {
          preview.textContent = '本次问诊的内容保留在逐字稿和 SOAP 病历中，可返回继续编辑。';
          loaded = true;
          return;
        }
        loading = true;
        preview.textContent = '正在读取已保存病历…';
        try {
          const detail = await api('/api/sessions/' + encodeURIComponent(item.id));
          if (ticket !== patientVisitsTicket || !card.isConnected) return;
          // An association may have changed since the timeline was fetched.
          if (detail.session.pet?.id !== petId) throw new Error('此问诊的关联已变化，请刷新记录。');
          preview.innerHTML = '<p class="hint">已保存版本 · 只读预览。复制、导出请打开问诊并完成兽医确认。</p>' + savedPatientNoteHTML(detail.session.note);
          loaded = true;
        } catch (e) {
          if (ticket === patientVisitsTicket && card.isConnected) preview.textContent = e.message + ' 收起后重新展开可重试。';
        } finally { loading = false; }
      });
      $('patientVisitsList').append(card);
    }
    if (!r.sessions.length) $('patientVisitsList').textContent = '暂无问诊记录。';
    petPages('patientVisits', offset, r);
  } catch (e) {
    if (ticket === patientVisitsTicket) $('patientVisitsStatus').textContent = e.message + ' 请点击刷新记录重试。';
  }
}
for (const tab of ['visits', 'profile']) {
  const button = $('patient-tab-' + tab);
  button.onclick = () => selectPatientArchiveTab(tab);
  button.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    selectPatientArchiveTab(event.key === 'Home' ? 'visits' : event.key === 'End' ? 'profile' : patientArchiveTab === 'visits' ? 'profile' : 'visits', true);
  });
}
$('btnPatientReturn').onclick = returnToPatientConsultation;
$('btnPatientNewConsult').onclick = () => startConsultForPet(session?.pet);
$('btnPatientProfileEdit').onclick = () => beginInlinePatientEdit();
$('btnPatientVisitsRefresh').onclick = () => loadPatientVisits(0);
$('patientVisitsPrev').onclick = () => loadPatientVisits(Math.max(0, patientVisitsOffset - 20));
$('patientVisitsNext').onclick = () => loadPatientVisits(patientVisitsOffset + 20);
$('btnSessionPet').setAttribute('aria-controls', 'panel-patient');
renderPatientWorkspace();
