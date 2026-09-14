import json
from tests.base import AppTestCase
from app import db
from app.main import create_app

class PetTests(AppTestCase):
    def req(self,path,body=None,method='POST',**kw):
        return self.client.open(path,method=method,json=body or {},headers={'X-Management-Token':self.app.config['MANAGEMENT_TOKEN']},**kw)
    def pet(self,name='茉莉',species='dog'):
        r=self.req('/api/pets',dict(name=name,species=species));self.assertEqual(r.status_code,201);return r.json['pet']
    def test_same_names_literal_search_and_pagination(self):
        a=self.pet();b=self.pet(species='cat');self.assertNotEqual(a['id'],b['id'])
        self.assertEqual(self.req('/api/pets/search',{'query':'茉'}).json['total'],2)
        for q in ('%',"' OR 1=1 --",'没有'):
            self.assertEqual(self.req('/api/pets/search',{'query':q}).json['total'],0)
        for i in range(21):self.pet('测试'+str(i))
        a=self.req('/api/pets/search',{'query':'测试'}).json;b=self.req('/api/pets/search',{'query':'测试','offset':20}).json
        self.assertEqual(len(a['pets']),20);self.assertEqual(len(b['pets']),1)
        self.assertTrue(a['has_more']);self.assertFalse(b['has_more'])
        self.assertFalse({p['id'] for p in a['pets']} & {p['id'] for p in b['pets']})
    def test_association_preserves_all_clinical_fields_and_export(self):
        sid=self.new_session();p=self.pet()
        note=dict(subjective='虚构测试：腹泻。',objective='未提及',assessment='未提及',plan='观察',uncertain=[])
        db.update_session(sid,note_json=json.dumps(note),transcript='虚构测试：腹泻。',history_raw='原文',history_summary_json='{"medications":"既往用药"}',flags_json='[]',confirmed_at=123,confirmed_by='测试',note_revision=7,evidence_revision=4)
        before=db.get_session(sid)
        for pid in (p['id'],None,p['id']):
            self.assertEqual(self.req('/api/sessions/'+sid+'/pet',{'pet_id':pid},'PUT').status_code,200)
            after=db.get_session(sid)
            self.assertEqual({k:v for k,v in before.items() if k!='pet_id'},{k:v for k,v in after.items() if k!='pet_id'})
            self.assertEqual(self.client.get('/api/sessions/'+sid+'/export').status_code,200)
        self.assertEqual(self.client.get('/api/sessions/'+sid).json['session']['pet']['id'],p['id'])
        self.assertEqual(self.client.get('/api/history?pet_id='+p['id']).json['sessions'][0]['display_name'],'茉莉')
        self.assertEqual(self.client.get('/api/history?pet_id=unassigned').json['total'],0)
        self.assertEqual(self.req('/api/pets/search').json['pets'][0]['session_count'],1)
    def test_legacy_failed_audio_discoverable(self):
        sid=self.new_session();self.attach_audio(sid);db.update_session(sid,status='failed')
        self.assertIsNone(self.client.get('/api/sessions/'+sid).json['session']['pet'])
        self.assertEqual(self.client.get('/api/history?scope=notes').json['total'],0)
        r=self.client.get('/api/history?scope=all&pet_id=unassigned').json
        self.assertEqual(r['total'],1);self.assertEqual(r['sessions'][0]['id'],sid)
    def test_invalid_missing_busy_inputs(self):
        sid=self.new_session();p=self.pet();before=db.get_session(sid)
        for body in ({'pet_id':'missing'},{'pet_id':''},{'pet_id':[]},{'wrong':p['id']}):
            self.assertIn(self.req('/api/sessions/'+sid+'/pet',body,'PUT').status_code,(400,404));self.assertEqual(db.get_session(sid),before)
        self.assertEqual(self.req('/api/sessions/missing/pet',{'pet_id':p['id']},'PUT').status_code,404)
        db.update_session(sid,status='generating')
        self.assertEqual(self.req('/api/sessions/'+sid+'/pet',{'pet_id':p['id']},'PUT').status_code,409)
        for body in ({'name':'','species':'cat'},{'name':'x'*81,'species':'dog'},{'name':'x','species':'invalid'}):
            self.assertEqual(self.req('/api/pets',body).status_code,400)
        for body in ({'query':[]},{'offset':-1},{'offset':True}):self.assertEqual(self.req('/api/pets/search',body).status_code,400)
    def test_local_guard_and_no_event_logging(self):
        self.assertEqual(self.client.post('/api/pets',json={'name':'茉莉','species':'dog'}).status_code,403)
        self.pet();self.req('/api/pets/search',{'query':'茉莉'})
        self.assertEqual(self.req('/api/pets/search',environ_overrides={'REMOTE_ADDR':'8.8.8.8'}).status_code,403)
        c=db.connect()
        try:self.assertEqual(c.execute('SELECT count(*) FROM event_log').fetchone()[0],0)
        finally:c.close()
    def test_old_schema_migration_restart_preserves_data(self):
        sid=self.new_session();db.update_session(sid,confirmed_at=123,transcript='保留旧文本')
        c=db.connect()
        try:
            c.execute('DROP INDEX idx_sessions_pet');c.execute('ALTER TABLE sessions DROP COLUMN pet_id');c.execute('DROP TABLE pets');c.commit()
        finally:c.close()
        db.init_db();self.assertEqual(db.get_session(sid)['transcript'],'保留旧文本')
        p=self.pet();self.req('/api/sessions/'+sid+'/pet',{'pet_id':p['id']},'PUT')
        app=create_app({'TESTING':True})
        self.assertEqual(app.test_client().get('/api/sessions/'+sid).json['session']['pet']['name'],'茉莉')
        self.assertEqual(db.get_session(sid)['confirmed_at'],123)
