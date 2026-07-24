#!/usr/bin/env python3
"""版本管理与自动更新检查。

对外接口：
  __version__                     当前版本号（与 git tag 同步）
  check_update()                  检查更新，返回 (has_update, remote_ver, download_url, changelog)
  compare_version(v1, v2)         版本号比较（'1.2.3' vs '1.2.4' → -1）
"""

from __future__ import annotations

import html
import json
import platform
import re
import urllib.error
import urllib.request
from typing import Literal

__version__ = "2.1.5"

GITHUB_REPO = "qq353167950/weixin_chat"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
# Atom 订阅不走 REST 限流（未认证 API 仅 60 次/小时/IP），国内经代理时更稳
GITHUB_ATOM = f"https://github.com/{GITHUB_REPO}/releases.atom"
TIMEOUT = 12  # 秒
_UA = "weixin-chat-updater"


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


def _http_get(url: str, *, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": _UA,
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def _friendly_http_error(exc: BaseException) -> str:
    """把 urllib / HTTP 错误翻成用户可读原因（勿一律叫网络失败）。"""
    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        reason = str(exc.reason or "")
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        low = (reason + " " + body).lower()
        if code == 403 and ("rate limit" in low or "api rate limit" in low):
            return (
                "GitHub API 访问次数已用尽（未登录每小时约 60 次，同出口 IP 共用）。"
                "请稍后再试；若开了代理请确认未把大量请求打到同一 IP。"
            )
        if code in (401, 403):
            return f"GitHub 拒绝访问（HTTP {code}）"
        if code == 404:
            return "未找到 Release（仓库可能尚无正式发布）"
        return f"GitHub 返回 HTTP {code}：{reason or 'error'}"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        msg = str(reason) if reason is not None else str(exc)
        low = msg.lower()
        if "timed out" in low or "timeout" in low:
            return "连接 GitHub 超时（国内常需系统/代理可访问 github.com）"
        if any(
            t in low
            for t in (
                "getaddrinfo failed",
                "name or service not known",
                "nodename nor servname",
                "11001",
            )
        ):
            return "无法解析 github.com（检查 DNS 或代理）"
        if any(
            t in low
            for t in (
                "connection refused",
                "connection reset",
                "10054",
                "10053",
                "10060",
                "10061",
                "network is unreachable",
                "failed to connect",
                "unexpected_eof",
                "ssl",
            )
        ):
            return (
                "无法连接 GitHub（直连常被重置/超时）。"
                "请开启可访问 GitHub 的系统代理后再检查更新。"
            )
        return f"网络请求失败：{msg}"
    return f"检查更新失败：{exc}"


def _changelog_from_html(content_html: str) -> str:
    """Atom <content> 里的 HTML 列表 → 纯文本 bullet，供 GUI 再条目化。"""
    text = html.unescape(content_html or "")
    items = re.findall(r"<li[^>]*>([\s\S]*?)</li>", text, flags=re.I)
    if items:
        lines = []
        for it in items:
            plain = re.sub(r"<[^>]+>", "", it)
            plain = re.sub(r"\s+", " ", plain).strip()
            if plain:
                lines.append(f"- {plain}")
        if lines:
            return "\n".join(lines)[:500]
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:500]


def _default_download_url(remote_ver: str) -> str:
    """无 assets 列表时按约定文件名拼下载地址（与 CI 产物命名一致）。"""
    tag = f"v{remote_ver.lstrip('v')}"
    base = f"https://github.com/{GITHUB_REPO}/releases/download/{tag}"
    sys = platform.system().lower()
    if sys == "windows":
        # 绿色版单 exe：应用内自替换更新用
        return f"{base}/wechat-assistant.exe"
    if sys == "darwin":
        return f"{base}/wechat-assistant-macos.zip"
    return f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}"


def _pick_asset_url(assets: list, remote_ver: str) -> str:
    sys = platform.system().lower()
    patterns = {
        "windows": [r"^wechat-assistant\.exe$", r"wechat-assistant\.exe$", r"\.exe$"],
        "darwin": [r"macos.*\.zip$", r"\.app\.zip$", r"\.zip$"],
        "linux": [r"linux.*\.tar\.gz$", r"\.tar\.gz$"],
    }
    for pattern in patterns.get(sys, [r"\.(exe|zip|tar\.gz)$"]):
        for asset in assets:
            name = asset.get("name", "")
            if re.search(pattern, name, flags=re.I):
                url = asset.get("browser_download_url", "")
                if url:
                    return url
    return _default_download_url(remote_ver)


