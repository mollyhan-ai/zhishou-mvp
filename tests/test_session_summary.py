import json
from unittest.mock import patch
from tests.base import AppTestCase
from app import db


class SessionSummaryTests(AppTestCase):
    def test_summary_persists_without_changing_clinical_record(self):
        sid=self.new_session()
        note=dict(subjective='原主诉',objective='未提及',assessment='未提及',plan='观察',uncertain=[])
        db.update_session(sid, note_json=json.dumps(note), transcript='原问诊', flags_json='[]',
                          confirmed_at=100,confirmed_by='兽医',evidence_revision=3,note_revision=4)
        before=db.get_session(sid)
        response=self.client.put(f'/api/sessions/{sid}/summary',json={'summary':' 呕吐复诊 ','revision':0})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json['saved'])
        self.assertEqual(self.client.get(f'/api/sessions/{sid}').json['session']['custom_summary'],'呕吐复诊')
        self.assertEqual(self.client.get('/api/history').json['sessions'][0]['preview'],'呕吐复诊')
        after=db.get_session(sid)
        for field in ('note_json','transcript','flags_json','confirmed_at','confirmed_by','evidence_revision','note_revision','last_content_edited_at'):
            self.assertEqual(after[field],before[field],field)
        export=self.client.get(f'/api/sessions/{sid}/export').json['text']
        self.assertIn('原主诉',export);self.assertNotIn('呕吐复诊',export)
        conn=db.connect()
        try:self.assertNotIn('呕吐复诊',str([tuple(r) for r in conn.execute('SELECT * FROM event_log')]))
        finally:conn.close()

    def test_empty_restores_automatic_and_regeneration_keeps_manual_summary(self):
        sid=self.new_session();url=f'/api/sessions/{sid}/summary'
        self.client.put(url,json={'summary':'自定义摘要','revision':0})
        db.update_session(sid,note_json=json.dumps({'subjective_sections':{'chief_complaint':'新主诉'}}))
        self.assertEqual(self.client.get('/api/history').json['sessions'][0]['preview'],'自定义摘要')
        self.assertEqual(self.client.put(url,json={'summary':'  ','revision':1}).status_code,200)
        self.assertIsNone(db.get_session(sid)['custom_summary'])
        self.assertEqual(self.client.get('/api/history').json['sessions'][0]['preview'],'新主诉')

    def test_invalid_payload_missing_session_and_conflict(self):
        sid=self.new_session();url=f'/api/sessions/{sid}/summary'
        for body in ([],{}, {'summary':None,'revision':0},{'summary':'x'*201,'revision':0},{'summary':'ok','revision':True}):
            self.assertEqual(self.client.put(url,json=body).status_code,400)
        self.assertEqual(self.client.put('/api/sessions/missing/summary',json={'summary':'x','revision':0}).status_code,404)
        self.client.put(url,json={'summary':'先保存','revision':0})
        self.assertEqual(self.client.put(url,json={'summary':'覆盖','revision':0}).status_code,409)
        self.assertEqual(db.get_session(sid)['custom_summary'],'先保存')

    def test_failed_write_does_not_report_success(self):
        sid=self.new_session()
        with patch('app.db.mutate_session',side_effect=RuntimeError('disk full')):
            response=self.client.put(f'/api/sessions/{sid}/summary',json={'summary':'不能保存','revision':0})
        self.assertEqual(response.status_code,500)
        self.assertFalse(response.json.get('saved',False))
        self.assertIsNone(db.get_session(sid)['custom_summary'])

    def test_migration_idempotent_and_preview_bounded(self):
        sid=self.new_session()
        self.client.put(f'/api/sessions/{sid}/summary',json={'summary':'字'*200,'revision':0})
        db.init_db();db.init_db()
        self.assertEqual(db.get_session(sid)['custom_summary'],'字'*200)
        self.assertEqual(len(self.client.get('/api/history').json['sessions'][0]['preview']),80)
        self.assertIsNone(self.client.get(f'/api/sessions/{self.new_session()}').json['session']['custom_summary'])
