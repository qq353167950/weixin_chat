#!/usr/bin/env python3
"""写作 / 选题整理 专用大模型（Chat）。

只读 LLM_* 配置，不与生图、搜索共用密钥。
每次请求使用短生命周期 Session，结束后强制关闭连接。

默认流式（LLM_STREAM=1）：长文写作可能超过 100 秒，中转站（Cloudflare 等）
对无数据的长连接会返回 HTTP 524 网关超时；流式让分块持续到达，连接不会被掐。
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Iterable

from http_util import request_json, with_session
from task_hooks import check_cancelled, report_progress


def llm_config() -> dict[str, str]:
    api_key = os.getenv("LLM_API_KEY", "").strip()
    base = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    return {"api_key": api_key, "base": base, "model": model}


def parse_sse_content(lines: Iterable[str]) -> str:
    """从 SSE 行流拼出 chat.completion.chunk 的 delta 内容（纯函数，便于自测）。

    每个分块都是取消探针点：GUI 里点「取消」秒级生效，
    同时把累计字数上报为实时进度。
    """
    parts: list[str] = []
    total = 0
    for raw in lines:
        check_cancelled()
        if not raw or not raw.startswith("data:"):
            continue
        data = raw[5:].strip()
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
            delta = (obj.get("choices") or [{}])[0].get("delta") or {}
            piece = delta.get("content") or ""
        except Exception:
            continue
        if piece:
            parts.append(piece)
            total += len(piece)
            report_progress(f"已生成 {total} 字…")
    return "".join(parts)


def _chat_stream(url: str, headers: dict, payload: dict, timeout: int, retries: int) -> str:
    """流式请求：read 超时按“相邻分块间隔”计，总时长不受网关空闲超时限制。"""
    payload = dict(payload)
    payload["stream"] = True
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        check_cancelled()
        try:
            def _do(sess):
                with sess.post(
                    url, headers=headers, json=payload,
                    timeout=(20, timeout), stream=True,
                ) as r:
                    if r.status_code >= 400:
                        body = re.sub(r"<[^>]+>", " ", r.text or "")[:200]
                        raise RuntimeError(f"HTTP {r.status_code}: {body.strip()}")
                    # SSE 响应头常不带 charset，requests 会退到 latin-1 →
                    # 中文变「ä½ å¥½」式乱码；OpenAI 协议明确是 UTF-8，强制指定
                    r.encoding = "utf-8"
                    text = parse_sse_content(r.iter_lines(decode_unicode=True))
                if not text.strip():
                    raise RuntimeError("流式响应为空")
                return text.strip()

            return with_session(_do)
        except Exception as e:
            last_err = e
            if attempt < retries:
                print(f"[写作大模型] 流式重试 {attempt}/{retries}: {e}")
                time.sleep(1.5 * attempt)
                continue
    raise RuntimeError(f"流式请求失败: {last_err}")


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
    retries = int(os.getenv("LLM_HTTP_RETRIES", "3") or 3)

    # 默认流式；个别中转不支持时自动回退非流式（LLM_STREAM=0 可强制关闭）
    if os.getenv("LLM_STREAM", "1") != "0":
        try:
            return _chat_stream(url, headers, payload, timeout, retries)
        except Exception as e:
            print(f"[写作大模型] 流式不可用（{e}），回退非流式")

    data = request_json(
        "POST",
        url,
        headers=headers,
        json_body=payload,
        timeout=timeout,
        retries=retries,
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
