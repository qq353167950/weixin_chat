#!/usr/bin/env python3
"""Generate a WeChat-style cover image from title text.

Default size: 900 x 383 (~2.35:1), suitable as thumb cover.

Usage:
  python generate_cover.py --title "文章标题" --out samples/cover.jpg
  python generate_cover.py --md samples/demo.md --out samples/cover.jpg
  python generate_cover.py --md article.md --theme hot --out cover.jpg
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


# WeChat cover often uses ~2.35:1
DEFAULT_SIZE = (900, 383)

THEMES = {
    "default": {
        "bg_top": (30, 41, 59),       # slate
        "bg_bottom": (15, 23, 42),
        "accent": (56, 189, 248),     # sky
        "title": (248, 250, 252),
        "sub": (148, 163, 184),
    },
    "hot": {
        "bg_top": (127, 29, 29),
        "bg_bottom": (69, 10, 10),
        "accent": (251, 191, 36),
        "title": (255, 251, 235),
        "sub": (254, 202, 202),
    },
    "green": {
        "bg_top": (6, 78, 59),
        "bg_bottom": (4, 47, 46),
        "accent": (52, 211, 153),
        "title": (236, 253, 245),
        "sub": (167, 243, 208),
    },
}


def extract_title_from_md(md_path: Path) -> str:
    text = md_path.read_text(encoding="utf-8")
    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
        if s.startswith("#"):
            return re.sub(r"^#+\s*", "", s).strip()
    for line in text.splitlines():
        if line.strip():
            return re.sub(r"^#+\s*", "", line.strip())
    return "未命名文章"


def find_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Prefer Chinese-capable fonts on Windows / common Linux paths."""
    candidates = [
        # Windows
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        # Linux common
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        p = Path(path)
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def wrap_title(text: str, font: ImageFont.ImageFont, draw: ImageDraw.ImageDraw, max_width: int) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return ["未命名文章"]

    # Prefer wrap by characters for CJK
    lines: list[str] = []
    current = ""
    for ch in text:
        trial = current + ch
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = ch
    if current:
        lines.append(current)

    # Max 3 lines
    if len(lines) > 3:
        lines = lines[:3]
        if len(lines[-1]) > 1:
            lines[-1] = lines[-1][:-1] + "…"
    return lines


def lerp_color(c1, c2, t: float):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def make_gradient(size, top, bottom) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, top)
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        color = lerp_color(top, bottom, t)
        for x in range(w):
            px[x, y] = color
    return img


def generate_cover(
    title: str,
    out_path: Path,
    theme: str = "default",
    subtitle: str = "公众号原创",
    size: tuple[int, int] = DEFAULT_SIZE,
) -> Path:
    cfg = THEMES.get(theme, THEMES["default"])
    w, h = size
    img = make_gradient(size, cfg["bg_top"], cfg["bg_bottom"])
    draw = ImageDraw.Draw(img)

    # accent bar
    draw.rectangle([0, 0, 12, h], fill=cfg["accent"])

    # decorative circle
    draw.ellipse([w - 220, -80, w + 40, 180], outline=cfg["accent"], width=3)
    draw.ellipse([w - 160, h - 160, w + 40, h + 40], outline=cfg["accent"], width=2)

    title_font = find_font(48)
    sub_font = find_font(22)

    # If default bitmap font, size control is weak; still ok for ASCII-only fallback
    max_text_width = w - 80
    lines = wrap_title(title, title_font, draw, max_text_width)

    # vertical center block
    line_heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=title_font)
        line_heights.append(bbox[3] - bbox[1])
    gap = 14
    block_h = sum(line_heights) + gap * (len(lines) - 1)
    y = (h - block_h) // 2 - 10

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=title_font)
        tw = bbox[2] - bbox[0]
        x = 48
        # slight shadow
        draw.text((x + 2, y + 2), line, font=title_font, fill=(0, 0, 0))
        draw.text((x, y), line, font=title_font, fill=cfg["title"])
        y += line_heights[i] + gap

    if subtitle:
        draw.text((48, h - 48), subtitle, font=sub_font, fill=cfg["sub"])

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # JPEG quality for WeChat
    if out_path.suffix.lower() in {".jpg", ".jpeg"}:
        img.save(out_path, format="JPEG", quality=90, optimize=True)
    else:
        img.save(out_path)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate WeChat cover image")
    parser.add_argument("--title", default="", help="Cover title text")
    parser.add_argument("--md", default="", help="Read title from markdown (# heading)")
    parser.add_argument("--out", default="samples/cover.jpg", help="Output image path")
    parser.add_argument("--theme", default="default", choices=list(THEMES.keys()))
    parser.add_argument("--subtitle", default="公众号原创", help="Bottom-left subtitle")
    args = parser.parse_args()

    title = (args.title or "").strip()
    if not title and args.md:
        title = extract_title_from_md(Path(args.md))
    if not title:
        print("[ERROR] provide --title or --md")
        return 1

    out = generate_cover(title, Path(args.out), theme=args.theme, subtitle=args.subtitle)
    print(f"[OK] cover saved: {out}")
    print(f"     title: {title}")
    print(f"     theme: {args.theme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
