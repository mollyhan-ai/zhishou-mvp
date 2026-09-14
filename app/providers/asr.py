"""Speech-to-text.

Talks to any OpenAI-compatible `/audio/transcriptions` endpoint, so the same
code points at a hosted API or at a self-hosted Whisper/FunASR gateway inside
the hospital network. Swap by changing ZS_ASR_BASE_URL.

If credentials are absent this raises ConfigError. It never returns text.
"""
import os

import requests

from . import ConfigError, ProviderError
from ..config import get_config


def transcribe(audio_path: str, language: str = None) -> dict:
    cfg = get_config()
    missing = cfg.asr_missing()
    if missing:
        raise ConfigError(
            "语音转写未配置：缺少环境变量 " + ", ".join(missing) +
            "。请在 .env 中填入后重启服务。",
            missing=missing,
        )
    if not os.path.exists(audio_path):
        raise ProviderError("音频文件不存在或已被删除。")

    if cfg.asr_provider == "doubao":
        from .doubao import transcribe_audio
        return transcribe_audio(audio_path, cfg, language or cfg.asr_language)

    url = cfg.asr_base_url.rstrip("/") + "/audio/transcriptions"
    headers = {"Authorization": f"Bearer {cfg.asr_api_key}"}
    data = {
        "model": cfg.asr_model,
        "language": language or cfg.asr_language,
        "response_format": "verbose_json",
    }
    try:
        with open(audio_path, "rb") as fh:
            files = {"file": (os.path.basename(audio_path), fh, "application/octet-stream")}
            resp = requests.post(url, headers=headers, data=data, files=files,
                                 timeout=cfg.asr_timeout)
    except requests.Timeout:
        raise ProviderError(f"语音转写超时（{cfg.asr_timeout}s）。可重试或换用更短的音频。")
    except requests.RequestException as exc:
        raise ProviderError("无法连接语音转写服务。", detail=str(exc))

    if resp.status_code == 401:
        raise ConfigError("语音转写鉴权失败：ZS_ASR_API_KEY 无效。", missing=["ZS_ASR_API_KEY"])
    if resp.status_code >= 400:
        raise ProviderError(
            f"语音转写服务返回 {resp.status_code}。",
            detail=resp.text[:500], status=resp.status_code,
        )

    try:
        payload = resp.json()
    except ValueError:
        raise ProviderError("语音转写返回了无法解析的内容。", detail=resp.text[:500])

    text = (payload.get("text") or "").strip()
    if not text:
        raise ProviderError("语音转写返回了空结果。请确认音频中有可识别的语音。")

    segments = []
    for seg in payload.get("segments") or []:
        segments.append({
            "start": seg.get("start"),
            "end": seg.get("end"),
            "text": (seg.get("text") or "").strip(),
        })
    return {"text": text, "segments": segments, "model": cfg.asr_model}
