#!/usr/bin/env python3
"""从 assets/app.png 生成 Windows 友好的多尺寸 app.ico。

规格（与资源管理器列表/快捷方式兼容）：
  - 16 / 24 / 32 / 48 / 64 / 128：32bpp BMP（含 AND 掩码）
  - 256：PNG 压缩（Vista+ 标准）

说明：纯 PNG 多尺寸 ICO 在部分壳/快捷方式场景下小图标可能异常；
属性页大图仍正常（优先 256），与「列表错、属性对」现象一致。
"""

from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "assets" / "app.png"
OUT = ROOT / "assets" / "app.ico"

# 资源管理器列表 / 开始菜单 / 桌面常用
SIZES_BMP = (16, 24, 32, 48, 64, 128)
SIZE_PNG = 256


def _row_pad(width: int, bpp: int) -> int:
    """DIB 行按 4 字节对齐后的字节数。"""
    bits = width * bpp
    return (bits + 31) // 32 * 4


def _rgba_to_icon_bmp(im: Image.Image) -> bytes:
    """RGBA → ICO 内嵌 32bpp DIB（XOR + 1bit AND mask，高度为 2h）。"""
    im = im.convert("RGBA")
    w, h = im.size
    pixels = im.load()

    xor_stride = _row_pad(w, 32)
    and_stride = _row_pad(w, 1)
    xor = bytearray(xor_stride * h)
    and_mask = bytearray(and_stride * h)

    for y in range(h):
        # DIB 自下而上
        src_y = h - 1 - y
        xor_row = y * xor_stride
        and_row = y * and_stride
        for x in range(w):
            r, g, b, a = pixels[x, src_y]
            o = xor_row + x * 4
            xor[o : o + 4] = bytes((b, g, r, a))
            if a < 128:
                and_mask[and_row + (x // 8)] |= 0x80 >> (x % 8)

    header = struct.pack(
        "<IiiHHIIiiII",
        40,  # biSize
        w,
        h * 2,  # 图像 + 掩码
        1,  # planes
        32,  # bitCount
        0,  # BI_RGB
        len(xor),
        0,
        0,
        0,
        0,
    )
    return header + bytes(xor) + bytes(and_mask)


def _png_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.convert("RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build_ico(src: Image.Image) -> bytes:
    src = src.convert("RGBA")
    entries: list[tuple[int, int, bytes, bool]] = []

    for size in SIZES_BMP:
        im = src.resize((size, size), Image.Resampling.LANCZOS)
        payload = _rgba_to_icon_bmp(im)
        entries.append((size, size, payload, False))

    im256 = src.resize((SIZE_PNG, SIZE_PNG), Image.Resampling.LANCZOS)
    entries.append((SIZE_PNG, SIZE_PNG, _png_bytes(im256), True))

    count = len(entries)
    # ICONDIR + ICONDIRENTRY * count
    offset = 6 + 16 * count
    dir_entries = bytearray()
    payloads = bytearray()

    for w, h, payload, is_png in entries:
        dir_entries += struct.pack(
            "<BBBBHHII",
            0 if w >= 256 else w,
            0 if h >= 256 else h,
            0,  # color count
            0,  # reserved
            1,  # planes
            32,  # bit count
            len(payload),
            offset,
        )
        payloads += payload
        offset += len(payload)
        _ = is_png  # 仅文档用途

    header = struct.pack("<HHH", 0, 1, count)
    return header + bytes(dir_entries) + bytes(payloads)


def main() -> int:
    if not SRC.is_file():
        print(f"缺少源图: {SRC}", file=sys.stderr)
        return 1
    src = Image.open(SRC)
    data = build_ico(src)
    OUT.write_bytes(data)

    # 自检：枚举 entry
    reserved, type_, count = struct.unpack_from("<HHH", data, 0)
    assert reserved == 0 and type_ == 1 and count == len(SIZES_BMP) + 1
    print(f"wrote {OUT} ({len(data)} bytes, {count} images)")
    off = 6
    for i in range(count):
        w, h, colors, _res, planes, bitcount, size, o = struct.unpack_from(
            "<BBBBHHII", data, off
        )
        ww = 256 if w == 0 else w
        hh = 256 if h == 0 else h
        payload = data[o : o + size]
        kind = "PNG" if payload[:8] == b"\x89PNG\r\n\x1a\n" else "BMP"
        print(f"  [{i}] {ww}x{hh} {kind} {size}B planes={planes} bpp={bitcount}")
        off += 16
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
