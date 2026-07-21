#!/usr/bin/env python3
"""真实联网搜索热门选题素材（无写死热门列表）。

支持的搜索源（.env 里 SEARCH_PROVIDER）：
  - auto      : 按已配置的 Key 自动选（tavily > bocha > bing > serper > duckduckgo）
  - multi     : 所有已配置 Key 的源并发一起搜，结果交错合并（推荐多 Key 用户）
  - tavily    : Tavily Search API（需 TAVILY_API_KEY）
  - bing      : 微软 Bing Web Search（需 BING_API_KEY）
  - serper    : Serper.dev Google 结果（需 SERPER_API_KEY）
  - duckduckgo: 免费网页检索（无需 Key，稳定性一般）
  - bocha     : 博查搜索（需 BOCHA_API_KEY，国内可用）

新鲜度：各源默认只搜近期内容（tavily=week / bocha=oneMonth / bing=Week /
serper=近一周 / ddg=w），配合检索词按日期轮换切入角度，
同一领域每天搜到的素材自然不同。

返回统一结构：
  [{"title","url","snippet","source","query"}, ...]
"""

from __future__ import annotations

import datetime
import os
import re
import time
from html import unescape
from typing import Any
from urllib.parse import quote_plus, unquote

from http_util import request_json

# 各搜索源对应的 Key 环境变量（multi 模式据此发现可用源）
PROVIDER_KEY_ENV = {
    "tavily": "TAVILY_API_KEY",
    "bocha": "BOCHA_API_KEY",
    "bing": "BING_API_KEY",
    "serper": "SERPER_API_KEY",
}

# 每日轮换的检索切入角度：同一领域每天搜索方向不同，素材随之变化
_DAILY_ANGLES = [
    "避坑 教训", "真实案例", "方法 技巧", "工具 实操", "数据 报告",
    "趋势 预测", "复盘 总结", "新手 入门", "误区 纠正", "清单 盘点",
]


def _headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "close",
    }


def configured_providers() -> list[str]:
    """已配置 Key 的搜索源列表；一个都没配则退到免费 duckduckgo。"""
    out = [p for p, env in PROVIDER_KEY_ENV.items() if os.getenv(env, "").strip()]
    return out or ["duckduckgo"]


def default_queries(domain: str = "", extra: str = "", day: datetime.date | None = None) -> list[str]:
    """根据领域生成检索词（不是写死选题标题）。

    day 参与角度轮换：同一领域在不同日期生成不同的检索组合，
    保证「同一个提示词每天搜到的内容不一样」。
    """
    domain = (domain or os.getenv("SEARCH_DOMAIN", "") or "公众号 个人成长 职场 副业").strip()
    d = day or datetime.date.today()
    year = str(d.year)
    month = f"{d.year}年{d.month}月"
    angle = _DAILY_ANGLES[d.toordinal() % len(_DAILY_ANGLES)]
    base = [
        f"{domain} {month} 热点 {angle}",
        f"{domain} {year} 公众号 10万+ 爆款 标题",
        f"{domain} 热门 公众号文章 高阅读",
        f"{domain} 微信公众号 刷屏 爆文",
        f"{year} 职场 副业 热点 话题",
        f"{domain} site:mp.weixin.qq.com",
    ]
    if extra:
        base.insert(0, extra.strip())
    # 允许 .env 自定义，用 | 分隔
    custom = os.getenv("SEARCH_QUERIES", "").strip()
    if custom:
        base = [q.strip() for q in custom.split("|") if q.strip()] + base
    # 去重保序
    seen = set()
    out = []
    for q in base:
        if q not in seen:
            seen.add(q)
            out.append(q)
    max_q = int(os.getenv("SEARCH_MAX_QUERIES", "4") or 4)
    return out[:max_q]


def _norm_item(title: str, url: str, snippet: str, source: str, query: str) -> dict[str, str]:
    title = re.sub(r"\s+", " ", (title or "").strip())
    snippet = re.sub(r"\s+", " ", (snippet or "").strip())
    return {
        "title": title[:200],
        "url": (url or "").strip(),
        "snippet": snippet[:300],
        "source": source,
        "query": query,
    }


# ---------- providers ----------
def search_tavily(query: str, n: int = 8) -> list[dict[str, str]]:
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        raise RuntimeError("TAVILY_API_KEY 未配置")
    data = request_json(
        "POST",
        "https://api.tavily.com/search",
        json_body={
            "api_key": key,
            "query": query,
            "search_depth": os.getenv("TAVILY_DEPTH", "basic"),
            "include_answer": False,
            "max_results": n,
            # 只要近期内容（天数可调），保证素材新鲜、每天不同
            "days": int(os.getenv("SEARCH_FRESH_DAYS", "7") or 7),
            "topic": "general",
        },
        timeout=40,
    )
    items = []
    for it in data.get("results") or []:
        items.append(
            _norm_item(it.get("title", ""), it.get("url", ""), it.get("content", ""), "tavily", query)
        )
    return items


