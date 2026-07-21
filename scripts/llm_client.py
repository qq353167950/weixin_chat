#!/usr/bin/env python3
"""写作 / 选题整理 专用大模型（Chat）。

只读 LLM_* 配置，不与生图、搜索共用密钥。
每次请求使用短生命周期 Session，结束后强制关闭连接。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from http_util import request_json


def llm_config() -> dict[str, str]:
    api_key = os.getenv("LLM_API_KEY", "").strip()
    base = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    return {"api_key": api_key, "base": base, "model": model}


def llm_chat(
    messages: list[dict[str, str]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    cfg = llm_config()
    if not cfg["api_key"]:
        raise RuntimeError(
            "未配置【写作大模型】密钥。\n"
            "请在 .env 单独填写（与生图/搜索无关）：\n"
            "  LLM_API_KEY=你的写作模型Key\n"
            "  LLM_BASE_URL=https://api.openai.com/v1\n"
            "  LLM_MODEL=gpt-4o-mini"
        )
    if temperature is None:
        temperature = float(os.getenv("LLM_TEMPERATURE", "0.7") or 0.7)
    url = f"{cfg['base']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
        "Connection": "close",
    }
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    print(f"[写作大模型] model={cfg['model']} base={cfg['base']}")
    timeout = int(os.getenv("LLM_TIMEOUT_SEC", "180") or 180)
    data = request_json(
        "POST",
        url,
        headers=headers,
        json_body=payload,
        timeout=timeout,
        retries=int(os.getenv("LLM_HTTP_RETRIES", "3") or 3),
    )
    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        raise RuntimeError(f"写作大模型返回异常: {data}") from e


def llm_json(messages: list[dict[str, str]], temperature: float = 0.4) -> Any:
    text = llm_chat(messages, temperature=temperature)
    text = text.strip()
    if text.startswith("```"):
        text = re_strip_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\[.*\]|\{.*\})", text, flags=re.S)
        if not m:
            raise RuntimeError(f"无法解析 JSON：{text[:500]}")
        return json.loads(m.group(1))


def re_strip_fence(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
    text = re.sub(r"\s*```$", "", text.strip())
    return text
