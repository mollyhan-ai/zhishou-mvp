import json
from unittest import mock
from tests.base import AppTestCase
from app import db
from app.config import get_config

NOTE=dict(subjective='本次主诉。\n历史背景（既往病史）。',objective='未提及',assessment='未提及',plan='观察',uncertain=[])

class SessionPanelTests(AppTestCase):
    def note_session(self, created=1, generated=2, confirmed=False):
        sid=self.new_session()
        db.update_session(sid,created_at=created,note_generated_at=generated,note_json=json.dumps(NOTE),
                          confirmed_at=3 if confirmed else None,confirmed_by='测试医生' if confirmed else None)
        return sid

    def test_notes_list_filters_and_sorts_by_generation(self):
        blank=self.new_session()
        earlier=self.note_session(created=200,generated=100)
        later=self.note_session(created=100,generated=200)
        data=self.client.get('/api/history?scope=notes').json
        self.assertEqual([row['id'] for row in data['sessions']],[later,earlier])
        self.assertEqual(data['total'],2)
        row=data['sessions'][0]
        self.assertEqual(row['display_name'],'未命名会话')
        self.assertIn('历史背景（既往病史）',row['preview'])
        self.assertNotIn('note_json',row)
        self.assertNotIn(blank,[r['id'] for r in data['sessions']])
        self.assertEqual(self.client.get('/api/history').json['total'],3)

    def test_list_preview_bounded_and_no_extra_clinical_data(self):
        sid=self.note_session()
        db.update_session(sid,note_json=json.dumps(dict(NOTE,subjective='x '*200,plan='PRIVATE PLAN')),transcript='PRIVATE TRANSCRIPT')
        response=self.client.get('/api/history?scope=notes')
        self.assertEqual(len(response.json['sessions'][0]['preview']),80)
        self.assertNotIn('PRIVATE',response.get_data(as_text=True))
        self.assertEqual(self.client.get('/api/history?scope=invalid').status_code,400)

    def test_notes_pagination(self):
        for i in range(23): self.note_session(created=i,generated=i)
        first=self.client.get('/api/history?scope=notes').json
        second=self.client.get('/api/history?scope=notes&offset=20').json
        self.assertEqual(len(first['sessions']),20)
        self.assertEqual(len(second['sessions']),3)
        self.assertTrue(first['has_more']);self.assertFalse(second['has_more'])
        self.assertFalse({row['id'] for row in first['sessions']} & {row['id'] for row in second['sessions']})

    def test_field_copy_requires_confirmation_and_keeps_history_marker(self):
        sid=self.note_session();url='/api/sessions/'+sid+'/export'
        for field in ('subjective','objective','assessment','plan'):
            self.assertEqual(self.client.get(url+'?field='+field).status_code,409)
        self.client.post('/api/sessions/'+sid+'/confirm',json={'confirmed_by':'测试医生'})
        for field in ('subjective','objective','assessment','plan'):
            self.assertEqual(self.client.get(url+'?field='+field).json['text'],NOTE[field])
        self.assertIn('历史背景（既往病史）',self.client.get(url).json['text'])
        self.assertEqual(self.client.get(url+'?field=bad').status_code,400)
        self.client.put('/api/sessions/'+sid+'/note',json={'note':NOTE})
        self.assertEqual(self.client.get(url+'?field=subjective').status_code,409)

    def test_reading_history_details_and_copy_preserves_all_grounding_state(self):
        sid=self.note_session(confirmed=True)
        saved_flags=[dict(id='f0',kind='dose',term='10mg',field='plan',severity='high')]
        db.update_session(sid,flags_json=json.dumps(saved_flags),history_raw='原文',history_summary_json='{"medications":"10mg"}')
        before=db.get_session(sid)
        self.client.get('/api/history?scope=notes')
        self.client.get('/api/sessions/'+sid)
        self.client.get('/api/sessions/'+sid+'/export?field=plan')
        after=db.get_session(sid)
        for key in ('note_json','flags_json','transcript','history_summary_json','evidence_revision','note_revision','confirmed_at'):
            self.assertEqual(before[key],after[key],key)

    def test_content_edit_time_does_not_move_on_confirmation(self):
        sid=self.note_session()
        before=db.get_session(sid)['last_content_edited_at']
        self.client.post('/api/sessions/'+sid+'/confirm',json={'confirmed_by':'测试医生'})
        self.assertEqual(db.get_session(sid)['last_content_edited_at'],before)
        self.client.put('/api/sessions/'+sid+'/history',json={'raw':'既往记录'})
        self.assertGreaterEqual(db.get_session(sid)['last_content_edited_at'],before)

    def test_model_snapshot_survives_setting_change(self):
        sid=self.new_session();self.attach_audio(sid)
        get_config().asr_provider='doubao';get_config().asr_model='asr-original'
        get_config().llm_model='llm-original'
        with mock.patch('app.providers.asr.transcribe',return_value={'text':'本次复查。','model':'asr-actual'}):
            self.client.post('/api/sessions/'+sid+'/transcribe')
        with mock.patch('app.soap.chat',return_value=json.dumps(dict(NOTE,subjective_sections=dict(chief_complaint=NOTE['subjective'],past_history='未提及',present_illness='未提及')))):
            self.client.post('/api/sessions/'+sid+'/note')
        get_config().asr_model='new-asr';get_config().llm_model='new-llm'
        detail=self.client.get('/api/sessions/'+sid).json['session']
        self.assertEqual(detail['asr_used_model'],'asr-actual')
        self.assertEqual(detail['asr_used_provider'],'doubao')
        self.assertEqual(detail['llm_used_model'],'llm-original')
        self.assertIn('last_content_edited_at',detail)

    def test_legacy_model_metadata_not_invented(self):
        sid=self.note_session()
        detail=self.client.get('/api/sessions/'+sid).json['session']
        self.assertIsNone(detail['asr_used_model']);self.assertIsNone(detail['llm_used_model'])
