import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import wave

import requests

from tests.base import AppTestCase


def wav_bytes():
    data = io.BytesIO()
    with wave.open(data, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 160)
    return data.getvalue()


class TestDoubao(AppTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ZS_ASR_PROVIDER"] = "doubao"
        os.environ["ZS_ASR_MODEL"] = "doubao-seed-2-0-lite-260428"
        os.environ["ZS_LLM_THINKING"] = "disabled"
        from app.config import reset_config_for_tests
        reset_config_for_tests()
        self.sid = self.new_session()
        self.audio = wav_bytes()
        self.attach_audio(self.sid, data=self.audio)

    def response(self, text="没有呕吐。", finish="stop"):
        return mock.Mock(status_code=200, json=mock.Mock(return_value={
            "choices": [{"message": {"content": text}, "finish_reason": finish}]}))

    def transcribe(self):
        return self.client.post(f"/api/sessions/{self.sid}/transcribe")

    def test_audio_request_and_saved_transcript(self):
        with mock.patch("app.providers.doubao.requests.post", return_value=self.response()) as post:
            r = self.transcribe()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["session"]["transcript"], "没有呕吐。")
        args, kw = post.call_args
        self.assertTrue(args[0].endswith("/chat/completions"))
        content = kw["json"]["messages"][1]["content"][0]
        self.assertEqual(content["type"], "input_audio")
        self.assertEqual(content["input_audio"]["format"], "wav")
        self.assertEqual(base64.b64decode(content["input_audio"]["data"]), self.audio)
        self.assertEqual(kw["json"]["thinking"], {"type": "disabled"})
        from app import db
        with db.connect() as conn:
            logs = str([tuple(row) for row in conn.execute("SELECT payload FROM event_log")])
        self.assertNotIn("没有呕吐", logs)
        self.assertNotIn(content["input_audio"]["data"], logs)

    def test_incomplete_or_invalid_response_preserves_previous_transcript(self):
        self.set_transcript(self.sid, "人工修订的逐字稿")
        cases = [self.response(finish="length"), self.response(finish="content_filter"),
                 self.response(text=""), self.response(text="[未识别到语音]"),
                 self.response(text=["invalid"]),
                 mock.Mock(status_code=200, json=mock.Mock(return_value={"choices": []})),
                 mock.Mock(status_code=200, json=mock.Mock(side_effect=ValueError))]
        for response in cases:
            with self.subTest(response=response):
                with mock.patch("app.providers.doubao.requests.post", return_value=response):
                    self.assertEqual(self.transcribe().status_code, 502)
                from app import db
                self.assertEqual(db.get_session(self.sid)["transcript"], "人工修订的逐字稿")

    def test_provider_errors_are_reported_without_echoing_body(self):
        for status, expected in [(401, 503), (403, 503), (429, 502), (500, 502)]:
            with self.subTest(status=status):
                with mock.patch("app.providers.doubao.requests.post", return_value=mock.Mock(
                        status_code=status, text="secret clinical content")):
                    r = self.transcribe()
                self.assertEqual(r.status_code, expected)
                self.assertNotIn("secret clinical content", r.get_data(as_text=True))

    def test_timeout_is_an_error(self):
        with mock.patch("app.providers.doubao.requests.post", side_effect=requests.Timeout):
            self.assertEqual(self.transcribe().status_code, 502)

    def test_missing_key_never_calls_network(self):
        os.environ.pop("ZS_ASR_API_KEY")
        from app.config import reset_config_for_tests
        reset_config_for_tests()
        with mock.patch("requests.post") as post:
            r = self.transcribe()
        post.assert_not_called()
        self.assertEqual(r.status_code, 503)
        self.assertFalse(r.get_json()["retryable"])

    def test_unsupported_container_never_calls_network(self):
        from app.config import get_config
        from app.providers.doubao import transcribe_audio
        from app.providers import ProviderError
        path = Path(self.tmp) / "recording.webm"
        path.write_bytes(b"webm-data")
        with mock.patch("requests.post") as post:
            with self.assertRaises(ProviderError):
                transcribe_audio(str(path), get_config(), "zh")
        post.assert_not_called()

    def test_oversized_file_never_calls_network(self):
        from app.config import get_config
        from app.providers.doubao import transcribe_audio
        from app.providers import ProviderError
        path = Path(self.tmp) / "large.mp3"
        with path.open("wb") as f:
            f.truncate(20 * 1024 * 1024 + 1)
        with mock.patch("requests.post") as post:
            with self.assertRaises(ProviderError):
                transcribe_audio(str(path), get_config(), "zh")
        post.assert_not_called()

    def test_soap_disables_thinking_and_rejects_truncation(self):
        self.set_transcript(self.sid, "未提及诊断")
        payload = json.dumps({"subjective_sections": {"chief_complaint":"未提及", "past_history":"未提及", "present_illness":"未提及"}, "objective": "未提及",
                              "assessment": "未提及", "plan": "未提及"})
        with mock.patch("app.providers.llm.requests.post", return_value=self.response(payload)) as post:
            r = self.client.post(f"/api/sessions/{self.sid}/note")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.loads(post.call_args.kwargs["data"])["thinking"], {"type": "disabled"})
        with mock.patch("app.providers.llm.requests.post", return_value=self.response(payload, "length")):
            r = self.client.post(f"/api/sessions/{self.sid}/note")
        self.assertEqual(r.status_code, 502)


class TestLocalSetup(unittest.TestCase):
    def test_preserves_other_settings_and_writes_private_file(self):
        from tools.configure_doubao import save_config
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("# preserved\nZS_KB_MODE=off\nZS_LLM_API_KEY=old\nZS_LLM_API_KEY=duplicate\n")
            save_config(path, "test-key")
            content = path.read_text()
            self.assertIn("ZS_KB_MODE=off", content)
            self.assertIn("# preserved", content)
            self.assertEqual(content.count("ZS_LLM_API_KEY="), 1)
            self.assertIn("ZS_ASR_API_KEY=test-key", content)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            before = content
            with self.assertRaises(ValueError):
                save_config(path, "bad\nkey")
            self.assertEqual(path.read_text(), before)
