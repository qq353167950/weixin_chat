#!/usr/bin/env python3
"""从带白底的源图生成透明底 assets/app.png。

默认源：assets/app-with-white-bg.png（首次去底时备份的原图）
输出：assets/app.png（1024×1024 透明底，主体居中）

用法：
  python scripts/gen_app_png_nobg.py
  python scripts/gen_app_ico.py   # 同步 Windows ico
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC_CANDIDATES = (
    ROOT / "assets" / "app-with-white-bg.png",
    ROOT / "assets" / "app.png",
)
OUT = ROOT / "assets" / "app.png"


def bg_score(r: int, g: int, b: int) -> int:
    """0=主体，255=背景。"""
    mn, mx = min(r, g, b), max(r, g, b)
    sat = mx - mn
    greenish = (g >= r + 12 and g >= 70) or (g >= r + 5 and b >= r + 5 and g >= 90)
    if greenish:
        if mn >= 248 and sat < 25:
            return 200
        return 0
    if mn >= 250 and sat <= 12:
        return 255
    if mn >= 240 and sat <= 20:
        return 255
    if mn >= 230 and sat <= 25:
        return int(180 + (mn - 230) * 3)
    if mn >= 220 and sat <= 30:
        return 120
    if sat <= 18 and mn >= 160:
        return min(255, int(100 + (mn - 160)))
    return 0


def remove_white_bg(src: Image.Image) -> Image.Image:
    src = src.convert("RGBA")
    w, h = src.size
    px = src.load()

    bg = [[False] * w for _ in range(h)]
    q: deque[tuple[int, int]] = deque()
    for x in range(w):
        for y in (0, h - 1):
            r, g, b, _a = px[x, y]
            if bg_score(r, g, b) >= 100:
                bg[y][x] = True
                q.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if not bg[y][x]:
                r, g, b, _a = px[x, y]
                if bg_score(r, g, b) >= 100:
                    bg[y][x] = True
                    q.append((x, y))
    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and not bg[ny][nx]:
                r, g, b, _a = px[nx, ny]
                if bg_score(r, g, b) >= 100:
                    bg[ny][nx] = True
                    q.append((nx, ny))

    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    op = out.load()
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            r, g, b, _a = px[x, y]
            sc = bg_score(r, g, b)
            if bg[y][x]:
                if sc >= 200:
                    continue
                alpha = max(0, 255 - sc)
            else:
                alpha = max(0, 255 - sc) if sc else 255
            if alpha <= 8:
                continue
            op[x, y] = (r, g, b, min(255, alpha))
            minx = min(minx, x)
            miny = min(miny, y)
            maxx = max(maxx, x)
            maxy = max(maxy, y)

    if maxx < 0:
        raise RuntimeError("未识别到 logo 主体，请检查源图")

    pad = 20
    box = (
        max(0, minx - pad),
        max(0, miny - pad),
        min(w, maxx + 1 + pad),
        min(h, maxy + 1 + pad),
    )
    cropped = out.crop(box)
    cw, ch = cropped.size
    side = int(max(cw, ch) / 0.92)
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