def _parse_atom_latest(xml: str) -> tuple[str, str, str]:
    """返回 (remote_ver, release_page_url, changelog)。"""
    entry_m = re.search(r"<entry>([\s\S]*?)</entry>", xml)
    if not entry_m:
        raise RuntimeError("Release 订阅为空（仓库尚无发布？）")
    entry = entry_m.group(1)
    title_m = re.search(r"<title>([^<]+)</title>", entry)
    if not title_m:
        raise RuntimeError("无法解析最新版本号")
    remote_ver = title_m.group(1).strip().lstrip("v")
    if not re.match(r"^\d+\.\d+", remote_ver):
        # 兼容 title 写成「v2.1.0：说明」
        m2 = re.search(r"v?(\d+\.\d+(?:\.\d+)?)", title_m.group(1))
        if not m2:
            raise RuntimeError(f"无法识别版本号：{title_m.group(1)!r}")
        remote_ver = m2.group(1)
    link_m = re.search(r'<link[^>]+href="([^"]+)"', entry)
    page = link_m.group(1) if link_m else f"https://github.com/{GITHUB_REPO}/releases/latest"
    content_m = re.search(r"<content[^>]*>([\s\S]*?)</content>", entry)
    changelog = _changelog_from_html(content_m.group(1) if content_m else "")
    return remote_ver, page, changelog


def _check_via_atom() -> tuple[bool, str, str, str]:
    raw = _http_get(GITHUB_ATOM, accept="application/atom+xml, application/xml, text/xml, */*")
    xml = raw.decode("utf-8", errors="replace")
    remote_ver, page, changelog = _parse_atom_latest(xml)
    if compare_version(__version__, remote_ver) >= 0:
        return False, remote_ver, "", ""
    download_url = _default_download_url(remote_ver)
    # page 作次选：下载失败时可打开发布页
    if not download_url:
        download_url = page
    if len(changelog) > 500:
        changelog = changelog[:500] + "..."
    return True, remote_ver, download_url, changelog


def _check_via_api() -> tuple[bool, str, str, str]:
    raw = _http_get(GITHUB_API, accept="application/vnd.github+json")
    data = json.loads(raw.decode("utf-8"))
    remote_ver = data.get("tag_name", "").lstrip("v")
    if not remote_ver:
        return False, "", "", "远程版本号为空"
    if compare_version(__version__, remote_ver) >= 0:
        return False, remote_ver, "", ""
    download_url = _pick_asset_url(data.get("assets") or [], remote_ver)
    if not download_url:
        download_url = data.get("html_url", "") or _default_download_url(remote_ver)
    changelog = (data.get("body") or "").strip()
    if len(changelog) > 500:
        changelog = changelog[:500] + "..."
    return True, remote_ver, download_url, changelog


def check_update() -> tuple[bool, str, str, str]:
    """检查 GitHub Releases 是否有新版本。

    返回：
      (has_update, remote_version, download_url, changelog)

    策略：
      1) 优先 Releases Atom（不计入 REST 60 次/小时限流）
      2) 失败再回退 api.github.com/releases/latest
      错误信息区分：限流 / 超时 / 连不上 GitHub，不再一律「网络请求失败」
    """
    errors: list[str] = []

    try:
        return _check_via_atom()
    except Exception as e:
        errors.append(f"订阅源：{_friendly_http_error(e)}")

    try:
        return _check_via_api()
    except Exception as e:
        errors.append(f"API：{_friendly_http_error(e)}")

    # 两条都失败：拼一条给 toast
    return False, "", "", "；".join(errors) if errors else "检查更新失败"


if __name__ == "__main__":
    # 测试版本比较
    assert compare_version("1.2.3", "1.2.4") == -1
    assert compare_version("1.3.0", "1.2.9") == 1
    assert compare_version("1.2.3", "1.2.3") == 0
    assert compare_version("v1.2.3", "1.2.4") == -1
    print("✓ 版本比较测试通过")

    sample = """<?xml version="1.0"?>
    <feed>
      <entry>
        <title>v2.1.0</title>
        <link rel="alternate" href="https://github.com/x/y/releases/tag/v2.1.0"/>
        <content type="html">&lt;ul&gt;&lt;li&gt;新增：A&lt;/li&gt;&lt;li&gt;修复：B&lt;/li&gt;&lt;/ul&gt;</content>
      </entry>
    </feed>
    """
    ver, page, log = _parse_atom_latest(sample)
    assert ver == "2.1.0"
    assert "v2.1.0" in page
    assert "新增：A" in log
    print("✓ Atom 解析测试通过")

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