def search_bing(query: str, n: int = 8) -> list[dict[str, str]]:
    key = os.getenv("BING_API_KEY", "").strip()
    if not key:
        raise RuntimeError("BING_API_KEY 未配置")
    endpoint = os.getenv(
        "BING_ENDPOINT",
        "https://api.bing.microsoft.com/v7.0/search",
    ).rstrip("/")
    data = request_json(
        "GET",
        endpoint,
        headers={"Ocp-Apim-Subscription-Key": key, **_headers()},
        params={
            "q": query,
            "count": n,
            "mkt": os.getenv("BING_MKT", "zh-CN"),
            "textDecorations": False,
            "freshness": os.getenv("BING_FRESHNESS", "Week"),
        },
        timeout=40,
    )
    items = []
    for it in (data.get("webPages") or {}).get("value") or []:
        items.append(
            _norm_item(it.get("name", ""), it.get("url", ""), it.get("snippet", ""), "bing", query)
        )
    return items


def search_serper(query: str, n: int = 8) -> list[dict[str, str]]:
    key = os.getenv("SERPER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("SERPER_API_KEY 未配置")
    data = request_json(
        "POST",
        "https://google.serper.dev/search",
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json_body={
            "q": query,
            "num": n,
            "gl": "cn",
            "hl": "zh-cn",
            # Google 时间过滤：qdr:w = 近一周
            "tbs": os.getenv("SERPER_TBS", "qdr:w"),
        },
        timeout=40,
    )
    items = []
    for it in data.get("organic") or []:
        items.append(
            _norm_item(it.get("title", ""), it.get("link", ""), it.get("snippet", ""), "serper", query)
        )
    return items


def search_bocha(query: str, n: int = 8) -> list[dict[str, str]]:
    """博查开放搜索 https://open.bochaai.com （若文档变更以官网为准）"""
    key = os.getenv("BOCHA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("BOCHA_API_KEY 未配置")
    url = os.getenv("BOCHA_ENDPOINT", "https://api.bochaai.com/v1/web-search")
    data = request_json(
        "POST",
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json_body={
            "query": query,
            "count": n,
            "summary": True,
            "freshness": os.getenv("BOCHA_FRESHNESS", "oneMonth"),
        },
        timeout=40,
    )
    items = []
    # 兼容多种返回结构
    web_pages = (
        ((data.get("data") or {}).get("webPages") or {}).get("value")
        or (data.get("data") or {}).get("results")
        or data.get("results")
        or []
    )
    for it in web_pages:
        if not isinstance(it, dict):
            continue
        items.append(
            _norm_item(
                it.get("name") or it.get("title") or "",
                it.get("url") or it.get("displayUrl") or "",
                it.get("snippet") or it.get("summary") or "",
                "bocha",
                query,
            )
        )
    return items


def search_duckduckgo(query: str, n: int = 8) -> list[dict[str, str]]:
    """无 Key：优先 ddgs 库（duckduckgo_search 的后继，旧包兜底），失败则 HTML 解析。"""
    # 1) optional package: ddgs（新）→ duckduckgo_search（旧，已停止维护）
    DDGS = None
    try:
        from ddgs import DDGS  # type: ignore
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except ImportError:
            pass
    if DDGS is not None:
        try:
            items = []
            with DDGS() as ddgs:
                for it in ddgs.text(
                    query, region="cn-zh", max_results=n,
                    timelimit=os.getenv("DDG_TIMELIMIT", "w") or None,  # w=近一周
                ):
                    items.append(
                        _norm_item(
                            it.get("title", ""),
                            it.get("href") or it.get("link", ""),
                            it.get("body") or it.get("snippet", ""),
                            "duckduckgo",
                            query,
                        )
                    )
            if items:
                return items
        except Exception:
            pass

    # 2) HTML lite — always close session
    from http_util import with_session

    def _ddg(sess):
        r = sess.post(
            "https://html.duckduckgo.com/html/",
            headers=_headers(),
            data={"q": query, "b": ""},
            timeout=40,
        )
        r.raise_for_status()
        text = r.text
        r.close()
        return text

    html = with_session(_ddg)
    items: list[dict[str, str]] = []
    blocks = re.findall(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)',
        html,
        flags=re.S | re.I,
    )
    if not blocks:
        links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.S | re.I)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</', html, flags=re.S | re.I)
        for i, (href, title) in enumerate(links[:n]):
            sn = snippets[i] if i < len(snippets) else ""
            blocks.append((href, title, sn))  # type: ignore

    for href, title, sn in blocks[:n]:
        title = unescape(re.sub(r"<[^>]+>", "", title))
        sn = unescape(re.sub(r"<[^>]+>", "", sn))
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            href = unquote(m.group(1))
        items.append(_norm_item(title, href, sn, "duckduckgo", query))
    return items


