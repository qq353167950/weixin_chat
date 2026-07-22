#!/usr/bin/env python3
"""AI-generate a WeChat cover image from article title/content.

Providers (set COVER_PROVIDER in .env):
  - openai      : OpenAI Images API (DALL·E 3 / gpt-image) or any OpenAI-compatible endpoint
  - dashscope   : 阿里云百炼 通义万相 (DashScope)
  - template    : local Pillow template (no API, fallback)

Recommended WeChat size after process: 900x383 (~2.35:1)
Pipeline:
  1) Build prompt from title (+ optional md abstract)
  2) Call image model -> download image
  3) Crop/resize to 900x383
  4) Optional: overlay Chinese title (AI text is often broken)

Usage:
  python generate_cover_ai.py --md samples/demo.md --out samples/cover.jpg
  python generate_cover_ai.py --title "标题" --provider openai --out cover.jpg
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from PIL import Image, ImageDraw

# 复用本地模板封面模块：字体查找 / 标题换行 / 取题逻辑的唯一来源
sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_cover import (
    extract_title_from_md,
    find_font,
    generate_cover as template_generate_cover,
    wrap_title,
)

TARGET_SIZE = (900, 383)


def load_env() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    load_dotenv()


def extract_abstract(md_path: Path, limit: int = 120) -> str:
    text = md_path.read_text(encoding="utf-8")
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        s = re.sub(r"[>*`\-\[\]\(\)!#]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        if s:
            lines.append(s)
        if sum(len(x) for x in lines) >= limit:
            break
    return " ".join(lines)[:limit]


def build_prompt(title: str, abstract: str = "", style: str = "editorial") -> str:
    """Prompt for BACKGROUND only. No Chinese characters in the image."""
    styles = {
        "editorial": "modern editorial magazine cover illustration, clean composition, soft cinematic lighting",
        "tech": "futuristic tech illustration, deep blue purple gradient, subtle data particles, clean modern",
        "warm": "warm lifestyle photography style, soft sunlight, shallow depth of field, cozy atmosphere",
        "business": "professional business concept art, minimal geometric shapes, premium flat design",
        "nature": "atmospheric landscape concept art, painterly light, calm premium feel",
    }
    style_text = styles.get(style, styles["editorial"])
    topic = title
    if abstract:
        topic = f"{title}. Context: {abstract}"

    prompt = (
        f"Create a horizontal WeChat public-account cover BACKGROUND image. "
        f"Theme: {topic}. "
        f"Style: {style_text}. "
        f"Wide cinematic 2.35:1 composition, leave some clean dark/soft area for later title overlay, "
        f"high quality, sharp, no watermark, no logo, "
        f"NO text, NO letters, NO Chinese characters, NO words, NO numbers, NO UI mockups."
    )
    return prompt[:1800]


def fit_cover(img: Image.Image, size: tuple[int, int] = TARGET_SIZE) -> Image.Image:
    """Center-crop to target aspect then resize."""
    tw, th = size
    target_ratio = tw / th
    w, h = img.size
    ratio = w / h
    if ratio > target_ratio:
        # too wide
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    return img.resize(size, Image.Resampling.LANCZOS)


def overlay_title(img: Image.Image, title: str) -> Image.Image:
    img = img.convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size

    # dark gradient bottom for readability
    for y in range(h // 2, h):
        alpha = int(180 * ((y - h // 2) / (h // 2)))
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

    font = find_font(42)
    lines = wrap_title(title, font, draw, w - 80)
    # measure
    heights = []
    for line in lines:
        b = draw.textbbox((0, 0), line, font=font)
        heights.append(b[3] - b[1])
    gap = 10
    block_h = sum(heights) + gap * (len(lines) - 1)
    y = h - block_h - 36
    for i, line in enumerate(lines):
        draw.text((42, y + 2), line, font=font, fill=(0, 0, 0, 200))
        draw.text((40, y), line, font=font, fill=(255, 255, 255, 255))
        y += heights[i] + gap
    return img.convert("RGB")


def download_image(url: str, timeout: int = 60) -> Image.Image:
    from http_util import request_bytes

    content = request_bytes("GET", url, timeout=timeout)
    return Image.open(BytesIO(content)).convert("RGB")


# ---------------- OpenAI-compatible image API ----------------
def gen_openai(prompt: str) -> Image.Image:
    """Uses IMAGE_* only (never LLM_* / SEARCH_*)."""
    from http_util import request_json

    api_key = os.getenv("IMAGE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "未配置【生图】密钥 IMAGE_API_KEY。\n"
            "生图与写作/搜索已分开，请在 .env 填写：\n"
            "  IMAGE_PROVIDER=openai\n"
            "  IMAGE_API_KEY=你的生图Key\n"
            "  IMAGE_BASE_URL=https://api.openai.com/v1\n"
            "  IMAGE_MODEL=dall-e-3\n"
            "  IMAGE_SIZE=1792x1024"
        )

    base = os.getenv("IMAGE_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    model = os.getenv("IMAGE_MODEL", "dall-e-3").strip() or "dall-e-3"
    size = os.getenv("IMAGE_SIZE", "1792x1024").strip() or "1792x1024"
    url = f"{base}/images/generations"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Connection": "close",
    }
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
    }
    if "dall-e" in model.lower():
        payload["response_format"] = "url"

    print(f"[生图] provider=openai model={model} size={size} endpoint={url}")
    data = request_json("POST", url, headers=headers, json_body=payload, timeout=120)

    item = (data.get("data") or [None])[0]
    if not item:
        raise RuntimeError(f"生图返回为空: {data}")
    if item.get("url"):
        return download_image(item["url"])
    if item.get("b64_json"):
        import base64

        raw = base64.b64decode(item["b64_json"])
        return Image.open(BytesIO(raw)).convert("RGB")
    raise RuntimeError(f"生图未知返回: {data}")


# ---------------- DashScope 万相（仍走 IMAGE_*，provider=dashscope） ----------------
def gen_dashscope(prompt: str) -> Image.Image:
    """DashScope 万相：优先 IMAGE_*，兼容旧 DASHSCOPE_* 变量名。"""
    api_key = (
        os.getenv("IMAGE_API_KEY", "").strip()
        or os.getenv("DASHSCOPE_API_KEY", "").strip()
    )
    if not api_key:
        raise RuntimeError(
            "未配置【生图】密钥。\n"
            "当 IMAGE_PROVIDER=dashscope 时请填写：\n"
            "  IMAGE_API_KEY=你的百炼Key\n"
            "  IMAGE_BASE_URL=https://dashscope.aliyuncs.com\n"
            "  IMAGE_MODEL=wanx2.1-t2i-turbo\n"
            "  IMAGE_SIZE=1280*720"
        )

    base = (
        os.getenv("IMAGE_BASE_URL", "").strip()
        or os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com")
    ).rstrip("/")
    model = (
        os.getenv("IMAGE_MODEL", "").strip()
        or os.getenv("DASHSCOPE_IMAGE_MODEL", "wanx2.1-t2i-turbo")
        or "wanx2.1-t2i-turbo"
    )
    size = (
        os.getenv("IMAGE_SIZE", "").strip()
        or os.getenv("DASHSCOPE_IMAGE_SIZE", "1280*720")
        or "1280*720"
    )

    create_url = f"{base}/api/v1/services/aigc/text2image/image-synthesis"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    payload = {
        "model": model,
        "input": {"prompt": prompt},
        "parameters": {
            "size": size,
            "n": 1,
            "prompt_extend": True,
            "watermark": False,
        },
    }
    from http_util import request_json

    headers = {**headers, "Connection": "close"}
    print(f"[生图] provider=dashscope model={model} size={size}")
    data = request_json(
        "POST",
        create_url,
        headers=headers,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=60,
    )

    task_id = (data.get("output") or {}).get("task_id")
    if not task_id:
        url = _extract_dashscope_url(data)
        if url:
            return download_image(url)
        raise RuntimeError(f"DashScope no task_id: {data}")

    task_url = f"{base}/api/v1/tasks/{task_id}"
    from task_hooks import check_cancelled, report_progress

    for i in range(60):
        check_cancelled()          # 生图轮询是取消探针点（用户点取消秒级停）
        report_progress(f"生图中，第 {i + 1}/60 次查询…")
        time.sleep(2 if i < 5 else 3)
        td = request_json(
            "GET",
            task_url,
            headers={"Authorization": f"Bearer {api_key}", "Connection": "close"},
            timeout=30,
        )
        status = ((td.get("output") or {}).get("task_status") or "").upper()
        print(f"[生图] poll {i+1}: {status}")
        if status == "SUCCEEDED":
            url = _extract_dashscope_url(td)
            if not url:
                raise RuntimeError(f"DashScope succeeded but no url: {td}")
            return download_image(url)
        if status in {"FAILED", "CANCELED", "UNKNOWN"}:
            raise RuntimeError(f"DashScope task failed: {td}")
    raise RuntimeError("DashScope timeout waiting for image")


def _extract_dashscope_url(data: dict) -> str:
    out = data.get("output") or {}
    # wanx classic
    results = out.get("results") or []
    if results and results[0].get("url"):
        return results[0]["url"]
    # multimodal style
    choices = out.get("choices") or []
    if choices:
        content = ((choices[0].get("message") or {}).get("content")) or []
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("image"):
                    return c["image"]
                if isinstance(c, dict) and c.get("url"):
                    return c["url"]
    return ""


def save_image(img: Image.Image, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix.lower() in {".jpg", ".jpeg"}:
        img.save(out_path, format="JPEG", quality=92, optimize=True)
    else:
        img.save(out_path)
    return out_path


def generate_ai_cover(
    title: str,
    out_path: Path,
    *,
    abstract: str = "",
    provider: str = "",
    style: str = "editorial",
    overlay: bool = True,
) -> Path:
    """AI 生图封面。IMAGE_FALLBACK_TEMPLATE=1（默认）时生图失败自动退回文字模板。"""
    from task_hooks import check_cancelled, report_progress

    # IMAGE_PROVIDER 为主；兼容旧名 COVER_PROVIDER
    provider = (
        provider
        or os.getenv("IMAGE_PROVIDER", "")
        or os.getenv("COVER_PROVIDER", "openai")
    ).strip().lower()
    style = style or os.getenv("IMAGE_STYLE", "") or os.getenv("COVER_STYLE", "editorial")

    if provider in {"template", "local", "pil"}:
        return template_generate_cover(title, out_path, theme="default")

    prompt = build_prompt(title, abstract=abstract, style=style)
    print(f"[prompt] {prompt[:200]}...")
    check_cancelled()
    try:
        if provider in {"openai", "oai", "dalle", "dall-e"}:
            report_progress("正在请求生图 API（最长 2 分钟）…")
            img = gen_openai(prompt)
        elif provider in {"dashscope", "wanx", "wanxiang", "ali", "qwen-image"}:
            img = gen_dashscope(prompt)
        else:
            raise RuntimeError(
                f"未知 IMAGE_PROVIDER={provider}。可选: openai | dashscope | template"
            )
    except BaseException as e:
        # 取消不兜底，其余失败按配置退回文字模板（GUI/CLI/pipeline 统一生效）
        from task_hooks import TaskCancelled

        if isinstance(e, TaskCancelled):
            raise
        if os.getenv("IMAGE_FALLBACK_TEMPLATE", "1") != "0":
            print(f"[封面] AI 生图失败（{e}），已自动改用文字模板封面")
            return template_generate_cover(title, out_path, theme="default")
        raise

    check_cancelled()
    img = fit_cover(img, TARGET_SIZE)
    overlay_flag = os.getenv("IMAGE_OVERLAY_TITLE", os.getenv("COVER_OVERLAY_TITLE", "1"))
    if overlay and overlay_flag != "0":
        img = overlay_title(img, title)
    return save_image(img, out_path)


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="AI WeChat cover generator")
    parser.add_argument("--title", default="")
    parser.add_argument("--md", default="")
    parser.add_argument("--out", default="samples/cover.jpg")
    parser.add_argument("--provider", default="", help="openai | dashscope | template")
    parser.add_argument("--style", default="", help="editorial|tech|warm|business|nature")
    parser.add_argument("--no-overlay", action="store_true", help="Do not print title on image")
    args = parser.parse_args()

    title = (args.title or "").strip()
    abstract = ""
    if args.md:
        md_path = Path(args.md)
        if not md_path.exists():
            print(f"[ERROR] md not found: {md_path}")
            return 1
        if not title:
            title = extract_title_from_md(md_path)
        abstract = extract_abstract(md_path)
    if not title:
        print("[ERROR] need --title or --md")
        return 1

    try:
        out = generate_ai_cover(
            title,
            Path(args.out),
            abstract=abstract,
            provider=args.provider,
            style=args.style
            or os.getenv("IMAGE_STYLE", "")
            or os.getenv("COVER_STYLE", "editorial"),
            overlay=not args.no_overlay,
        )
        print(f"[OK] AI cover saved: {out}")
        print(f"     title: {title}")
        return 0
    except Exception as e:
        print(f"[ERROR] {e}")
        # optional fallback
        fb = os.getenv("IMAGE_FALLBACK_TEMPLATE", os.getenv("COVER_FALLBACK_TEMPLATE", "1"))
        if fb == "1":
            print("[fallback] using local template cover...")
            out = template_generate_cover(title, Path(args.out), theme="default")
            print(f"[OK] template cover saved: {out}")
            return 0
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
