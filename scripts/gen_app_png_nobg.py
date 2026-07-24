#!/usr/bin/env python3
"""从带白底的源图生成透明底 assets/app.png。

默认源：assets/app-with-white-bg.png（首次去底时备份的原图）
输出：assets/app.png（1024×1024 透明底，主体居中）

抠图策略：只保留实色青绿叶子/枝干，去掉纸白与底部淡青光晕。

用法：
  python scripts/gen_app_png_nobg.py
  python scripts/gen_app_ico.py   # 同步 Windows ico
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC_CANDIDATES = (
    ROOT / "assets" / "app-with-white-bg.png",
    ROOT / "assets" / "app.png",
)
OUT = ROOT / "assets" / "app.png"


def is_logo_ink(r: int, g: int, b: int) -> bool:
    """实色青绿叶子/枝干，排除纸白与淡青雾。"""
    mn, mx = min(r, g, b), max(r, g, b)
    sat = mx - mn
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    # 必须偏绿
    if g < r + 8:
        return False
    # 太亮且饱和度不够 = 背景雾（含底部浅白/淡青）
    if lum >= 220 and sat < 55:
        return False
    if lum >= 200 and sat < 40:
        return False
    if mn >= 200 and sat < 60 and g < 230:
        if not (g >= 150 and sat >= 50 and g >= r + 20):
            return False
    if g >= 90 and sat >= 35 and g >= r + 10:
        return True
    if g >= 70 and sat >= 50 and g >= r + 15:
        return True
    if g >= 40 and sat >= 30 and g >= r + 8 and lum < 180:
        return True
    return False


def near_logo(mask: list[list[bool]], x: int, y: int, w: int, h: int, rad: int = 2) -> bool:
    for dy in range(-rad, rad + 1):
        for dx in range(-rad, rad + 1):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and mask[ny][nx]:
                return True
    return False


def remove_white_bg(src: Image.Image) -> Image.Image:
    src = src.convert("RGBA")
    w, h = src.size
    px = src.load()

    mask = [[False] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            r, g, b, _a = px[x, y]
            mask[y][x] = is_logo_ink(r, g, b)

    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    op = out.load()
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            r, g, b, _a = px[x, y]
            if mask[y][x]:
                alpha = 255
            elif near_logo(mask, x, y, w, h, 2):
                # 仅给紧贴主体的轻微青绿边缘做抗锯齿，避免底部雾状底
                if g >= r + 5 and g >= 60:
                    sat = max(r, g, b) - min(r, g, b)
                    lum = 0.299 * r + 0.587 * g + 0.114 * b
                    if lum < 230 and sat >= 25:
                        alpha = 90
                    else:
                        continue
                else:
                    continue
            else:
                continue
            op[x, y] = (r, g, b, alpha)
            minx = min(minx, x)
            miny = min(miny, y)
            maxx = max(maxx, x)
            maxy = max(maxy, y)

    if maxx < 0:
        raise RuntimeError("未识别到 logo 主体，请检查源图")

    pad = 24
    box = (
        max(0, minx - pad),
        max(0, miny - pad),
        min(w, maxx + 1 + pad),
        min(h, maxy + 1 + pad),
    )
    cropped = out.crop(box)
    cw, ch = cropped.size
    side = int(max(cw, ch) / 0.90)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(cropped, ((side - cw) // 2, (side - ch) // 2), cropped)
    return canvas.resize((1024, 1024), Image.Resampling.LANCZOS)


def main() -> int:
    src_path = next((p for p in SRC_CANDIDATES if p.is_file()), None)
    if src_path is None:
        print("缺少源图 assets/app-with-white-bg.png 或 assets/app.png", file=sys.stderr)
        return 1
    master = remove_white_bg(Image.open(src_path))
    master.save(OUT, optimize=True)
    print(f"wrote {OUT} from {src_path.name} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
