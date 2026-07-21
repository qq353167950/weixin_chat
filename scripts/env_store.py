#!/usr/bin/env python3
"""GUI 设置页的 .env 读写：只改目标键的值，保留注释与结构。

约束：
  - 仅匹配未注释的 `KEY=` 行，注释里的示例（# KEY=xxx）不受影响
  - 文件里不存在的键统一追加到末尾的「GUI 追加」区块
  - 值原样写入（dotenv 格式无需引号），换行等非法字符被剔除
"""

from __future__ import annotations

import re
from pathlib import Path

from dotenv import dotenv_values


def read_env_values(path: str | Path) -> dict[str, str]:
    """读取 .env 全部键值（不污染进程环境变量）。文件缺失返回空表。"""
    p = Path(path)
    if not p.exists():
        return {}
    return {k: (v or "") for k, v in dotenv_values(p).items()}


def _clean_value(value: str) -> str:
    return re.sub(r"[\r\n]+", " ", str(value)).strip()


def update_env_file(path: str | Path, updates: dict[str, str]) -> None:
    """把 updates 写入 .env：就地替换已有行，缺失键追加到文件末尾。"""
    p = Path(path)
    lines = p.read_text(encoding="utf-8").split("\n") if p.exists() else []

    pending = {k: _clean_value(v) for k, v in updates.items()}
    out: list[str] = []
    for line in lines:
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) in pending:
            key = m.group(1)
            out.append(f"{key}={pending.pop(key)}")
        else:
            out.append(line)

    if pending:
        if out and out[-1].strip():
            out.append("")
        out.append("# ---- 以下由图形界面追加 ----")
        for k, v in pending.items():
            out.append(f"{k}={v}")

    p.write_text("\n".join(out), encoding="utf-8")
