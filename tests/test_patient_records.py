import json
from unittest.mock import patch
from tests.base import AppTestCase
from app import db, history_context, grounding, soap
from app.patient_records import normalized, quoted_source, serializer

class PatientRecordTests(AppTestCase):
    def req(self,path,body=None,method='POST'):
        return self.client.open(path,method=method,json=body or {},headers={'X-Management-Token':self.app.config['MANAGEMENT_TOKEN']})
    def count(self,table):
        c=db.connect()
        try:return c.execute('SELECT count(*) FROM '+table).fetchone()[0]
        finally:c.close()
    def create_pet(self,**f):
        r=self.req('/api/patient-records',{'reviewer':'测试兽医','reviewed':True,'profile':normalized(f)})
        self.assertEqual(r.status_code,201,r.json);return r.json['pet']
    def review(self,sid,pet=None,**extra):
        s=db.get_session(sid)
        body={'reviewer':'测试兽医','reviewed':True,'manual_review':True,'profile':pet['profile'] if pet else normalized({'name':'molly','species':'dog','breed':'比熊','sex_neuter':'female_intact'}),
              'pet_id':pet['id'] if pet else None,'profile_revision':pet['profile_revision'] if pet else None,
              'create_new':not pet,'evidence_revision':s['evidence_revision'],'note_revision':s['note_revision'],'observations':{}}
        body.update(extra);return self.req('/api/sessions/'+sid+'/patient-review',body)
    def note(self,sid):
        note={'subjective_sections':{'chief_complaint':'呕吐。','past_history':'未提及','present_illness':'呕吐。'},'objective':'未提及','assessment':'未提及','plan':'未提及','uncertain':[]}
        r=self.client.put('/api/sessions/'+sid+'/note',json={'note':note});self.assertEqual(r.status_code,200)
    def confirm(self,sid):
        return self.client.post('/api/sessions/'+sid+'/confirm',json={'confirmed_by':'测试兽医'})
    def test_candidate_draft_survives_refresh_without_creating_a_record(self):
        sid=self.new_session();self.set_transcript(sid,'茉莉是比熊，3岁，未绝育。')
        data={'name':{'value':'茉莉','source':'茉莉'},'sex_neuter':{'value':'unknown_intact','source':'未绝育'}}
        events=self.count('event_log')
        with patch('app.patient_records.chat',return_value=json.dumps(data)):
            r=self.req('/api/sessions/'+sid+'/patient-candidates')
        self.assertEqual(r.status_code,200);self.assertFalse(r.json['saved'])
        self.assertEqual(set(r.json['candidate']),{'name','species','breed','sex_neuter','age'})
        self.assertEqual(self.count('pets'),0);self.assertEqual(self.count('patient_audit'),0)
        self.assertEqual(self.count('event_log'),events)
        restored=self.client.get('/api/sessions/'+sid).json['session']['patient_candidate']
        self.assertEqual(restored['candidate'],r.json['candidate']);self.assertEqual(restored['token'],r.json['token'])
        with patch('app.patient_records.chat') as model:
            cached=self.req('/api/sessions/'+sid+'/patient-candidates')
        model.assert_not_called();self.assertTrue(cached.json['cached'])
        replacement={'name':{'value':'Molly','source':'茉莉'}}
        with patch('app.patient_records.chat',return_value=json.dumps(replacement)) as model:
            refreshed=self.req('/api/sessions/'+sid+'/patient-candidates',{'refresh':True})
        model.assert_called_once();self.assertFalse(refreshed.json['cached'])
        self.assertEqual(refreshed.json['candidate']['name']['value'],'Molly')
        self.assertEqual(self.client.get('/api/sessions/'+sid).json['session']['patient_candidate']['candidate'],
                         refreshed.json['candidate'])
        self.set_transcript(sid,'茉莉今天复查。')
        self.assertIsNone(self.client.get('/api/sessions/'+sid).json['session']['patient_candidate'])
    def test_invalid_evidence_rejected_and_no_provider_detail_leak(self):
        sid=self.new_session();self.set_transcript(sid,'呕吐。')
        for output in ('oops',json.dumps({'name':{'value':'molly','source':'不存在的原文'}})):
            with patch('app.patient_records.chat',return_value=output):r=self.req('/api/sessions/'+sid+'/patient-candidates')
            self.assertEqual(r.status_code,502);self.assertEqual(self.count('pets'),0)
    def test_empty_fields_number_sequence_restart_merge_not_reused(self):
        a=self.create_pet();b=self.create_pet(name='molly');self.assertEqual(a['record_number'],'Z00001');self.assertEqual(b['record_number'],'Z00002')
        db.init_db();c=self.create_pet();self.assertEqual(c['record_number'],'Z00003')
        self.assertEqual(a['profile']['sex_neuter'],'unknown');self.assertNotIn('weight',a['profile'])
    def test_manual_confirmation_required_and_local_guard(self):
        sid=self.new_session()
        r=self.req('/api/patient-records',{'profile':{},'reviewer':'x','reviewed':False});self.assertEqual(r.status_code,400)
        r=self.client.post('/api/patient-records',json={'profile':{},'reviewer':'x','reviewed':True});self.assertEqual(r.status_code,403)
        self.assertEqual(self.review(sid,reviewed=False).status_code,400);self.assertEqual(self.count('pets'),0)
    def test_species_breed_validation_combined_unknown_sex(self):
        r=self.req('/api/patient-records',{'reviewer':'x','reviewed':True,'profile':{'species':'cat','breed':'比熊'}});self.assertEqual(r.status_code,400)
        p=self.create_pet(species='dog',breed='比熊',sex_neuter='unknown_intact');self.assertEqual(p['profile']['sex_neuter'],'unknown_intact')
    def test_bind_sources_snapshot_visits_and_confirmation_gate(self):
        sid=self.new_session();self.set_transcript(sid,'呕吐。');self.note(sid);self.assertEqual(self.confirm(sid).status_code,200)
        p=self.create_pet(name='molly',species='dog',medications='阿莫西林 10mg 每日一次，已停药。',allergies='青霉素过敏。')
        r=self.review(sid,p,observations={'age':'3岁','weight':'5kg','immunization':'今年未接种疫苗','deworming':'上月已驱虫'})
        self.assertEqual(r.status_code,200,r.json);s=r.json['session'];self.assertFalse(s['confirmed']);self.assertTrue(s['note_needs_review'])
        self.assertIn('已停药',s['effective_history']);self.assertNotIn('5kg',s['effective_history']);self.assertEqual(self.count('visit_observations'),1)
        self.assertEqual(self.client.get('/api/sessions/'+sid+'/export').status_code,409)
        self.assertEqual(self.confirm(sid).status_code,200)
        self.set_transcript(sid,'呕吐。名字改为其他动物。')
        self.assertTrue(self.client.get('/api/sessions/'+sid).json['session']['patient_review_needed'])
        self.assertEqual(self.confirm(sid).status_code,409)
        self.assertEqual(self.req('/api/sessions/'+sid+'/pet',{'pet_id':None},'PUT').status_code,409)
        self.assertEqual(self.review(sid,p).status_code,200);self.assertEqual(self.confirm(sid).status_code,200)
    def test_conflict_requires_explicit_decisions_no_silent_overwrite(self):
        sid=self.new_session();self.set_transcript(sid,'茉莉是犬，未绝育。')
        p=self.create_pet(name='molly',species='dog',sex_neuter='female_spayed')
        data={'name':{'value':'茉莉','source':'茉莉'},'sex_neuter':{'value':'unknown_intact','source':'未绝育'}}
        with patch('app.patient_records.chat',return_value=json.dumps(data)):token=self.req('/api/sessions/'+sid+'/patient-candidates').json['token']
        before=self.count('patient_audit')
        r=self.review(sid,p,candidate_token=token,manual_review=False)
        self.assertEqual(r.status_code,409);self.assertEqual(self.count('patient_audit'),before)
        # Keeping the stored values is an explicit rejection of suspected ASR values.
        r=self.review(sid,p,candidate_token=token,manual_review=False,decisions={'name':'keep','sex_neuter':'keep'})
        self.assertEqual(r.status_code,200,r.json);self.assertEqual(r.json['session']['pet']['profile']['sex_neuter'],'female_spayed')
        self.assertEqual(r.json['session']['pet']['name'],'molly')
        self.assertIsNone(r.json['session']['patient_candidate'])
        stored=db.get_session(sid)
        self.assertEqual(stored['patient_candidate_json'],'{}');self.assertIsNone(stored['patient_candidate_hash'])
    def test_stale_candidate_and_archive_revision_are_rejected_atomically(self):
        sid=self.new_session();self.set_transcript(sid,'名字茉莉。')
        with patch('app.patient_records.chat',return_value='{}'):token=self.req('/api/sessions/'+sid+'/patient-candidates').json['token']
        self.set_transcript(sid,'名字molly。')
        r=self.review(sid,candidate_token=token,manual_review=False);self.assertEqual(r.status_code,409);self.assertEqual(self.count('pets'),0)
        p=self.create_pet(name='molly');values={**p['profile'],'other':'已核对的背景'}
        r=self.req('/api/patient-records/'+p['id'],{'reviewer':'x','reviewed':True,'profile_revision':p['profile_revision'],'profile':values},'PUT');self.assertEqual(r.status_code,200)
        self.assertEqual(self.review(sid,p).status_code,409)
    def test_archive_updates_do_not_rewrite_old_signed_note_or_context(self):
        p=self.create_pet(name='molly',diagnoses='既往慢性肾病。');sid=self.new_session();self.set_transcript(sid,'呕吐。');self.note(sid)
        self.assertEqual(self.review(sid,p).status_code,200);self.confirm(sid);before=db.get_session(sid)
        values={**p['profile'],'diagnoses':'既往慢性肾病，现已复查。'}
        r=self.req('/api/patient-records/'+p['id'],{'reviewer':'x','reviewed':True,'profile_revision':p['profile_revision'],'profile':values,'decisions':{'diagnoses':'replace'}},'PUT')
        self.assertEqual(r.status_code,200);self.assertEqual(db.get_session(sid),before)
        self.assertEqual(self.client.get('/api/sessions/'+sid+'/export').status_code,200)
        fresh=self.new_session();self.review(fresh,r.json['pet']);self.assertIn('现已复查',history_context.active_text(db.get_session(fresh)))
        self.assertNotIn('现已复查',history_context.active_text(before))
    def test_grounding_union_unchanged_and_context_is_used_by_soap(self):
        transcript='呕吐。';summary={'medications':'阿莫西林 10mg 每日一次','diagnoses':'','allergies':'','other':''}
        note={'subjective':'阿莫西林 10mg 每日一次（既往病史）。','objective':'未提及','assessment':'未提及','plan':'未提及'}
        legacy={'history_summary_json':json.dumps(summary)}
        snapshot={'patient_context_json':json.dumps({'summary':summary})}
        self.assertEqual(grounding.check(transcript,note,history_context.active_text(legacy)),grounding.check(transcript,note,history_context.active_text(snapshot)))
        self.assertTrue(any(f['term']=='阿莫西林' for f in grounding.check(transcript,note,'')))
        output={'subjective_sections':{'chief_complaint':'呕吐。','past_history':'未提及','present_illness':'呕吐。'},'objective':'未提及','assessment':'未提及','plan':'未提及','uncertain':[],'history_background':['阿莫西林 10mg 每日一次']}
        with patch('app.soap.chat',return_value=json.dumps(output)) as model:
            got=soap.generate(transcript,history_context.active_text(snapshot))
        self.assertIn('既往病史',got['subjective_sections']['past_history']);self.assertIn('阿莫西林',str(model.call_args))
    def test_merge_manual_alias_search_signed_snapshot_preserved(self):
        a=self.create_pet(name='茉莉',species='dog');b=self.create_pet(name='molly',species='dog');sid=self.new_session();self.set_transcript(sid,'呕吐。');self.note(sid);self.review(sid,a);self.confirm(sid);before=db.get_session(sid)
        body={'target_id':b['id'],'source_revision':a['profile_revision'],'target_revision':b['profile_revision'],'reviewer':'x','reviewed':True,'profile':b['profile']}
        self.assertEqual(self.req('/api/patient-records/'+a['id']+'/merge',body).status_code,409)
        body['decisions']={'name':'keep'};r=self.req('/api/patient-records/'+a['id']+'/merge',body);self.assertEqual(r.status_code,200,r.json)
        after=db.get_session(sid);self.assertEqual(after['pet_id'],b['id']);self.assertEqual({k:v for k,v in before.items() if k!='pet_id'},{k:v for k,v in after.items() if k!='pet_id'})
        for query in ('茉莉',a['record_number'],'molly'):
            results=self.req('/api/pets/search',{'query':query}).json;self.assertEqual(results['total'],1);self.assertEqual(results['pets'][0]['id'],b['id'])
        self.assertEqual(self.client.get('/api/sessions/'+sid+'/export').status_code,200)
        c=self.create_pet();self.assertEqual(c['record_number'],'Z00003')
    def test_session_chief_complaint_preview(self):
        sid=self.new_session();self.set_transcript(sid,'呕吐。');self.note(sid);self.review(sid)
        s=self.client.get('/api/history').json['sessions'][0];self.assertEqual(s['display_name'],'molly');self.assertEqual(s['preview'],'呕吐。')

    def test_failed_bind_rolls_back_record_number_and_audit(self):
        sid=self.new_session();self.set_transcript(sid,'呕吐。')
        with patch('app.patient_records.attach',side_effect=RuntimeError('synthetic write failure')):
            with self.assertRaises(RuntimeError):self.review(sid)
        self.assertEqual(self.count('pets'),0);self.assertEqual(self.count('patient_audit'),0)
        self.assertEqual(self.count('patient_numbers'),0);self.assertIsNone(db.get_session(sid)['pet_id'])
        self.assertEqual(self.review(sid).json['session']['pet']['record_number'],'Z00001')

    def test_prior_age_is_dated_and_does_not_become_static_profile(self):
        p=self.create_pet(name='molly');first=self.new_session();self.review(first,p,observations={'age':'3岁','weight':'5kg'})
        second=self.new_session();self.review(second,p,observations={'weight':'5.5kg'})
        view=self.client.get('/api/sessions/'+second).json['session']
        self.assertEqual(view['patient_observations'],{'weight':'5.5kg'})
        self.assertEqual(view['pet']['recent_observations']['age']['value'],'3岁')
        self.assertEqual(view['pet']['recent_observations']['age']['observed_at'],db.get_session(first)['created_at'])
        self.assertNotIn('age',view['pet']['profile'])
        self.assertEqual(view['pet']['last_visit'],db.get_session(first)['created_at'])

    def test_parallel_stale_bind_does_not_replace_a_newer_note(self):
        sid=self.new_session();self.set_transcript(sid,'呕吐。');old=db.get_session(sid);self.note(sid)
        r=self.review(sid,note_revision=old['note_revision'])
        self.assertEqual(r.status_code,409);self.assertEqual(self.count('pets'),0)

    def unlink(self,sid,**extra):
        s=db.get_session(sid)
        body={'reviewer':'测试兽医','reviewed':True,'pet_id':s['pet_id'],
              'evidence_revision':s['evidence_revision'],'note_revision':s['note_revision']}
        body.update(extra)
        return self.req('/api/sessions/'+sid+'/patient-unlink',body)

    def test_unlink_preserves_note_and_manual_history_but_rechecks_removed_archive_evidence(self):
        sid=self.new_session();self.set_transcript(sid,'呕吐。')
        p=self.create_pet(name='molly',medications='阿莫西林 10mg 每日一次')
        self.assertEqual(self.review(sid,p,observations={'weight':'5kg'}).status_code,200)
        manual={'allergies':'青霉素过敏。'}
        db.update_session(sid,history_raw='手动输入过敏史',history_summary_json=json.dumps(manual))
        note={'subjective':'呕吐。阿莫西林 10mg 每日一次（既往病史）。青霉素过敏（既往病史）。','objective':'未提及','assessment':'未提及','plan':'未提及','uncertain':[]}
        self.assertEqual(self.client.put('/api/sessions/'+sid+'/note',json={'note':note}).status_code,200)
        self.assertEqual(self.confirm(sid).status_code,200)
        before=db.get_session(sid)
        r=self.unlink(sid);self.assertEqual(r.status_code,200,r.json)
        v=r.json['session'];after=db.get_session(sid)
        self.assertIsNone(v['pet']);self.assertEqual(v['patient_context'],{})
        self.assertEqual(v['patient_observations'],{'weight':'5kg'})
        for key in ('note_json','transcript','history_raw','history_summary_json','note_revision'):
            self.assertEqual(after[key],before[key])
        self.assertEqual(after['evidence_revision'],before['evidence_revision']+1)
        self.assertFalse(v['confirmed']);self.assertTrue(v['note_needs_review'])
        expected=grounding.check(before['transcript'],json.loads(before['note_json']),history_context.summary_text(manual))
        self.assertEqual(v['flags'],expected)
        self.assertTrue(any(f['term']=='阿莫西林' for f in v['flags']))
        self.assertEqual(self.client.get('/api/sessions/'+sid+'/export').status_code,409)
        archive=self.req('/api/pets/search',{'query':'molly'}).json['pets'][0]
        self.assertEqual(archive['session_count'],0);self.assertIsNone(archive['last_observations'])
        self.assertEqual(archive['recent_observations'],{});self.assertEqual(self.count('pets'),1)
        self.assertEqual(self.count('visit_observations'),1)
        changes=self.req('/api/patient-records/'+p['id']+'/audit',method='GET').json['changes']
        self.assertEqual(changes[0]['action'],'unlink-context')
        self.assertEqual(self.review(sid,p,observations=v['patient_observations']).status_code,200)

    def test_unlink_needs_review_and_local_access(self):
        sid=self.new_session();p=self.create_pet(name='molly');self.review(sid,p);before=db.get_session(sid)
        for extra in ({'reviewed':False},{'reviewer':''}):
            self.assertEqual(self.unlink(sid,**extra).status_code,400)
        self.assertEqual(self.client.post('/api/sessions/'+sid+'/patient-unlink',json={}).status_code,403)
        self.assertEqual(db.get_session(sid),before)

    def test_unlink_refuses_stale_or_changed_association_and_busy_work(self):
        sid=self.new_session();p=self.create_pet(name='molly');self.review(sid,p);before=db.get_session(sid)
        for extra in ({'pet_id':'another'},{'evidence_revision':-1},{'note_revision':-1}):
            self.assertEqual(self.unlink(sid,**extra).status_code,409)
            self.assertEqual(db.get_session(sid),before)
        for status in ('transcribing','generating'):
            db.update_session(sid,status=status)
            self.assertEqual(self.unlink(sid).status_code,409)
        db.update_session(sid,status='created')
        self.assertEqual(self.unlink(sid).status_code,200)
        self.assertEqual(self.unlink(sid).status_code,409)

    def test_failed_unlink_rolls_back_and_unknown_session_returns_404(self):
        sid=self.new_session();p=self.create_pet(name='molly');self.review(sid,p)
        before=db.get_session(sid);count=self.count('patient_audit')
        with patch('app.patient_records.audit',side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError): self.unlink(sid)
        self.assertEqual(db.get_session(sid),before);self.assertEqual(self.count('patient_audit'),count)
        r=self.req('/api/sessions/missing/patient-unlink',{'reviewer':'x','reviewed':True})
        self.assertEqual(r.status_code,404)

    def test_unlinked_observations_not_attributed_to_old_patient_on_legacy_rebind(self):
        sid=self.new_session();a=self.create_pet(name='molly');b=self.create_pet(name='豆包')
        self.review(sid,a,observations={'age':'3岁'});self.unlink(sid)
        self.assertEqual(self.req('/api/sessions/'+sid+'/pet',{'pet_id':b['id']},'PUT').status_code,200)
        for p in (a,b):
            view=self.req('/api/pets/search',{'query':p['name']}).json['pets'][0]
            self.assertEqual(view['recent_observations'],{})

    def test_partial_candidates_keep_name_and_exclude_rejected_evidence_from_token(self):
        sid=self.new_session()
        transcript='医生你好我的小狗叫 molly英文拼写是 m o l l y它是比熊犬今年三岁了母犬还没有做绝育。去年肠胃炎。'
        self.set_transcript(sid,transcript)
        data={'name':{'value':'molly','source':'我的小狗叫molly'},
              'species':{'value':'dog','source':'比熊犬'},
              'sex_neuter':{'value':'female_intact','source':'母犬，还没有做绝育。'},
              'diagnoses':{'value':'去年肠胃炎已治愈','source':'去年肠胃炎已治愈'},
              'weight':None}
        before=db.get_session(sid);events=self.count('event_log')
        with patch('app.patient_records.chat',return_value=json.dumps(data)):
            r=self.req('/api/sessions/'+sid+'/patient-candidates')
        self.assertEqual(r.status_code,200,r.json)
        c=r.json['candidate'];self.assertEqual(set(c),{'name','species','breed','sex_neuter','age'})
        self.assertEqual(c['name'],{'value':'molly','source':'我的小狗叫 molly'})
        self.assertEqual(c['sex_neuter']['source'],'母犬还没有做绝育')
        self.assertEqual({w['field'] for w in r.json['warnings']},{'diagnoses','weight'})
        for item in c.values():self.assertIn(item['source'],transcript)
        with self.app.app_context():self.assertEqual(serializer().loads(r.json['token'])['candidate'],c)
        self.assertFalse(r.json['saved']);self.assertNotEqual(db.get_session(sid),before)
        self.assertEqual(self.count('event_log'),events);self.assertEqual(self.count('pets'),0)
        self.assertEqual(self.review(sid,candidate_token=r.json['token'],manual_review=False,reviewed=False).status_code,400)
        self.assertEqual(self.count('pets'),0)

    def test_bad_optional_field_does_not_discard_valid_name(self):
        for item in (None,[],{'value':'犬','source':'犬'}, {'value':3,'source':'犬'},
                     {'value':'dog','source':''},{'value':'dog','source':'不存在'}):
            with self.subTest(item=item):
                sid=self.new_session();self.set_transcript(sid,'molly 是犬。')
                data={'name':{'value':'molly','source':'molly'},'species':item}
                with patch('app.patient_records.chat',return_value=json.dumps(data)):
                    r=self.req('/api/sessions/'+sid+'/patient-candidates')
                self.assertEqual(r.status_code,200,r.json)
                self.assertEqual(set(r.json['candidate']),{'name','species'})
                self.assertEqual(r.json['candidate']['species'],{'value':'dog','source':'犬'})
                self.assertEqual(r.json['warnings'][0]['field'],'species')
        self.assertEqual(self.count('pets'),0)

    def test_explicit_spelled_name_is_recovered_when_model_omits_it(self):
        sid=self.new_session()
        transcript='医生你好我的小狗叫 molly英文拼写是 m o l l y它是比熊犬今年三岁了母犬还没有做绝育。'
        self.set_transcript(sid,transcript)
        data={'species':{'value':'dog','source':'比熊犬'},
              'sex_neuter':{'value':'female_intact','source':'母犬还没有做绝育'}}
        with patch('app.patient_records.chat',return_value=json.dumps(data)):
            r=self.req('/api/sessions/'+sid+'/patient-candidates')
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['candidate']['name']['value'],'molly')
        self.assertEqual(r.json['candidate']['name']['source'],'英文拼写是 m o l l y')
        self.assertEqual(r.json['candidate']['breed'],{'value':'比熊','source':'比熊犬'})
        self.assertEqual(r.json['candidate']['age']['value'],'今年三岁')
        self.assertEqual(self.count('pets'),0)

    def test_explicit_source_can_recover_when_the_only_model_field_is_invalid(self):
        sid=self.new_session();self.set_transcript(sid,'我的小狗叫 molly，是比熊犬。')
        invalid={'name':{'value':'茉莉','source':'模型虚构的原文'}}
        with patch('app.patient_records.chat',return_value=json.dumps(invalid)):
            r=self.req('/api/sessions/'+sid+'/patient-candidates')
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['candidate']['name'],{'value':'molly','source':'小狗叫 molly'})
        self.assertEqual(r.json['candidate']['breed'],{'value':'比熊','source':'比熊犬'})
        self.assertEqual(r.json['warnings'],[{'field':'name','reason':'引用内容无法在逐字稿中定位'}])
        self.assertEqual(self.count('pets'),0)

    def test_source_formatting_preserves_original_continuous_span(self):
        for transcript,quote,expected in (
            ('小狗叫 molly 今年三岁','小狗叫molly，今年三岁。','小狗叫 molly 今年三岁'),
            ('母犬还没有做绝育','母犬，还没有做绝育。','母犬还没有做绝育'),
            ('前文体重 5.2 kg后文','体重5.2kg','体重 5.2 kg')):
            self.assertEqual(quoted_source(transcript,quote),expected)

    def test_source_matching_cannot_rewrite_negation_numbers_or_join_distant_spans(self):
        for transcript,quote in (
            ('母犬还没有做绝育','母犬已经做绝育'),('名字茉莉','名字molly'),
            ('体重5.2kg','体重52kg'),('给药1/2片','给药12片'),
            ('给药1 , 5mg','给药15mg'),('给药一、二片','给药一二片'),
            ('剂量-5mg','剂量5mg'),('名字molly呕吐三天是比熊犬','名字molly是比熊犬')):
            with self.subTest(quote=quote):self.assertIsNone(quoted_source(transcript,quote))

    def test_all_invalid_candidates_report_field_reason_without_echoing_response(self):
        sid=self.new_session();self.set_transcript(sid,'molly。')
        with patch('app.patient_records.chat',return_value=json.dumps({'allergies':{'value':'秘密诊断','source':'虚构引用'}})):
            r=self.req('/api/sessions/'+sid+'/patient-candidates')
        self.assertEqual(r.status_code,502);text=r.json['message']
        self.assertIn('过敏史',text);self.assertIn('无法在逐字稿中定位',text)
        self.assertNotIn('秘密诊断',text);self.assertNotIn('虚构引用',text)
        self.assertEqual(self.count('pets'),0)

    def test_invalid_response_structure_still_rejected(self):
        sid=self.new_session();self.set_transcript(sid,'molly。')
        for output in ('not-json','[]','null','{"invented_field":{}}'):
            with patch('app.patient_records.chat',return_value=output):
                r=self.req('/api/sessions/'+sid+'/patient-candidates')
            self.assertEqual(r.status_code,502,r.json)
        with patch('app.patient_records.chat',return_value='{}'):
            r=self.req('/api/sessions/'+sid+'/patient-candidates')
        self.assertEqual(r.status_code,200);self.assertEqual(r.json['candidate'],{});self.assertEqual(r.json['warnings'],[])
