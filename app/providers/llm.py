"""Text generation.

OpenAI-compatible `/chat/completions`. Point ZS_LLM_BASE_URL at a hosted API,
at vLLM, or at an on-prem gateway serving Qwen / Baichuan. Same code path.
"""
import json

import requests

from . import ConfigError, ProviderError
from ..config import get_config


def chat(messages, temperature=None, max_tokens=2048, response_json=False) -> str:
    cfg = get_config()
    missing = cfg.llm_missing()
    if missing:
        raise ConfigError(
            "大模型未配置：缺少环境变量 " + ", ".join(missing) +
            "。请在 .env 中填入后重启服务。",
            missing=missing,
        )

    url = cfg.llm_base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": cfg.llm_model,
        "messages": messages,
        "temperature": cfg.llm_temperature if temperature is None else temperature,
        "max_tokens": max_tokens,
    }
    if cfg.llm_thinking:
        body["thinking"] = {"type": cfg.llm_thinking}
    if response_json:
        body["response_format"] = {"type": "json_object"}

    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {cfg.llm_api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=cfg.llm_timeout,
        )
    except requests.Timeout:
        raise ProviderError(f"大模型请求超时（{cfg.llm_timeout}s）。可重试。")
    except requests.RequestException as exc:
        raise ProviderError("无法连接大模型服务。", detail=str(exc))

    if resp.status_code == 401:
        raise ConfigError("大模型鉴权失败：ZS_LLM_API_KEY 无效。", missing=["ZS_LLM_API_KEY"])
    if resp.status_code >= 400:
        raise ProviderError(
            f"大模型服务返回 {resp.status_code}。",
            detail=resp.text[:500], status=resp.status_code,
        )

    try:
        payload = resp.json()
        choice = payload["choices"][0]
        if choice.get("finish_reason") not in (None, "stop"):
            raise ProviderError("病历生成未完整结束，请缩短逐字稿后重试。")
        content = choice["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Invalid content")
    except (ValueError, KeyError, IndexError, TypeError):
        raise ProviderError("大模型返回了无法解析的结构。", detail=resp.text[:500])

    if not (content or "").strip():
        raise ProviderError("大模型返回了空内容。可重试。")
    return content
