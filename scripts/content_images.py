#!/usr/bin/env python3
"""正文图片处理：把 HTML 里的外链 / 本地图片换成微信图床 URL。

背景约束：
  - 公众号正文里的非微信域名 <img> 会被过滤成裂图
  - 官方 uploadimg 接口只收 jpg/png 且单张 ≤ 1MB
处理流程：
  1) 扫描 HTML 中所有 <img src="...">
  2) mmbiz 域名 → 原样保留；外链 → 下载；本地路径 → 直接读取
  3) 超限图片用 Pillow 压缩到 1MB 以内
  4) 调 WeChatClient.upload_content_image 换取图床 URL 并回写 HTML

对外接口：
  replace_content_images(html, client, work_dir) -> (新HTML, 报告列表)
"""

from __future__ import annotations

import hashlib
import re
from io import BytesIO
from pathlib import Path

from PIL import Image

from http_util import request_bytes
from wechat_client import WeChatClient

# 微信图床域名，已经可用，无需二次上传
_MMBIZ_RE = re.compile(r"^https?://mmbiz\.q(?:pic|logo)\.cn/", re.I)
_IMG_TAG_RE = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', re.I)

# uploadimg 官方上限 1MB，留一点余量
_MAX_BYTES = 1000 * 1024


def _compress_to_limit(raw: bytes) -> bytes:
    """确保图片为 jpg/png 且 ≤ 1MB；超限时转 JPEG 逐级降质、必要时缩边。"""
    img = Image.open(BytesIO(raw))
    fmt = (img.format or "").upper()
    if fmt in {"JPEG", "PNG"} and len(raw) <= _MAX_BYTES:
        return raw

    img = img.convert("RGB")
    # 先按质量降，再按尺寸缩，直到达标
    for scale in (1.0, 0.85, 0.7, 0.55, 0.4):
        w, h = img.size
        cur = img if scale == 1.0 else img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS
        )
        for quality in (88, 78, 68, 58):
            buf = BytesIO()
            cur.save(buf, format="JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= _MAX_BYTES:
                return data
    return data  # 已是最小尝试结果，交给接口做最终裁决


def _load_image_bytes(src: str, base_dir: Path | None) -> bytes:
    """按 src 类型取图：http(s) 下载；其余按本地路径读取。"""
    if src.lower().startswith(("http://", "https://")):
        return request_bytes("GET", src, timeout=60)
    p = Path(src)
    if not p.is_absolute() and base_dir is not None:
        p = base_dir / p
    return p.read_bytes()


def replace_content_images(
    html: str,
    client: WeChatClient,
    *,
    base_dir: Path | None = None,
    cache_dir: Path | None = None,
) -> tuple[str, list[str]]:
    """把 HTML 内所有非微信域名图片上传微信图床并替换 URL。

    返回 (新 HTML, 处理报告行)。单张失败不中断，只记录并保留原地址。
    """
    report: list[str] = []
    uploaded: dict[str, str] = {}  # src -> mmbiz url，同图去重

    def _repl(m: re.Match) -> str:
        src = m.group(2)
        if _MMBIZ_RE.match(src) or src.startswith("data:"):
            return m.group(0)
        if src in uploaded:
            return m.group(1) + uploaded[src] + m.group(3)
        try:
            raw = _load_image_bytes(src, base_dir)
            raw = _compress_to_limit(raw)
            # 落一份缓存，便于用户核查上传了什么
            digest = hashlib.md5(raw).hexdigest()[:10]
            tmp_dir = cache_dir or Path(".")
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp = tmp_dir / f"content-img-{digest}.jpg"
            tmp.write_bytes(raw)
            url = client.upload_content_image(tmp)
            uploaded[src] = url
            report.append(f"[图片] 已上传 {src[:60]} → 微信图床")
            return m.group(1) + url + m.group(3)
        except Exception as e:  # 保底：留原链，避免整篇失败
            report.append(f"[图片] 上传失败，保留原地址 {src[:60]}：{e}")
            return m.group(0)

    new_html = _IMG_TAG_RE.sub(_repl, html)
    return new_html, report


def count_external_images(html: str) -> int:
    """统计 HTML 中需要转存的图片数量（非 mmbiz、非 data:）。"""
    n = 0
    for m in _IMG_TAG_RE.finditer(html):
        src = m.group(2)
        if not _MMBIZ_RE.match(src) and not src.startswith("data:"):
            n += 1
    return n
