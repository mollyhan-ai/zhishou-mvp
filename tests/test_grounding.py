import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import grounding  # noqa: E402


def kinds(flags):
    return sorted({f["kind"] for f in flags})


def terms(flags, kind):
    return sorted(f["term"] for f in flags if f["kind"] == kind)


class TestGrounding(unittest.TestCase):

    def test_clean_note_has_no_flags(self):
        t = "兽医说给甲硝唑10mg，一日两次，连用5天。宠主说有腹泻。"
        note = {
            "subjective": "宠主诉腹泻。",
            "objective": "未提及",
            "assessment": "未提及",
            "plan": "甲硝唑10mg，一日两次，连用5天。",
        }
        self.assertEqual(grounding.check(t, note), [])

    def test_drug_not_in_transcript_is_flagged(self):
        t = "宠主说狗有点拉肚子，兽医建议先观察。"
        note = {"subjective": "腹泻", "objective": "未提及",
                "assessment": "未提及", "plan": "口服甲硝唑。"}
        flags = grounding.check(t, note)
        self.assertIn("drug", kinds(flags))
        self.assertEqual(terms(flags, "drug"), ["甲硝唑"])
        self.assertEqual([f["severity"] for f in flags if f["kind"] == "drug"], ["high"])

    def test_dose_not_in_transcript_is_flagged(self):
        t = "兽医说给甲硝唑，剂量回头再定。"
        note = {"subjective": "未提及", "objective": "未提及",
                "assessment": "未提及", "plan": "甲硝唑 15mg/kg。"}
        flags = grounding.check(t, note)
        self.assertIn("dose", kinds(flags))
        self.assertIn("15mg/kg", terms(flags, "dose"))

    def test_dose_present_in_transcript_passes(self):
        t = "兽医说甲硝唑按15mg/kg给。"
        note = {"subjective": "未提及", "objective": "未提及",
                "assessment": "未提及", "plan": "甲硝唑 15mg/kg。"}
        self.assertNotIn("dose", kinds(grounding.check(t, note)))

    def test_unit_alias_and_fullwidth_are_normalised(self):
        t = "兽医说给１０毫克。"          # full-width digits + Chinese unit
        note = {"subjective": "未提及", "objective": "未提及",
                "assessment": "未提及", "plan": "给药 10mg。"}
        self.assertNotIn("dose", kinds(grounding.check(t, note)))

    def test_frequency_not_in_transcript_is_flagged(self):
        t = "兽医说这个药先吃着。"
        note = {"subjective": "未提及", "objective": "未提及",
                "assessment": "未提及", "plan": "每日3次口服。"}
        self.assertIn("frequency", kinds(grounding.check(t, note)))

    def test_duration_not_in_transcript_is_flagged(self):
        t = "兽医说先吃着看看。"
        note = {"subjective": "未提及", "objective": "未提及",
                "assessment": "未提及", "plan": "连用7天。"}
        self.assertIn("duration", kinds(grounding.check(t, note)))

    def test_negation_flip_is_flagged(self):
        t = "宠主说没有呕吐，只是拉稀。"
        note = {"subjective": "呕吐，腹泻。", "objective": "未提及",
                "assessment": "未提及", "plan": "未提及"}
        flags = grounding.check(t, note)
        self.assertIn("negation", kinds(flags))
        self.assertEqual(terms(flags, "negation"), ["呕吐"])

    def test_negation_preserved_passes(self):
        t = "宠主说没有呕吐，只是拉稀。"
        note = {"subjective": "无呕吐，有腹泻。", "objective": "未提及",
                "assessment": "未提及", "plan": "未提及"}
        self.assertNotIn("negation", kinds(grounding.check(t, note)))

    def test_empty_field_is_flagged_as_placeholder(self):
        t = "随便说点什么。"
        note = {"subjective": "", "objective": "未提及",
                "assessment": "未提及", "plan": "未提及"}
        flags = grounding.check(t, note)
        self.assertIn("placeholder", kinds(flags))

    def test_weimentioned_marker_is_accepted(self):
        t = "宠主说狗拉稀两天了。"
        note = {"subjective": "腹泻两天。", "objective": "未提及",
                "assessment": "未提及", "plan": "未提及"}
        self.assertEqual(grounding.check(t, note), [])

    def test_high_severity_sorted_first(self):
        t = "宠主说狗不舒服。"
        note = {"subjective": "", "objective": "未提及",
                "assessment": "未提及", "plan": "呋塞米 2mg/kg。"}
        flags = grounding.check(t, note)
        self.assertEqual(flags[0]["severity"], "high")
        self.assertEqual(flags[-1]["severity"], "low")

    def test_summarize_counts(self):
        t = "宠主说狗不舒服。"
        note = {"subjective": "未提及", "objective": "未提及",
                "assessment": "未提及", "plan": "呋塞米 2mg/kg 每日2次。"}
        s = grounding.summarize(grounding.check(t, note))
        self.assertGreaterEqual(s["total"], 3)
        self.assertGreaterEqual(s["high"], 3)

    def test_find_spans(self):
        self.assertEqual(grounding.find_spans("给了甲硝唑和甲硝唑", "甲硝唑"),
                         [(2, 5), (6, 9)])


