import json
from unittest import mock
from tests.base import AppTestCase
from app import db, history_context, soap
from app.providers import ProviderError

SUMMARY = dict(medications='苯巴比妥10mg，每日2次。', diagnoses='癫痫。', allergies='阿莫西林过敏。', other='去年呕吐。')
NOTE = dict(subjective_sections=dict(chief_complaint='今天无呕吐。',past_history='未提及',present_illness='未提及'), objective='未提及', assessment='未提及', plan='未提及', uncertain=[], history_background=['癫痫。苯巴比妥10mg，每日2次。','阿莫西林过敏','去年呕吐'])

class HistoryContextTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.sid = self.new_session()
        self.url = '/api/sessions/' + self.sid
    def state(self):
        return self.client.get(self.url).json['session']
    def raw(self, text='既往记录原文，含需要核对的兽医资料。'):
        return self.client.put(self.url+'/history', json={'raw':text})
    def summary(self, summary=SUMMARY):
        return self.client.put(self.url+'/history-summary', json={'summary':summary})
    def draft(self):
        self.set_transcript(self.sid,'今天没有呕吐。')
        self.summary()
        with mock.patch('app.soap.chat', return_value=json.dumps(NOTE, ensure_ascii=False)):
            r=self.client.post(self.url+'/note')
        self.assertEqual(r.status_code,200)
        return r.json['session']
    def confirm(self):
        return self.client.post(self.url+'/confirm',json={'confirmed_by':'测试医生'})

    def test_empty_default_and_persisted_history(self):
        self.assertEqual(self.state()['history_raw'],'')
        self.assertFalse(self.state()['history_summary_stale'])
        self.raw();self.summary()
        self.assertEqual(self.state()['history_summary'],SUMMARY)
        self.assertIn('既往记录原文',self.state()['history_raw'])

    def test_generate_summary_calls_model_with_only_raw_record(self):
        self.raw('药物原始记录')
        self.set_transcript(self.sid,'本次私密对话')
        with mock.patch('app.history_context.chat',return_value=json.dumps(SUMMARY)) as chat:
            r=self.client.post(self.url+'/history-summary')
        self.assertEqual(r.status_code,200)
        payload=chat.call_args.kwargs['messages'][1]['content']
        self.assertIn('药物原始记录',payload)
        self.assertNotIn('本次私密对话',payload)
        self.assertTrue(r.json['saved'])

    def test_soap_two_sources_and_programmatic_marker(self):
        self.raw('原文中不应直接传给SOAP的秘密片段')
        self.summary()
        self.set_transcript(self.sid,'今天没有呕吐。')
        with mock.patch('app.soap.chat',return_value=json.dumps(NOTE)) as chat:
            r=self.client.post(self.url+'/note')
        text=chat.call_args.kwargs['messages'][1]['content']
        self.assertIn('本次逐字稿',text)
        self.assertIn('既往摘要',text)
        self.assertNotIn('秘密片段',text)
        s=r.json['session'];self.assertEqual(s['flags'],[])
        self.assertIn('苯巴比妥10mg，每日2次（既往病史）。',s['note']['subjective'])
        self.assertEqual(s['note']['plan'],'未提及')
        self.confirm()
        self.assertIn('（既往病史）',self.client.get(self.url+'/export').json['text'])

    def test_manual_summary_is_final_input(self):
        self.raw()
        with mock.patch('app.history_context.chat',return_value=json.dumps(SUMMARY)):
            self.client.post(self.url+'/history-summary')
        edited=dict(SUMMARY, medications='苯巴比妥5mg，已停药。')
        self.summary(edited);self.set_transcript(self.sid,'复查。')
        with mock.patch('app.soap.chat',return_value=json.dumps({**NOTE,'history_background':[]})) as chat:
            self.client.post(self.url+'/note')
        text=chat.call_args.kwargs['messages'][1]['content']
        self.assertIn('5mg，已停药',text);self.assertNotIn('10mg',text)

    def test_summary_edit_and_clear_recheck_and_revoke_confirmation(self):
        self.draft();self.confirm()
        s=self.summary(dict(SUMMARY,medications='苯巴比妥5mg。')).json['session']
        self.assertFalse(s['confirmed']);self.assertTrue(s['note_needs_review'])
        self.assertIn('dose',{f['kind'] for f in s['flags']})
        self.assertEqual(self.client.get(self.url+'/export').status_code,409)
        s=self.summary({}).json['session']
        self.assertIn('drug',{f['kind'] for f in s['flags']})
        self.assertEqual(history_context.active_text(db.get_session(self.sid)),'')

    def test_transcript_edit_delete_and_retranscription_revoke(self):
        for operation in ('edit','delete','transcribe'):
            self.draft();self.confirm()
            if operation=='edit': r=self.set_transcript(self.sid,'今天精神正常。')
            elif operation=='delete': r=self.client.delete(self.url+'/transcript')
            else:
                self.attach_audio(self.sid)
                with mock.patch('app.providers.asr.transcribe',return_value={'text':'本次复查。'}):
                    r=self.client.post(self.url+'/transcribe')
            self.assertEqual(r.status_code,200)
            self.assertFalse(r.json['session']['confirmed'])
            self.assertEqual(self.client.get(self.url+'/export').status_code,409)

    def test_raw_change_disables_old_summary_until_reviewed(self):
        self.raw('旧原文');self.draft();self.confirm()
        s=self.raw('新原文').json['session']
        self.assertTrue(s['history_summary_stale']);self.assertEqual(s['history_summary'],SUMMARY)
        self.assertFalse(s['confirmed'])
        self.assertEqual(history_context.active_text(db.get_session(self.sid)),'')
        self.assertIn('drug',{f['kind'] for f in s['flags']})
        self.summary()
        self.assertFalse(self.state()['history_summary_stale'])
        self.raw('')
        self.assertEqual(self.state()['history_summary'],{})

    def test_edit_note_uses_both_sources(self):
        s=self.draft()
        r=self.client.put(self.url+'/note',json={'note':s['note']})
        self.assertEqual(r.json['session']['flags'],[])

    def test_failed_or_malformed_summary_preserves_saved_state(self):
        self.raw();self.summary()
        for content in ('not json','[]','{}',json.dumps(dict(SUMMARY, allergies=['bad']))):
            with mock.patch('app.history_context.chat',return_value=content):
                r=self.client.post(self.url+'/history-summary')
            self.assertEqual(r.status_code,502)
            self.assertEqual(self.state()['history_summary'],SUMMARY)
        with mock.patch('app.history_context.chat',side_effect=ProviderError('超时')):
            self.assertEqual(self.client.post(self.url+'/history-summary').status_code,502)

    def test_missing_source_empty_and_input_limits(self):
        with mock.patch('app.history_context.chat') as chat:
            self.assertEqual(self.client.post(self.url+'/history-summary').status_code,400)
            chat.assert_not_called()
        for raw in (None,[], 'x'*30001):
            self.assertEqual(self.raw(raw).status_code,400)
        for summary in (None,[], {'bogus':'x'}, {'medications':3}):
            self.assertEqual(self.summary(summary).status_code,400)
        self.assertEqual(self.client.put('/api/sessions/missing/history',json={'raw':'x'}).status_code,404)

    def test_late_summary_does_not_overwrite_manual_change(self):
        self.raw()
        edited=dict(SUMMARY,medications='兽医刚刚修改的内容')
        def model(**kwargs):
            self.summary(edited)
            return json.dumps(SUMMARY)
        with mock.patch('app.history_context.chat',side_effect=model):
            r=self.client.post(self.url+'/history-summary')
        self.assertEqual(r.status_code,409)
        self.assertEqual(self.state()['history_summary'],edited)

    def test_late_note_does_not_overwrite_changed_evidence(self):
        self.draft()
        before=self.state()['note']
        def model(*args):
            self.summary({})
            return dict(before,subjective='迟到结果')
        with mock.patch('app.soap.generate',side_effect=model):
            r=self.client.post(self.url+'/note')
        self.assertEqual(r.status_code,409)
        self.assertEqual(self.state()['note'],before)
        self.assertTrue(self.state()['note_needs_review'])

    def test_history_never_logged(self):
        self.raw('UNIQUE_RAW_PRIVATE');self.summary(dict(SUMMARY,other='UNIQUE_SUMMARY_PRIVATE'));self.draft()
        conn=db.connect()
        text=json.dumps([dict(row) for row in conn.execute('SELECT * FROM event_log')]);conn.close()
        self.assertNotIn('UNIQUE_RAW_PRIVATE',text);self.assertNotIn('UNIQUE_SUMMARY_PRIVATE',text)

    def test_additive_schema_migration_keeps_legacy_session(self):
        conn=db.connect();conn.execute('DROP TABLE sessions');conn.executescript(db.SCHEMA)
        conn.execute("INSERT INTO sessions(id,created_at,updated_at,status,transcript,confirmed_at,confirmed_by) VALUES ('legacy',1,1,'drafted','old',2,'vet')")
        conn.commit();conn.close()
        db.init_db();db.init_db()
        old=db.get_session('legacy')
        self.assertEqual(old['transcript'],'old');self.assertEqual(old['confirmed_at'],2)
        self.assertEqual(old['history_raw'],'');self.assertEqual(old['evidence_revision'],0)

    def test_stale_confirmation_cannot_sign_changed_evidence(self):
        old=self.draft()
        self.summary(dict(SUMMARY,medications='苯巴比妥5mg。'))
        r=self.client.post(self.url+'/confirm',json={'confirmed_by':'测试医生',
            'evidence_revision':old['evidence_revision'],'note_revision':old['note_revision']})
        self.assertEqual(r.status_code,409)
        self.assertFalse(self.state()['confirmed'])
    def test_stale_confirmation_cannot_sign_changed_note(self):
        old=self.draft()
        self.client.put(self.url+'/note',json={'note':dict(old['note'],subjective_sections=dict(old['note']['subjective_sections'],chief_complaint='新修改'))})
        r=self.client.post(self.url+'/confirm',json={'confirmed_by':'测试医生',
            'evidence_revision':old['evidence_revision'],'note_revision':old['note_revision']})
        self.assertEqual(r.status_code,409)
        self.assertFalse(self.state()['confirmed'])


class MissingHistoryConfigTests(AppTestCase):
    configure_providers=False
    def test_missing_key_not_fake_success(self):
        sid=self.new_session();url='/api/sessions/'+sid
        self.client.put(url+'/history',json={'raw':'原始记录'})
        r=self.client.post(url+'/history-summary')
        self.assertEqual(r.status_code,503);self.assertFalse(r.json['retryable'])
        self.assertIn('ZS_LLM_API_KEY',r.json['missing'])
        self.assertEqual(self.client.get(url).json['session']['history_summary'],{})
