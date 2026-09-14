import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ENV_KEYS = [
    "ZS_ASR_PROVIDER", "ZS_ASR_MODEL", "ZS_LLM_MODEL", "ZS_LLM_THINKING",
    "ZS_DB_PATH", "ZS_AUDIO_DIR", "ZS_KB_DOCS_DIR",
    "ZS_ASR_BASE_URL", "ZS_ASR_API_KEY", "ZS_LLM_BASE_URL", "ZS_LLM_API_KEY",
    "ZS_KB_MODE", "ZS_KB_MIN_COVERAGE", "ZS_ALLOW_RAW_QUERY_STORAGE",
    "ZS_LOG_RETENTION_DAYS", "ZS_LOG_SALT",
]


class AppTestCase(unittest.TestCase):
    """Each test gets its own sqlite file and a clean config."""
    kb_mode = "placeholder"
    configure_providers = True

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zs_test_")
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)

        os.environ["ZS_DB_PATH"] = os.path.join(self.tmp, "test.db")
        os.environ["ZS_AUDIO_DIR"] = os.path.join(self.tmp, "audio")
        os.environ["ZS_KB_DOCS_DIR"] = os.path.join(self.tmp, "docs")
        os.environ["ZS_KB_MODE"] = self.kb_mode
        os.environ["ZS_LOG_SALT"] = "test-salt"
        if self.configure_providers:
            os.environ["ZS_ASR_BASE_URL"] = "https://example.invalid/v1"
            os.environ["ZS_ASR_API_KEY"] = "test-asr-key"
            os.environ["ZS_LLM_BASE_URL"] = "https://example.invalid/v1"
            os.environ["ZS_LLM_API_KEY"] = "test-llm-key"

        from app.config import reset_config_for_tests
        reset_config_for_tests()

        from app.main import create_app
        self.app = create_app({"TESTING": True})
        self.client = self.app.test_client()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        from app.config import reset_config_for_tests
        reset_config_for_tests()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers ---------------------------------------------------------
    def new_session(self):
        r = self.client.post("/api/sessions")
        return r.get_json()["session"]["id"]

    def set_transcript(self, sid, text):
        return self.client.put(f"/api/sessions/{sid}/transcript", json={"transcript": text})

    def attach_audio(self, sid, name="a.wav", data=b"RIFFfake"):
        import io
        return self.client.post(
            f"/api/sessions/{sid}/audio",
            data={"file": (io.BytesIO(data), name)},
            content_type="multipart/form-data",
        )