if __name__ == "__main__":
    unittest.main()

class TestDualSourceGrounding(unittest.TestCase):
    def note(self, text):
        return dict(subjective=text, objective='未提及', assessment='未提及', plan='未提及')

    def test_empty_history_is_exact_legacy_behavior(self):
        note = self.note('呕吐，甲硝唑15mg/kg，每日3次，连用7天。')
        self.assertEqual(grounding.check('无呕吐。', note), grounding._check_single('无呕吐。', note))
        self.assertEqual(grounding.check('无呕吐。', note, ' \n'), grounding._check_single('无呕吐。', note))

    def test_history_medications_and_diagnosis_are_legal_evidence(self):
        history = '长期用药：苯巴比妥10mg，每日2次，连用7天。\n慢性病：癫痫。'
        note = self.note('癫痫；苯巴比妥10mg，每日2次，连用7天（既往病史）。')
        self.assertEqual(grounding.check('本次复查。', note, history), [])

    def test_both_missing_still_flagged_even_with_history_marker(self):
        note = self.note('恩诺沙星25mg/kg，每日3次，连用7天（既往病史）。')
        flags = grounding.check('本次复查。', note, '苯巴比妥10mg，每日2次。')
        self.assertTrue({'drug','dose','frequency','duration'} <= set(kinds(flags)))
        self.assertTrue(all('均' in f['message'] for f in flags))

    def test_history_only_unlabelled_token_is_source_issue_not_hallucination(self):
        flags = grounding.check('本次复查。', self.note('苯巴比妥10mg。'), '长期用药：苯巴比妥10mg。')
        self.assertEqual(kinds(flags), ['source'])

    def test_history_positive_does_not_cancel_current_negation(self):
        flags = grounding.check('今天没有呕吐。', self.note('今天呕吐。'), '以前呕吐。')
        self.assertIn('negation', kinds(flags))
        self.assertEqual(next(f for f in flags if f['kind']=='negation')['sources'], ['transcript'])

    def test_labelled_historical_positive_is_not_current_negation_flip(self):
        self.assertEqual(grounding.check('今天没有呕吐。', self.note('今天无呕吐。以前呕吐（既往病史）。'), '以前呕吐。'), [])

    def test_history_negation_is_checked(self):
        flags = grounding.check('本次复查。', self.note('呕吐（既往病史）。'), '此前没有呕吐。')
        self.assertIn('negation', kinds(flags))
        self.assertEqual(flags[0]['sources'], ['history_summary'])

    def test_current_positive_not_negated_by_old_record(self):
        self.assertEqual(grounding.check('今天呕吐。', self.note('今天呕吐。此前无呕吐（既往病史）。'), '此前无呕吐。'), [])

    def test_no_tokens_fabricated_across_source_boundary(self):
        flags = grounding.check('给药10', self.note('给药10mg。'), 'mg是单位。')
        self.assertIn('dose', kinds(flags))

    def test_history_normalization_and_repeatability(self):
        note = self.note('苯巴比妥10mg（既往病史）。')
        history = '苯巴比妥１０毫克。'
        self.assertEqual(grounding.check('复查。', note, history), [])
        self.assertEqual(grounding.check('复查。', note, history), grounding.check('复查。', note, history))


class TestMixedHistorySource(unittest.TestCase):
    def test_historical_marker_cannot_hide_current_negation(self):
        note = dict(subjective='今天呕吐，去年也呕吐（既往病史）。', objective='未提及', assessment='未提及', plan='未提及')
        flags = grounding.check('今天没有呕吐。', note, '去年呕吐。')
        self.assertIn('source', kinds(flags))
        self.assertIn('negation', kinds(flags))
