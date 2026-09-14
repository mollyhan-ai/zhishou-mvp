import unittest
from unittest import mock

from tests.base import AppTestCase

TRANSCRIPT = "宠主说狗拉稀两天了，没有呕吐。兽医说给甲硝唑10mg，一日两次，连用5天。"

GOOD_NOTE_JSON = (
    '{"subjective_sections":{"chief_complaint":"宠主诉腹泻两天，无呕吐。","past_history":"未提及","present_illness":"未提及"},'
    '"objective":"未提及",'
    '"assessment":"未提及",'
    '"plan":"甲硝唑10mg，一日两次，连用5天。",'
    '"uncertain":[]}'
)
HALLUCINATED_NOTE_JSON = (
    '{"subjective_sections":{"chief_complaint":"宠主诉腹泻两天，呕吐。","past_history":"未提及","present_illness":"未提及"},'
    '"objective":"体温39.8℃",'
    '"assessment":"未提及",'
    '"plan":"甲硝唑15mg/kg，连用7天，加用恩诺沙星。",'
    '"uncertain":[]}'
)


class TestConfigErrors(AppTestCase):
    """Missing credentials must surface as a config error -- never as a
    fabricated success."""
    configure_providers = False

    def test_status_reports_missing_keys(self):
        s = self.client.get("/api/status").get_json()
        self.assertFalse(s["asr_ready"])
        self.assertFalse(s["llm_ready"])
        self.assertIn("ZS_ASR_API_KEY", s["asr_missing"])
        self.assertIn("ZS_LLM_BASE_URL", s["llm_missing"])

    def test_transcribe_without_key_returns_503_config(self):
        sid = self.new_session()
        self.attach_audio(sid)
        r = self.client.post(f"/api/sessions/{sid}/transcribe")
        self.assertEqual(r.status_code, 503)
        body = r.get_json()
        self.assertEqual(body["error_kind"], "config")
        self.assertFalse(body["retryable"])
        self.assertIn("ZS_ASR_API_KEY", body["missing"])
        # and nothing fake was written
        sess = self.client.get(f"/api/sessions/{sid}").get_json()["session"]
        self.assertIsNone(sess["transcript"])
        self.assertEqual(sess["status"], "failed")
        self.assertEqual(sess["error_kind"], "config")

    def test_generate_without_key_returns_503_config(self):
        sid = self.new_session()
        self.set_transcript(sid, TRANSCRIPT)
        r = self.client.post(f"/api/sessions/{sid}/note")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["error_kind"], "config")
        sess = self.client.get(f"/api/sessions/{sid}").get_json()["session"]
        self.assertIsNone(sess["note"])


class TestProviderErrors(AppTestCase):

    def test_provider_failure_is_retryable(self):
        from app.providers import ProviderError
        sid = self.new_session()
        self.attach_audio(sid)
        with mock.patch("app.providers.asr.transcribe",
                        side_effect=ProviderError("语音转写超时（180s）。")):
            r = self.client.post(f"/api/sessions/{sid}/transcribe")
        self.assertEqual(r.status_code, 502)
        body = r.get_json()
        self.assertEqual(body["error_kind"], "provider")
        self.assertTrue(body["retryable"])

    def test_retry_after_failure_succeeds(self):
        from app.providers import ProviderError
        sid = self.new_session()
        self.attach_audio(sid)
        with mock.patch("app.providers.asr.transcribe",
                        side_effect=ProviderError("临时故障")):
            self.client.post(f"/api/sessions/{sid}/transcribe")
        with mock.patch("app.providers.asr.transcribe",
                        return_value={"text": TRANSCRIPT, "segments": [], "model": "m"}):
            r = self.client.post(f"/api/sessions/{sid}/transcribe")
        self.assertEqual(r.status_code, 200)
        sess = r.get_json()["session"]
        self.assertEqual(sess["status"], "transcribed")
        self.assertEqual(sess["transcript"], TRANSCRIPT)

    def test_bad_json_from_model_is_an_error_not_a_note(self):
        sid = self.new_session()
        self.set_transcript(sid, TRANSCRIPT)
        with mock.patch("app.soap.chat", return_value="抱歉，我无法完成。"):
            r = self.client.post(f"/api/sessions/{sid}/note")
        self.assertEqual(r.status_code, 502)
        sess = self.client.get(f"/api/sessions/{sid}").get_json()["session"]
        self.assertIsNone(sess["note"])


