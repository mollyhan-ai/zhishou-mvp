"""Configuration. Everything comes from environment variables.

Design rule: this module never invents a default that would let the app
*appear* to work without real credentials. If a key is missing, the
relevant feature reports a configuration error upward. See providers/.
"""
import os
from dataclasses import dataclass, field

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


KB_MODES = ("placeholder", "rag", "off")


@dataclass
class Config:
    # --- storage ---
    db_path: str = field(default_factory=lambda: _env("ZS_DB_PATH", os.path.join(BASE_DIR, "data", "zhishou.db")))
    audio_dir: str = field(default_factory=lambda: _env("ZS_AUDIO_DIR", os.path.join(BASE_DIR, "data", "audio")))
    kb_docs_dir: str = field(default_factory=lambda: _env("ZS_KB_DOCS_DIR", os.path.join(BASE_DIR, "kb_docs")))

    # --- ASR provider (OpenAI-compatible /audio/transcriptions) ---
    asr_provider: str = field(default_factory=lambda: _env("ZS_ASR_PROVIDER", "whisper").lower())
    asr_base_url: str = field(default_factory=lambda: _env("ZS_ASR_BASE_URL"))
    asr_api_key: str = field(default_factory=lambda: _env("ZS_ASR_API_KEY"))
    asr_model: str = field(default_factory=lambda: _env("ZS_ASR_MODEL", "whisper-1"))
    asr_language: str = field(default_factory=lambda: _env("ZS_ASR_LANGUAGE", "zh"))
    asr_timeout: int = field(default_factory=lambda: _env_int("ZS_ASR_TIMEOUT", 180))

    # --- LLM provider (OpenAI-compatible /chat/completions) ---
    llm_base_url: str = field(default_factory=lambda: _env("ZS_LLM_BASE_URL"))
    llm_api_key: str = field(default_factory=lambda: _env("ZS_LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("ZS_LLM_MODEL", "qwen2.5-72b-instruct"))
    llm_thinking: str = field(default_factory=lambda: _env("ZS_LLM_THINKING"))
    llm_timeout: int = field(default_factory=lambda: _env_int("ZS_LLM_TIMEOUT", 120))
    llm_temperature: float = field(default_factory=lambda: _env_float("ZS_LLM_TEMPERATURE", 0.1))

    # --- knowledge assistant ---
    kb_mode: str = field(default_factory=lambda: _env("ZS_KB_MODE", "placeholder").lower())
    kb_min_coverage: float = field(default_factory=lambda: _env_float("ZS_KB_MIN_COVERAGE", 0.25))
    kb_top_k: int = field(default_factory=lambda: _env_int("ZS_KB_TOP_K", 5))

    # --- privacy ---
    log_retention_days: int = field(default_factory=lambda: _env_int("ZS_LOG_RETENTION_DAYS", 30))
    allow_raw_query_storage: bool = field(
        default_factory=lambda: _env("ZS_ALLOW_RAW_QUERY_STORAGE", "false").lower() == "true"
    )

    # --- misc ---
    max_audio_mb: int = field(default_factory=lambda: _env_int("ZS_MAX_AUDIO_MB", 100))

    def __post_init__(self):
        if self.asr_provider not in ("whisper", "doubao"):
            raise ValueError("ZS_ASR_PROVIDER 必须为 whisper 或 doubao")
        if self.llm_thinking not in ("", "disabled", "enabled", "auto"):
            raise ValueError("ZS_LLM_THINKING 无效")
        if self.kb_mode not in KB_MODES:
            raise ValueError(
                f"ZS_KB_MODE={self.kb_mode!r} is not valid. Use one of: {', '.join(KB_MODES)}"
            )
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(self.audio_dir, exist_ok=True)
        os.makedirs(self.kb_docs_dir, exist_ok=True)

    # --- readiness, surfaced in the UI banner -------------------------------
    def asr_missing(self):
        missing = []
        if not self.asr_base_url:
            missing.append("ZS_ASR_BASE_URL")
        if not self.asr_api_key:
            missing.append("ZS_ASR_API_KEY")
        return missing

    def llm_missing(self):
        missing = []
        if not self.llm_base_url:
            missing.append("ZS_LLM_BASE_URL")
        if not self.llm_api_key:
            missing.append("ZS_LLM_API_KEY")
        return missing

    def status(self):
        return {
            "asr_provider": self.asr_provider,
            "asr_ready": not self.asr_missing(),
            "asr_missing": self.asr_missing(),
            "asr_model": self.asr_model if not self.asr_missing() else None,
            "llm_ready": not self.llm_missing(),
            "llm_missing": self.llm_missing(),
            "llm_model": self.llm_model if not self.llm_missing() else None,
            "kb_mode": self.kb_mode,
            "log_retention_days": self.log_retention_days,
            "allow_raw_query_storage": self.allow_raw_query_storage,
        }


_config = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def reset_config_for_tests():
    global _config
    _config = None


def apply_provider_settings(values):
    """Replace the snapshot only after its .env file was durably written."""
    from dataclasses import replace
    global _config
    cfg = get_config()
    _config = replace(cfg,
        asr_provider="doubao", asr_base_url=values["ZS_ASR_BASE_URL"],
        asr_api_key=values["ZS_ASR_API_KEY"], asr_model=values["ZS_ASR_MODEL"],
        asr_language="zh", asr_timeout=180,
        llm_base_url=values["ZS_LLM_BASE_URL"], llm_api_key=values["ZS_LLM_API_KEY"],
        llm_model=values["ZS_LLM_MODEL"], llm_thinking="disabled", llm_timeout=120)
    os.environ.update(values)
