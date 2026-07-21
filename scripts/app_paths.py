#!/usr/bin/env python3
"""运行时路径解析：源码运行与 PyInstaller 打包（frozen）两种形态统一出口。

约定：
  app_root()  可写数据目录 —— .env、runs/ 都放这里
              源码运行 = 仓库根目录；exe 运行 = exe 所在目录
  asset_dir() 只读资源目录 —— gui/index.html、.env.example 等打包进 exe 的文件
              源码运行 = 仓库根目录；exe 运行 = PyInstaller 解包临时目录 _MEIPASS
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def asset_dir() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[1]


def ensure_env_file() -> Path:
    """确保可写目录下存在 .env；首次运行从打包内置的模板复制。"""
    env = app_root() / ".env"
    if not env.exists():
        template = asset_dir() / ".env.example"
        if template.exists():
            env.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    return env
