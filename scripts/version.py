#!/usr/bin/env python3
"""版本管理与自动更新检查。

对外接口：
  __version__                     当前版本号（与 git tag 同步）
  check_update()                  检查更新，返回 (has_update, remote_ver, download_url, changelog)
  compare_version(v1, v2)         版本号比较（'1.2.3' vs '1.2.4' → -1）
"""

from __future__ import annotations

import json
import platform
import re
import urllib.error
import urllib.request
from typing import Literal

__version__ = "1.7.4"

GITHUB_REPO = "qq353167950/weixin_chat"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
TIMEOUT = 10  # 秒


def compare_version(v1: str, v2: str) -> Literal[-1, 0, 1]:
    """版本号比较：v1 < v2 返回 -1，v1 == v2 返回 0，v1 > v2 返回 1。

    示例：
      compare_version("1.2.3", "1.2.4") → -1
      compare_version("1.3.0", "1.2.9") → 1
      compare_version("1.2.3", "1.2.3") → 0
    """
    def _parse(v: str) -> tuple[int, ...]:
        # 去掉前导 v 和后缀（如 v1.2.3-beta → 1.2.3）
        clean = re.sub(r"^v", "", v).split("-")[0]
        return tuple(int(x) for x in clean.split("."))

    p1, p2 = _parse(v1), _parse(v2)
    if p1 < p2:
        return -1
    if p1 > p2:
        return 1
    return 0


def check_update() -> tuple[bool, str, str, str]:
    """检查 GitHub Releases 是否有新版本。

    返回：
      (has_update, remote_version, download_url, changelog)

    异常处理：
      - 网络超时、API 失败等返回 (False, "", "", "检查更新失败：...")
    """
    try:
        req = urllib.request.Request(GITHUB_API, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        remote_ver = data.get("tag_name", "").lstrip("v")
        if not remote_ver:
            return False, "", "", "远程版本号为空"

        # 比较版本
        if compare_version(__version__, remote_ver) >= 0:
            return False, remote_ver, "", ""  # 当前已是最新

        # 找到对应平台的下载链接
        assets = data.get("assets", [])
        sys = platform.system().lower()

        # 优先匹配规则（Windows 优先安装版，macOS 优先 .zip）
        patterns = {
            "windows": [r"setup.*\.exe$", r"\.exe$"],
            "darwin": [r"macos.*\.zip$", r"\.app\.zip$", r"\.zip$"],
            "linux": [r"linux.*\.tar\.gz$", r"\.tar\.gz$"],
        }

        download_url = ""
        for pattern in patterns.get(sys, [r"\.(exe|zip|tar\.gz)$"]):
            for asset in assets:
                name = asset.get("name", "").lower()
                if re.search(pattern, name):
                    download_url = asset.get("browser_download_url", "")
                    break
            if download_url:
                break

        if not download_url:
            download_url = data.get("html_url", "")  # 回退到 Release 页面

        # 提取更新日志（取 body 前 500 字符）
        changelog = data.get("body", "")[:500].strip()
        if len(data.get("body", "")) > 500:
            changelog += "..."

        return True, remote_ver, download_url, changelog

    except urllib.error.URLError as e:
        return False, "", "", f"网络请求失败：{e.reason}"
    except Exception as e:
        return False, "", "", f"检查更新失败：{e}"


if __name__ == "__main__":
    # 测试版本比较
    assert compare_version("1.2.3", "1.2.4") == -1
    assert compare_version("1.3.0", "1.2.9") == 1
    assert compare_version("1.2.3", "1.2.3") == 0
    assert compare_version("v1.2.3", "1.2.4") == -1
    print(f"✓ 版本比较测试通过")

    # 测试更新检查
    print(f"当前版本：{__version__}")
    has, ver, url, log = check_update()
    if has:
        print(f"✓ 发现新版本：{ver}")
        print(f"  下载链接：{url}")
        print(f"  更新日志：{log[:100]}...")
    else:
        print(f"✓ 已是最新版本（远程：{ver or '未知'}）")
        if log:
            print(f"  原因：{log}")