class TestScribeHappyPath(AppTestCase):

    def _draft(self, note_json=GOOD_NOTE_JSON):
        sid = self.new_session()
        self.attach_audio(sid)
        with mock.patch("app.providers.asr.transcribe",
                        return_value={"text": TRANSCRIPT, "segments": [], "model": "m"}):
            self.client.post(f"/api/sessions/{sid}/transcribe")
        with mock.patch("app.soap.chat", return_value=note_json):
            r = self.client.post(f"/api/sessions/{sid}/note")
        return sid, r.get_json()["session"]

    def test_full_flow_produces_note_and_empty_checklist(self):
        sid, sess = self._draft()
        self.assertEqual(sess["status"], "drafted")
        self.assertEqual(sess["note"]["plan"], "甲硝唑10mg，一日两次，连用5天。")
        self.assertEqual(sess["flag_summary"]["total"], 0)
        self.assertFalse(sess["confirmed"])

    def test_hallucinated_content_is_flagged(self):
        sid, sess = self._draft(HALLUCINATED_NOTE_JSON)
        kinds = {f["kind"] for f in sess["flags"]}
        self.assertIn("drug", kinds)        # 恩诺沙星 not in transcript
        self.assertIn("dose", kinds)        # 15mg/kg, 39.8℃ not in transcript
        self.assertIn("duration", kinds)    # 7天 not in transcript
        self.assertIn("negation", kinds)    # transcript said 没有呕吐
        self.assertGreater(sess["flag_summary"]["high"], 0)

    def test_export_blocked_before_confirmation(self):
        sid, _ = self._draft()
        r = self.client.get(f"/api/sessions/{sid}/export")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error_kind"], "not_confirmed")

    def test_confirm_requires_a_named_person(self):
        sid, _ = self._draft()
        r = self.client.post(f"/api/sessions/{sid}/confirm", json={"confirmed_by": "  "})
        self.assertEqual(r.status_code, 400)

    def test_export_allowed_after_confirmation(self):
        sid, _ = self._draft()
        self.client.post(f"/api/sessions/{sid}/confirm", json={"confirmed_by": "韩医生"})
        r = self.client.get(f"/api/sessions/{sid}/export")
        self.assertEqual(r.status_code, 200)
        text = r.get_json()["text"]
        self.assertIn("S 主观", text)
        self.assertIn("韩医生", text)

    def test_editing_note_revokes_confirmation_and_rechecks(self):
        sid, sess = self._draft()
        self.client.post(f"/api/sessions/{sid}/confirm", json={"confirmed_by": "韩医生"})
        note = dict(sess["note"])
        note["plan"] = "甲硝唑10mg，一日两次，连用5天。另加恩诺沙星。"
        r = self.client.put(f"/api/sessions/{sid}/note", json={"note": note})
        edited = r.get_json()["session"]
        self.assertFalse(edited["confirmed"])
        self.assertIn("drug", {f["kind"] for f in edited["flags"]})
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/export").status_code, 409)

    def test_empty_field_is_normalised_to_weitiji(self):
        sid = self.new_session()
        self.set_transcript(sid, TRANSCRIPT)
        with mock.patch("app.soap.chat",
                        return_value='{"subjective_sections":{"chief_complaint":"腹泻","past_history":"","present_illness":""},"objective":"","assessment":"","plan":""}'):
            r = self.client.post(f"/api/sessions/{sid}/note")
        note = r.get_json()["session"]["note"]
        self.assertEqual(note["objective"], "未提及")
        self.assertEqual(note["plan"], "未提及")

    def test_generate_without_transcript_is_rejected(self):
        sid = self.new_session()
        r = self.client.post(f"/api/sessions/{sid}/note")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error_kind"], "input")


class TestDeletion(AppTestCase):

    def test_delete_parts_and_whole_session(self):
        sid = self.new_session()
        self.attach_audio(sid)
        self.set_transcript(sid, TRANSCRIPT)
        with mock.patch("app.soap.chat", return_value=GOOD_NOTE_JSON):
            self.client.post(f"/api/sessions/{sid}/note")

        s = self.client.delete(f"/api/sessions/{sid}/note").get_json()["session"]
        self.assertIsNone(s["note"])
        s = self.client.delete(f"/api/sessions/{sid}/transcript").get_json()["session"]
        self.assertIsNone(s["transcript"])
        s = self.client.delete(f"/api/sessions/{sid}/audio").get_json()["session"]
        self.assertFalse(s["has_audio"])

        self.assertEqual(self.client.delete(f"/api/sessions/{sid}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/sessions/{sid}").status_code, 404)

    def test_deleting_audio_removes_the_file(self):
        import os
        from app import db
        sid = self.new_session()
        self.attach_audio(sid)
        path = db.get_session(sid)["audio_path"]
        self.assertTrue(os.path.exists(path))
        self.client.delete(f"/api/sessions/{sid}/audio")
        self.assertFalse(os.path.exists(path))

    def test_unknown_part_rejected(self):
        sid = self.new_session()
        self.assertEqual(self.client.delete(f"/api/sessions/{sid}/everything").status_code, 400)


if __name__ == "__main__":
    unittest.main()
