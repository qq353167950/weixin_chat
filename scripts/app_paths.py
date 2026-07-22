#!/usr/bin/env python3
"""运行时路径解析：源码运行与 PyInstaller 打包（frozen）两种形态统一出口。

约定：
  app_root()  可写数据目录 —— .env、runs/ 都放这里
              源码运行 = 仓库根目录
              exe 运行 = exe 所在目录；若该目录不可写（如装进 Program Files），
              自动回退到 %APPDATA%/公众号助手，保证普通权限也能用
  asset_dir() 只读资源目录 —— gui/index.html、.env.example 等打包进 exe 的文件
              源码运行 = 仓库根目录；exe 运行 = PyInstaller 解包临时目录 _MEIPASS
"""

from __future__ import annotations

import os
import sys
import uuid
from functools import lru_cache
from pathlib import Path

APP_NAME = "公众号助手"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _dir_writable(d: Path) -> bool:
    """真实写入探测：os.access 在 Windows UAC 虚拟化下不可靠。"""
    probe = d / f".write_probe_{uuid.uuid4().hex[:8]}"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


@lru_cache(maxsize=1)
def app_root() -> Path:
    if not is_frozen():
        return Path(__file__).resolve().parents[1]
    if sys.platform == "darwin":
        # mac .app 包内不可写数据（破坏签名），统一落用户目录
        base = Path.home() / "Library" / "Application Support" / APP_NAME
        base.mkdir(parents=True, exist_ok=True)
        return base
    exe_dir = Path(sys.executable).resolve().parent
    if _dir_writable(exe_dir):
        return exe_dir
    # 安装到 Program Files 等只读位置：数据落用户目录
    base = Path(os.getenv("APPDATA") or Path.home()) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def asset_dir() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[1]


def ensure_env_file() -> Path:
    """确保可写目录下存在 .env，并把新版本新增的配置键合并进去。

    合并规则（升级安全）：
      - .env 不存在 → 从内置模板整份复制
      - .env 已存在 → 只追加模板里有而用户没有的键（带默认值），
        用户已有的任何配置一个字不动；追加集中在文件末尾的标记区块
    """
    import re

    env = app_root() / ".env"
    template = asset_dir() / ".env.example"
    if not template.exists():
        return env
    tpl_text = template.read_text(encoding="utf-8")
    if not env.exists():
        env.write_text(tpl_text, encoding="utf-8")
        return env

    try:
        current = env.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        current = env.read_text(encoding="gbk", errors="replace")
    have = set(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", current, flags=re.M))
    missing = [
        (k, v)
        for k, v in re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", tpl_text, flags=re.M)
        if k not in have
    ]
    if missing:
        lines = ["", "# ---- 新版本新增配置（自动追加，默认值可在设置页修改） ----"]
        lines += [f"{k}={v}" for k, v in missing]
        if not current.endswith("\n"):
            current += "\n"
        env.write_text(current + "\n".join(lines) + "\n", encoding="utf-8")
    return env
