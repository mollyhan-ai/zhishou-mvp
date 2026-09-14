import json
import os
from pathlib import Path
from unittest.mock import Mock, patch
import requests
from .base import AppTestCase
from app import db
from app.config import get_config
from app.settings_store import BASE_URL, MODEL

class ManagementTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.path = Path(self.tmp) / '.env'
        self.app.config['SETTINGS_ENV_PATH'] = str(self.path)
        self.headers = {'X-Management-Token':self.app.config['MANAGEMENT_TOKEN']}
        self.extra = {k:os.environ.get(k) for k in ('ZS_ASR_LANGUAGE','ZS_ASR_TIMEOUT','ZS_LLM_TIMEOUT')}
    def tearDown(self):
        for k,v in self.extra.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        super().tearDown()
    def save(self, key='private-new-key'):
        return self.client.put('/api/settings/doubao', json={'api_key':key}, headers=self.headers)
    def test_settings_redacts_key(self):
        r = self.client.get('/api/settings')
        self.assertNotIn('test-llm-key',r.get_data(as_text=True))
        self.assertTrue(r.json['key_configured'])
    def test_save_atomic_private_live(self):
        self.path.write_text('# keep\nZS_KB_MODE=placeholder\nZS_LLM_API_KEY=old\nZS_LLM_API_KEY=duplicate\n')
        r = self.save()
        self.assertTrue(r.json['saved'])
        self.assertNotIn('private-new-key',r.get_data(as_text=True))
        self.assertEqual(self.path.read_text().count('ZS_LLM_API_KEY='),1)
        self.assertIn('# keep\nZS_KB_MODE=placeholder',self.path.read_text())
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
        self.assertEqual(get_config().llm_api_key,'private-new-key')
        self.assertEqual(get_config().asr_model,MODEL)
    def test_save_failure_preserves_previous(self):
        before = get_config().llm_api_key
        self.path.write_text('original')
        with patch('app.settings_store.os.replace',side_effect=OSError('private')):
            r = self.save()
        self.assertEqual(r.status_code,500)
        self.assertFalse(r.json['saved'])
        self.assertEqual(get_config().llm_api_key,before)
        self.assertEqual(self.path.read_text(),'original')
        self.assertEqual(list(self.path.parent.glob('.env-*')),[])
    def test_local_origin_and_token_required(self):
        self.assertEqual(self.client.put('/api/settings/doubao',json={'api_key':'key'}).status_code,403)
        self.assertEqual(self.client.put('/api/settings/doubao',json={'api_key':'key'},headers={**self.headers,'Origin':'https://evil.test'}).status_code,403)
        self.assertEqual(self.client.get('/api/settings',base_url='http://evil.test').status_code,403)
        self.assertEqual(self.client.get('/api/history',environ_overrides={'REMOTE_ADDR':'192.168.0.1'}).status_code,403)
        self.assertFalse(self.path.exists())
    def test_invalid_key_not_saved(self):
        for key in ('','white space','line\nbreak',123,'a'*1025):
            self.assertEqual(self.save(key).status_code,400)
        self.assertFalse(self.path.exists())
    def test_connection_real_response_only(self):
        self.save()
        for status,data,ok in ((200,{'choices':[{'message':{'content':'OK'},'finish_reason':'stop'}]},True),(401,{},False),(200,{},False),(200,{'choices':[{'message':{'content':'partial'},'finish_reason':'length'}]},False),(404,{'error':{'code':'ModelNotOpen','message':'private account'}},False)):
            self.app.config['CONNECTION_TEST_AFTER'] = 0
            upstream = Mock(status_code=status)
            upstream.json.return_value = data
            with patch('app.management.requests.post',return_value=upstream) as post:
                r = self.client.post('/api/settings/test',json={},headers=self.headers)
                self.assertEqual(r.status_code,200 if ok else 502)
                self.assertNotIn('private account',r.get_data(as_text=True))
                self.assertEqual(post.call_args.args[0],BASE_URL+'/chat/completions')
                self.assertFalse(post.call_args.kwargs['allow_redirects'])
                self.assertEqual(post.call_args.kwargs['json']['messages'],[{'role':'user','content':'请只回复OK'}])
                if status == 404: self.assertIn('尚未开通',r.json['message'])
                self.assertEqual(self.client.post('/api/settings/test',json={},headers=self.headers).status_code,429)
                self.assertEqual(post.call_count,1)
    def test_connection_timeout(self):
        self.save()
        with patch('app.management.requests.post',side_effect=requests.Timeout('private')):
            r=self.client.post('/api/settings/test',json={},headers=self.headers)
        self.assertEqual(r.status_code,502)
        self.assertNotIn('private',r.get_data(as_text=True))
    def test_arbitrary_host_never_receives_key(self):
        with patch('app.management.requests.post') as post:
            self.assertEqual(self.client.post('/api/settings/test',json={},headers=self.headers).status_code,400)
            post.assert_not_called()
    def test_history_pagination_and_no_clinical_text(self):
        ids=[self.new_session() for _ in range(22)]
        db.update_session(ids[-1],transcript='private clinical',note_json='{}')
        a=self.client.get('/api/history').json
        b=self.client.get('/api/history?offset=20').json
        self.assertEqual(len(a['sessions']),20)
        self.assertEqual(len(b['sessions']),2)
        self.assertTrue(a['has_more'])
        self.assertFalse(b['has_more'])
        self.assertFalse({s['id'] for s in a['sessions']} & {s['id'] for s in b['sessions']})
        self.assertNotIn('private clinical',json.dumps(a))
        self.assertEqual(self.client.get('/api/history?offset=-1').status_code,400)
    def test_reopen_confirmation_gate(self):
        sid=self.new_session()
        note=dict(subjective='测试',objective='未提及',assessment='未提及',plan='未提及')
        db.update_session(sid,note_json=json.dumps(note),transcript='测试',confirmed_at=100,confirmed_by='测试人')
        self.assertTrue(self.client.get('/api/sessions/'+sid).json['session']['confirmed'])
        self.assertEqual(self.client.get('/api/sessions/'+sid+'/export').status_code,200)
        self.client.put('/api/sessions/'+sid+'/note',json={'note':note})
        self.assertFalse(self.client.get('/api/sessions/'+sid).json['session']['confirmed'])
        self.assertEqual(self.client.get('/api/sessions/'+sid+'/export').status_code,409)
    def test_audio_range_missing_and_containment(self):
        sid=self.new_session()
        self.attach_audio(sid,data=b'RIFF0123456789')
        r=self.client.get('/api/sessions/'+sid+'/audio',headers={'Range':'bytes=0-3'})
        self.assertEqual(r.status_code,206)
        self.assertEqual(r.data,b'RIFF')
        self.assertIn('no-store',r.headers['Cache-Control'])
        r.close()
        self.client.delete('/api/sessions/'+sid+'/audio')
        self.assertEqual(self.client.get('/api/sessions/'+sid+'/audio').status_code,404)
        self.path.write_text('secret')
        db.update_session(sid,audio_path=str(self.path))
        self.assertEqual(self.client.get('/api/sessions/'+sid+'/audio').status_code,404)

class MissingManagementConfigTests(AppTestCase):
    configure_providers = False
    def test_missing_config_is_explicit(self):
        headers = {'X-Management-Token': self.app.config['MANAGEMENT_TOKEN']}
        with patch('app.management.requests.post') as post:
            r = self.client.post('/api/settings/test', json={}, headers=headers)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json['error_kind'], 'config')
        self.assertFalse(r.json['retryable'])
        self.assertIn('ZS_LLM_API_KEY', r.json['missing'])
        post.assert_not_called()