def resolve_provider(name: str = "") -> str:
    name = (name or os.getenv("SEARCH_PROVIDER", "auto") or "auto").strip().lower()
    if name == "multi":
        return "multi"
    if name != "auto":
        return name
    # auto：配了多个 Key 时自动升级为 multi（并发全搜），单 Key 用该源
    providers = configured_providers()
    if providers == ["duckduckgo"]:
        return "duckduckgo"
    if len(providers) > 1:
        return "multi"
    return providers[0]


def _interleave(groups: list[list[dict[str, str]]]) -> list[dict[str, str]]:
    """多个源的结果交错合并（源1第1条、源2第1条…源1第2条…），保证来源多样性。"""
    out: list[dict[str, str]] = []
    idx = 0
    while True:
        added = False
        for g in groups:
            if idx < len(g):
                out.append(g[idx])
                added = True
        if not added:
            return out
        idx += 1


def search_multi(query: str, n: int = 8) -> list[dict[str, str]]:
    """所有已配置 Key 的搜索源并发搜同一个词，结果交错合并。"""
    from concurrent.futures import ThreadPoolExecutor

    providers = configured_providers()
    table = {
        "tavily": search_tavily,
        "bing": search_bing,
        "serper": search_serper,
        "bocha": search_bocha,
        "duckduckgo": search_duckduckgo,
    }
    fns = [(p, table[p]) for p in providers if p in table]
    groups: list[list[dict[str, str]]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=len(fns)) as pool:
        futures = {pool.submit(fn, query, n): p for p, fn in fns}
        for fut, p in futures.items():
            try:
                groups.append(fut.result(timeout=60))
            except Exception as e:
                errors.append(f"{p}: {e}")
    if not groups:
        raise RuntimeError("multi 搜索全部失败：" + "；".join(errors[:3]))
    if errors:
        print(f"[搜索] multi 部分源失败（不影响整体）：{'；'.join(errors[:3])}")
    return _interleave(groups)


def search_one(query: str, provider: str = "", n: int = 8) -> list[dict[str, str]]:
    provider = resolve_provider(provider)
    n = int(os.getenv("SEARCH_RESULTS_PER_QUERY", str(n)) or n)
    table = {
        "multi": search_multi,
        "tavily": search_tavily,
        "bing": search_bing,
        "serper": search_serper,
        "bocha": search_bocha,
        "duckduckgo": search_duckduckgo,
        "ddg": search_duckduckgo,
    }
    fn = table.get(provider)
    if not fn:
        raise RuntimeError(f"未知 SEARCH_PROVIDER={provider}")
    return fn(query, n=n)


def search_hot_materials(
    domain: str = "",
    extra_query: str = "",
    provider: str = "",
) -> tuple[str, list[dict[str, str]]]:
    """执行多组真实搜索，合并去重。"""
    provider = resolve_provider(provider)
    queries = default_queries(domain=domain, extra=extra_query)
    all_items: list[dict[str, str]] = []
    errors: list[str] = []
    if provider == "multi":
        print(f"[搜索] provider=multi（并发源：{'+'.join(configured_providers())}）")
    else:
        print(f"[搜索] provider={provider}")
    for q in queries:
        print(f"  - 检索: {q}")
        try:
            items = search_one(q, provider=provider)
            print(f"    得到 {len(items)} 条")
            all_items.extend(items)
            time.sleep(float(os.getenv("SEARCH_SLEEP_SEC", "0.6") or 0.6))
        except Exception as e:
            msg = f"{q} -> {e}"
            errors.append(msg)
            print(f"    失败: {e}")

    # dedupe by url/title
    seen = set()
    uniq: list[dict[str, str]] = []
    for it in all_items:
        key = (it.get("url") or "") + "|" + (it.get("title") or "")
        if not it.get("title"):
            continue
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    max_total = int(os.getenv("SEARCH_MAX_RESULTS", "20") or 20)
    uniq = uniq[:max_total]
    meta = f"provider={provider}; queries={len(queries)}; hits={len(uniq)}; errors={len(errors)}"
    if not uniq and errors:
        raise RuntimeError("搜索全部失败：\n" + "\n".join(errors[:5]))
    return meta, uniq


def materials_to_prompt_block(items: list[dict[str, str]]) -> str:
    lines = []
    for i, it in enumerate(items, 1):
        lines.append(
            f"{i}. 标题: {it.get('title','')}\n"
            f"   摘要: {it.get('snippet','')}\n"
            f"   链接: {it.get('url','')}\n"
            f"   检索词: {it.get('query','')}"
        )
    return "\n".join(lines)
