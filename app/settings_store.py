"""Atomic, private storage shared by local web settings and CLI."""
import os
import tempfile

MODEL = "doubao-seed-2-0-lite-260428"
BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


def save_config(path, key):
    key = key.strip()
    if not key or any(c.isspace() or c in "\"'" for c in key):
        raise ValueError("密钥不能为空，也不能含空白或引号。请粘贴完整 API Key。")
    values = {
        "ZS_ASR_PROVIDER": "doubao",
        "ZS_ASR_BASE_URL": BASE_URL,
        "ZS_ASR_API_KEY": key,
        "ZS_ASR_MODEL": MODEL,
        "ZS_ASR_LANGUAGE": "zh",
        "ZS_ASR_TIMEOUT": "180",
        "ZS_LLM_BASE_URL": BASE_URL,
        "ZS_LLM_API_KEY": key,
        "ZS_LLM_MODEL": MODEL,
        "ZS_LLM_THINKING": "disabled",
        "ZS_LLM_TIMEOUT": "120",
    }
    # Preserve unrelated settings; remove duplicate provider assignments.
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = [line for line in old.splitlines()
             if line.split("=", 1)[0].strip() not in values]
    text = "\n".join(lines + [f"{k}={v}" for k, v in values.items()]) + "\n"
    fd, name = tempfile.mkstemp(prefix=".env-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return values
