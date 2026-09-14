import copy
import json
from unittest import mock
from tests.base import AppTestCase
from app import db, grounding, soap

TRANSCRIPT = '狗腹泻2天，来院就诊。昨天开始精神较差，今天没有呕吐。兽医说先观察。'
HISTORY = '既往诊断癫痫。长期用药苯巴比妥10mg，每日2次，连用5天。'
SECTIONS = dict(chief_complaint='腹泻2天。', past_history='癫痫。苯巴比妥10mg，每日2次，连用5天（既往病史）。', present_illness='昨天开始精神较差，今天没有呕吐。')
OTHER = dict(objective='未提及', assessment='未提及', plan='先观察。', uncertain=[])
LEGACY_S = '腹泻2天。\n癫痫。苯巴比妥10mg，每日2次，连用5天（既往病史）。\n昨天开始精神较差，今天没有呕吐。'

class SubjectiveSectionsTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.sid = self.new_session(); self.url = '/api/sessions/' + self.sid
        self.set_transcript(self.sid, TRANSCRIPT)
        self.client.put(self.url+'/history-summary', json={'summary':{'medications':HISTORY}})

    def save(self, sections=SECTIONS):
        return self.client.put(self.url+'/note', json={'note':dict(OTHER, subjective_sections=sections)})

    def test_checker_result_identical_to_old_full_s_for_valid_and_invalid_content(self):
        legacy = dict(OTHER, subjective=LEGACY_S)
        new = soap.normalize_note(dict(OTHER, subjective_sections=SECTIONS))
        self.assertEqual(new['subjective'], legacy['subjective'])
        self.assertEqual(grounding.check(TRANSCRIPT, legacy, HISTORY), [])
        self.assertEqual(grounding.check(TRANSCRIPT, legacy, HISTORY), grounding.check(TRANSCRIPT, new, HISTORY))
        bad_sections = dict(chief_complaint='腹泻2天。加用恩诺沙星。', past_history='癫痫。苯巴比妥15mg，每日3次，连用7天（既往病史）。', present_illness='今天呕吐。')
        bad_s = '腹泻2天。加用恩诺沙星。\n癫痫。苯巴比妥15mg，每日3次，连用7天（既往病史）。\n今天呕吐。'
        old_bad = dict(OTHER, subjective=bad_s, objective='体温39.8℃')
        new_bad = soap.normalize_note(dict(OTHER, subjective_sections=bad_sections, objective='体温39.8℃'))
        for history in (HISTORY, ''):
            old_flags = grounding.check(TRANSCRIPT, old_bad, history)
            self.assertEqual(old_flags, grounding.check(TRANSCRIPT, new_bad, history))
            self.assertTrue({'drug','dose','frequency','duration','negation'} <= {f['kind'] for f in old_flags})
            self.assertTrue(all(f['field'] in soap.FIELDS for f in old_flags))
        # Provenance warnings also stay identical after source markers are edited.
        no_marker = {k:v.replace('（既往病史）','') for k,v in SECTIONS.items()}
        old_flags = grounding.check(TRANSCRIPT, dict(OTHER, subjective=LEGACY_S.replace('（既往病史）','')), HISTORY)
        self.assertEqual(old_flags, grounding.check(TRANSCRIPT, soap.normalize_note(dict(OTHER, subjective_sections=no_marker)), HISTORY))
        self.assertIn('source', {f['kind'] for f in old_flags})

    def test_save_ignores_forged_flat_s_and_checks_complete_combined_note_once(self):
        changed = dict(SECTIONS, present_illness='加用恩诺沙星。')
        incoming = dict(OTHER, subjective_sections=changed, subjective='未提及')
        with mock.patch('app.evidence.grounding.check', wraps=grounding.check) as check:
            result = self.client.put(self.url+'/note', json={'note':incoming})
        self.assertEqual(result.status_code, 200); check.assert_called_once()
        checked_note = check.call_args.args[1]
        for text in changed.values():self.assertIn(text, checked_note['subjective'])
        for key in ('objective','assessment','plan'):self.assertEqual(checked_note[key],OTHER[key])
        self.assertIn('drug', {f['kind'] for f in result.json['session']['flags']})

    def test_model_generates_sections_and_summary_background_goes_only_to_past(self):
        payload = dict(OTHER, subjective_sections=dict(SECTIONS, past_history='未提及'), history_background=['癫痫。苯巴比妥10mg，每日2次，连用5天。'])
        with mock.patch('app.soap.chat', return_value=json.dumps(payload)) as model:
            result = self.client.post(self.url+'/note')
        self.assertEqual(result.status_code, 200)
        note = result.json['session']['note']; sections = note['subjective_sections']
        self.assertEqual(sections['chief_complaint'],SECTIONS['chief_complaint'])
        self.assertEqual(sections['present_illness'],SECTIONS['present_illness'])
        self.assertIn('苯巴比妥10mg，每日2次，连用5天（既往病史）。', sections['past_history'])
        self.assertNotIn('苯巴比妥', sections['chief_complaint']+sections['present_illness'])
        for key in ('objective','assessment','plan'):self.assertEqual(note[key],OTHER[key])
        prompt=model.call_args.kwargs['messages'][0]['content']
        self.assertIn('subjective_sections', prompt)
        self.assertIn('不得扩大为“无长期用药史”', prompt)
        self.assertIn('目前未开药', prompt)
        self.assertIn('无腹泻、无便血、未换粮', prompt)
        self.assertEqual(result.json['session']['flags'], [])

    def test_bad_model_shape_preserves_previous_note(self):
        before = self.save().json['session']['note']
        for payload in (dict(OTHER,subjective='旧模型整段输出'), dict(OTHER,subjective_sections={'chief_complaint':'不完整'}), dict(OTHER,subjective_sections=dict(SECTIONS,past_history=[]))):
            with mock.patch('app.soap.chat', return_value=json.dumps(payload)):
                result = self.client.post(self.url+'/note')
            self.assertEqual(result.status_code, 502)
            self.assertEqual(self.client.get(self.url).json['session']['note'], before)

    def test_child_copy_and_full_export_follow_confirmation_gate(self):
        self.save()
        for key in SECTIONS:self.assertEqual(self.client.get(self.url+'/export?field='+key).status_code,409)
        self.client.post(self.url+'/confirm',json={'confirmed_by':'验收兽医'})
        for key,value in SECTIONS.items():self.assertEqual(self.client.get(self.url+'/export?field='+key).json['text'], value)
        exported = self.client.get(self.url+'/export').json['text']
        for label in ('主诉','既往病史','现病史'):self.assertIn('【'+label+'】',exported)
        self.assertIn('（既往病史）', exported)
        result=self.save(dict(SECTIONS,present_illness='加用恩诺沙星。')).json['session']
        self.assertFalse(result['confirmed']);self.assertIn('drug',{f['kind'] for f in result['flags']})
        self.assertEqual(self.client.get(self.url+'/export?field=past_history').status_code,409)

    def test_legacy_confirmed_record_read_and_copy_is_lossless_and_does_not_migrate(self):
        original=dict(OTHER,subjective='旧版原文：主诉、既往和现病史保留原顺序。')
        self.client.put(self.url+'/note',json={'note':original})
        self.client.post(self.url+'/confirm',json={'confirmed_by':'旧版兽医'})
        before=db.get_session(self.sid)
        read=self.client.get(self.url).json['session']
        self.assertTrue(read['confirmed']);self.assertNotIn('subjective_sections', read['note'])
        self.assertEqual(read['note'],original)
        self.assertEqual(self.client.get(self.url+'/export?field=subjective').json['text'],original['subjective'])
        self.assertEqual(self.client.get(self.url+'/export?field=chief_complaint').status_code,400)
        self.assertTrue(self.client.get(self.url+'/export').json['text'].startswith('【S 主观】\n'+original['subjective']))
        after=db.get_session(self.sid)
        for key in ('note_json','flags_json','confirmed_at','note_revision','updated_at'):self.assertEqual(before[key],after[key])

    def test_invalid_manual_sections_cannot_erase_or_downgrade_saved_sections(self):
        before=self.save().json['session']['note']
        for incoming in (dict(OTHER,subjective='旧缓存表单'), dict(OTHER,subjective_sections={}), dict(OTHER,subjective_sections=dict(SECTIONS,chief_complaint=None))):
            result=self.client.put(self.url+'/note',json={'note':incoming})
            self.assertEqual(result.status_code,400)
            self.assertEqual(self.client.get(self.url).json['session']['note'],before)

    def test_editing_history_still_rechecks_combined_s(self):
        self.save();self.client.post(self.url+'/confirm',json={'confirmed_by':'兽医'})
        with mock.patch('app.evidence.grounding.check',wraps=grounding.check) as check:
            result=self.client.put(self.url+'/history-summary',json={'summary':{}})
        check.assert_called_once()
        self.assertEqual(check.call_args.args[1]['subjective'],LEGACY_S)
        self.assertFalse(result.json['session']['confirmed'])
        self.assertIn('drug',{f['kind'] for f in result.json['session']['flags']})
