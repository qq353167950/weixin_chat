#!/usr/bin/env python3
"""文章内容引擎：选题整理 + 反AI腔写作 + 参考文章抓取 + 教程配图。

被 pipeline.py（命令行）与 gui_server.py（图形界面）共用，保持单一实现。

能力：
  llm_rank_topics          真实搜索素材 → 候选选题
  fetch_reference_articles 抓取搜索结果原文，供写作学习真人语感
  build_article_prompt     反AI腔写作提示词（禁用词表 + 句式约束）
  local_fallback_article   写作模型不可用时的本地提纲稿
  detect_ai_phrases        成稿 AI 腔检测
  plan_illustrations       大模型规划文中配图点位
  illustrate_article       生成教程示意图并插入 Markdown
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from http_util import with_session
from llm_client import llm_chat, llm_json

# ---------------- AI 腔禁用词（提示词约束 + 成稿检测双用） ----------------
AI_BANNED = [
    "首先", "其次", "再者", "综上所述", "总而言之", "总的来说",
    "众所周知", "不难发现", "由此可见", "值得一提", "值得注意的是",
    "在当今", "在这个快节奏的时代", "随着科技的发展", "随着社会的发展",
    "赋能", "抓手", "闭环", "底层逻辑", "颗粒度",
    "干货满满", "深度好文", "揭秘", "震惊", "必看",
    "让我们一起", "跟我一起", "开启你的",
]

_UA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "close",
}


# ---------------- 选题整理 ----------------
def llm_rank_topics(materials: list[dict], domain: str, want_n: int = 5) -> list[dict]:
    """用大模型把真实搜索结果整理成可选选题（不是写死列表）。"""
    import datetime

    from topic_search import materials_to_prompt_block

    block = materials_to_prompt_block(materials)
    today = datetime.date.today()
    # 与 topic_search._DAILY_ANGLES 同步的轮换角度：同一领域每天侧重不同
    angles = [
        "避坑与教训", "真实案例拆解", "方法与技巧", "工具实操", "数据与报告解读",
        "趋势预测", "复盘总结", "新手入门", "常见误区纠正", "清单式盘点",
    ]
    angle_today = angles[today.toordinal() % len(angles)]
    system = (
        "你是公众号主编，擅长从真实热点素材里提炼可写爆文选题。\n"
        "必须基于给定搜索结果，禁止编造不存在的文章标题链接。\n"
        "输出严格 JSON 数组，不要 markdown 代码围栏。"
    )
    user = (
        f"今天是 {today.isoformat()}，本日选题侧重方向：{angle_today}"
        f"（在合适素材上优先往这个角度提炼，避免与往日选题同质化）。\n"
        f"领域/账号方向：{domain or '综合成长/职场/副业'}\n"
        f"请从下列真实搜索结果中，提炼 {want_n} 个适合公众号的候选选题。\n"
        "每个元素字段：\n"
        "title: 公众号标题，要求：\n"
        "  - 短而有冲击力，14-24 字，绝不超过 28 字\n"
        "  - 突出最抓人的一个点，可以适度夸张制造好奇/反差/情绪\n"
        "    （如数字反差「3 个月从 0 到 1 万」、悬念「没人告诉你的」、\n"
        "    立场「劝你别再…」），但不虚构事实、不做纯标题党\n"
        "  - 口语化，像朋友转发时会说的话；禁止「浅析/探讨/之我见/\n"
        "    关于…的思考」这类论文腔和「XX：YY」式冒号双段格式化标题\n"
        "type: 干货文/观点文/案例文/清单体 之一\n"
        "angle: 写作角度（一句话）\n"
        "why: 为什么现在值得写（结合热点）\n"
        "audience: 目标读者\n"
        "refs: 参考了哪些素材标题（短，用分号分隔）\n"
        "score: 1-10 爆款潜力\n\n"
        f"搜索素材：\n{block}\n"
    )
    data = llm_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=float(os.getenv("TOPIC_LLM_TEMPERATURE", "0.5") or 0.5),
    )
    if isinstance(data, dict) and "topics" in data:
        data = data["topics"]
    if not isinstance(data, list):
        raise RuntimeError(f"选题 JSON 不是数组: {type(data)}")
    topics = []
    for it in data:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        topics.append(
            {
                "title": title,
                "type": str(it.get("type") or "干货文"),
                "angle": str(it.get("angle") or ""),
                "why": str(it.get("why") or ""),
                "audience": str(it.get("audience") or ""),
                "refs": str(it.get("refs") or ""),
                "score": it.get("score", ""),
            }
        )
    return topics[:want_n]


# ---------------- 参考文章抓取 ----------------
def html_to_text(html: str) -> str:
    """网页 HTML → 纯文本（轻量实现，够喂给大模型学语感即可）。"""
    from html import unescape

    html = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>|</tr>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f　]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _fetch_page_text(url: str, timeout: int = 15) -> str:
    def _do(sess):
        from urllib.parse import urlsplit

        # 带 Referer 走搜索引擎来路，部分站点据此放行
        headers = dict(_UA_HEADERS)
        headers["Referer"] = "https://www.google.com/"
        headers["Accept"] = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        )
        r = sess.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").lower()
        if ctype and "html" not in ctype and "text" not in ctype:
            raise RuntimeError(f"非网页内容（{ctype.split(';')[0]}）")
        # 中文站点常见编码声明缺失，按内容推断
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        text = r.text
        r.close()
        return text

    return with_session(_do)


def pick_reference_materials(
    materials: list[dict], topic: dict, max_n: int
) -> list[dict]:
    """优先选被选题 refs 引用的素材，不足时按搜索顺序补齐。"""
    refs = topic.get("refs", "") or ""
    hit, rest = [], []
    for m in materials:
        title = (m.get("title") or "").strip()
        url = (m.get("url") or "").strip()
        if not title or not url.startswith("http"):
            continue
        if title[:10] and title[:10] in refs:
            hit.append(m)
        else:
            rest.append(m)
    return (hit + rest)[:max_n]


def fetch_reference_articles(
    materials: list[dict],
    topic: dict,
    *,
    max_n: int | None = None,
    per_article_chars: int = 3000,
    log=print,
) -> list[dict]:
    """抓取参考文章原文。逐个候选尝试直到抓够 max_n 篇。

    此前只试前 max_n 个候选，命中反爬/JS 页就颗粒无收；
    现在失败自动换下一个候选（上限 max_n*4 次尝试），并说明失败原因。
    """
    if max_n is None:
        max_n = int(os.getenv("ARTICLE_REF_MAX", "5") or 5)
    candidates = pick_reference_materials(materials, topic, max_n * 4)
    out: list[dict] = []
    fails = 0
    for m in candidates:
        if len(out) >= max_n:
            break
        url = m["url"]
        try:
            log(f"  抓取参考: {m['title'][:40]}")
            text = html_to_text(_fetch_page_text(url))
            # 过滤导航/版权等噪声行，取正文密集段
            lines = [ln.strip() for ln in text.split("\n") if len(ln.strip()) >= 20]
            body = "\n".join(lines)[:per_article_chars]
            if len(body) < 200:
                log("    正文太短（可能是 JS 渲染页/反爬拦截页），换下一篇")
                fails += 1
                continue
            out.append({"title": m["title"], "url": url, "text": body})
        except Exception as e:
            msg = str(e)
            if "403" in msg or "Forbidden" in msg:
                log("    该站拒绝抓取（403 反爬），换下一篇")
            elif "404" in msg:
                log("    页面不存在（404），换下一篇")
            elif "timed out" in msg.lower() or "timeout" in msg.lower():
                log("    访问超时，换下一篇")
            else:
                log(f"    抓取失败: {msg[:80]}，换下一篇")
            fails += 1
    if not out and fails:
        log("  参考文全部抓取失败（多为站点反爬），将不带参考直接写作——不影响成稿")
    return out


def refs_to_prompt_block(refs: list[dict]) -> str:
    if not refs:
        return ""
    parts = [
        "以下是几篇真实文章片段，供你吸收「真人是怎么写这个话题的」：\n"
        "- 学它们的语感、口头禅、举例方式、开头切入和收尾节奏\n"
        "- 观察真人如何自然过渡段落、如何带入个人视角和情绪\n"
        "- 多篇参考取长补短融成自己的风格，不要只模仿其中一篇\n"
        "- 严禁抄袭任何句子或段落，事实数据必须自己改写核实：\n"
    ]
    for i, r in enumerate(refs, 1):
        parts.append(f"【参考{i}】{r['title']}\n{r['text']}\n")
    return "\n".join(parts)


# ---------------- 写作提示词（反AI腔） ----------------
def build_article_prompt(
    topic: dict, user_extra: str = "", refs_block: str = ""
) -> list[dict]:
    system = (
        "你是一位有多年行业实战经验的公众号作者。文字像跟朋友聊天，"
        "但工具、数字、方法都经得起内行推敲。\n"
        "\n"
        "【结构要求】\n"
        "1) 第一行是 Markdown 一级标题：# 标题——14-24 字，突出最抓人的一个点，"
        "可适度夸张制造好奇/反差，但不虚构；禁止论文腔和「XX：YY」冒号双段式\n"
        "2) 3-5 个 ## 小标题，短而有力；每节可用 > 引用块放一句核心观点\n"
        "3) 操作步骤用 1. 2. 3. 有序列表，要点用 - 列表\n"
        "4) 关键结论 **加粗**，每节至多 2 处\n"
        "5) 结尾自然收束即可：可以是一个观点、一句留白或对读者的真诚提醒，"
        "禁止套用「今天就能动手的动作/现在就去做/别等周末」这类行动号召模板——"
        "每篇结尾方式应随内容自然变化，不要形成固定套路\n"
        "\n"
        "【语言要求（最重要）】\n"
        "6) 口语化与专业化并存：解释像跟同事聊天，数据和操作精确具体\n"
        "7) 长短句交替出节奏：短句制造停顿，长句展开细节\n"
        "8) 拒绝空泛：〈很多钱〉要写成〈大概3000块〉，〈某工具〉要写出名字和用法\n"
        "9) 案例必须有人物、场景、动作、结果（带数字），像你亲眼见过\n"
        "10) 每个抽象概念后面，必须马上跟一个具体例子\n"
        "\n"
        "【绝对禁止（AI腔清单）】\n"
        f"11) 禁用词：{'、'.join(AI_BANNED)}\n"
        "12) 禁句式：不是…而是… / 真正的…是… / 你是不是也… / 试想一下\n"
        "13) 禁止排比刷屏、段尾强行升华、连续使用感叹号\n"
        "14) 不要每段都是一两句的短段轰炸，该展开时老老实实展开\n"
        "15) 正文禁止出现任何网址链接、[[1]] 式引用角标、参考来源标注；"
        "素材内容消化后用自己的话写出来即可\n"
        "\n"
        "【输出】\n"
        "16) 全文 1800-2500 字；只输出文章 Markdown，不要任何解释和分析过程\n"
        "17) 不要照搬搜索素材原句，必须原创"
    )
    user = (
        f"文章类型：{topic.get('type', '干货文')}\n"
        f"选题标题方向：{topic.get('title', '')}\n"
        f"写作角度：{topic.get('angle', '')}\n"
        f"为什么可能爆：{topic.get('why', '')}\n"
        f"目标人群：{topic.get('audience', '')}\n"
    )
    if refs_block:
        user += f"\n{refs_block}\n"
    if user_extra:
        user += f"补充要求：{user_extra}\n"
    user += "请直接输出成稿 Markdown。"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def detect_ai_phrases(md_text: str) -> list[str]:
    """检测成稿中残留的 AI 腔用词，返回命中的词表。"""
    return [p for p in AI_BANNED if p in md_text]


# 引用角标链接：[[1]](url) / [1](url)，公众号里点不了还破坏阅读
_CITE_LINK_RE = re.compile(r"\[\[?\d+\]?\]\(https?://[^)]+\)")
# 裸角标 [1]：排除紧跟在 ASCII 标识符/右括号后的情况（arr[1]、lst[0] 是代码下标；
# 注意不能用 \w——它匹配中文，会把「如图[1]」这类真角标也保护掉）
_BARE_CITE_RE = re.compile(r"(?<![A-Za-z0-9_\]\[])\[\d{1,2}\](?!\()")


def scrub_citations(md_text: str) -> str:
    """删除正文里的引用角标（模型偶尔不听话时兜底），保持句子通顺。

    代码块与行内代码先抠出保护：`arr[1]` 这类数组下标不能被当角标删掉。
    """
    slots: list[str] = []

    def _stash(m: re.Match) -> str:
        slots.append(m.group(0))
        return f"\x00CODE{len(slots) - 1}\x00"

    text = re.sub(r"```.*?```", _stash, md_text, flags=re.S)   # 围栏代码块
    text = re.sub(r"`[^`\n]+`", _stash, text)                   # 行内代码
    text = _CITE_LINK_RE.sub("", text)
    text = _BARE_CITE_RE.sub("", text)
    # 角标删掉后标点前可能剩空格
    text = re.sub(r"[ \t]+([，。；！？、）])", r"\1", text)
    for idx, code in enumerate(slots):
        text = text.replace(f"\x00CODE{idx}\x00", code)
    return text


def local_fallback_article(topic: dict) -> str:
    title = topic.get("title") or "未命名选题"
    return f"""# {title}

上周有个朋友跟我说，他不是不努力，是努力了很久，生活还是卡住。

这种感觉很多人都懂。不是懒，是力气耗在无效动作上。

## 先看清一件事

{topic.get('angle') or '先把问题拆开，再谈方法。'}

很多人习惯先焦虑，再找课，再收藏。收藏清单越来越长，行动却越来越少。

## 可以马上做的三步

1. 写下你现在最卡住的1个具体问题（越具体越好）。
2. 找一个能验证结果的小动作，今天就做，时间不超过1小时。
3. 用结果复盘：有没有人反馈、有没有数据变化、有没有赚到第一块钱。

## 一个小例子

有人把「我会做表」这件事挂出去接单。第一单只赚几十块，但反馈比上十节课都准。
因为他第一次知道：市场要的不是证书，是能不能把事办成。

## 最后

{topic.get('why') or '这不是鸡汤，是可执行的下一步。'}

你不需要一次做完美。你需要先开始那个最小的动作。

今晚就做一件事：打开备忘录，写下「明天1小时我要完成的最小交付」。
"""


# ---------------- 智能配图 ----------------
# 技术文：只在真有流程/架构/对比可画时配示意图，否则不配
# 情感文：配暖色氛围图，不必与段落内容严格对应
ILLUST_SIZE = (900, 506)  # 正文配图 16:9，窄于封面


def plan_illustrations(md_text: str, max_n: int = 3) -> list[dict]:
    """让大模型判断文章类型并给出配图方案。技术文没有合适位置就返回空。"""
    system = (
        "你是公众号图文编辑，擅长判断文章该不该配图、配什么图。\n"
        "输出严格 JSON 对象，不要 markdown 代码围栏。"
    )
    user = (
        "先判断文章类型，再按规则给配图方案：\n"
        "A. 技术/教程/工具/行业类：只有当文中确实有适合画成示意图的流程、架构、"
        "步骤对比时才配（kind=diagram）；找不到合适位置就宁可不配，images 给空数组。\n"
        f"B. 情感/生活/成长/故事类：配 1-{max_n} 张氛围图（kind=mood），"
        "暖色调、生活化、有情绪感染力，不需要和段落内容严格对应，"
        "插在小标题后面或情绪最浓的段落后面。\n\n"
        '输出 JSON：{"category": "tech 或 life", "images": [...]}\n'
        "images 每个元素字段：\n"
        "anchor: 图插在哪一行之后（原样复制文中该行完整文本，别改字）\n"
        "caption: 中文图注一句话；mood 氛围图留空字符串\n"
        "prompt_en: 英文生图提示词。diagram 描述图表元素与构图；"
        "mood 描述画面场景、光线、氛围。都绝对不要出现任何文字内容\n"
        "kind: diagram 或 mood\n\n"
        f"文章：\n{md_text[:6000]}\n"
    )
    data = llm_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.4,
    )
    category = "tech"
    if isinstance(data, dict):
        category = str(data.get("category") or "tech").strip().lower()
        data = data.get("images") or data.get("illustrations") or []
    plans = []
    for it in data if isinstance(data, list) else []:
        if not isinstance(it, dict):
            continue
        anchor = str(it.get("anchor") or "").strip()
        prompt_en = str(it.get("prompt_en") or "").strip()
        if not anchor or not prompt_en:
            continue
        kind = str(it.get("kind") or "").strip().lower()
        if kind not in {"diagram", "mood"}:
            kind = "mood" if category == "life" else "diagram"
        plans.append(
            {
                "anchor": anchor,
                "caption": str(it.get("caption") or "").strip(),
                "prompt_en": prompt_en,
                "kind": kind,
                "category": category,
            }
        )
    return plans[:max_n]


def gen_illustration(prompt_en: str, out_path: Path, kind: str = "diagram") -> Path:
    """按 IMAGE_* 配置生成一张正文配图（复用封面生图通道）。"""
    from generate_cover_ai import fit_cover, gen_dashscope, gen_openai, save_image

    provider = (
        os.getenv("IMAGE_PROVIDER", "") or os.getenv("COVER_PROVIDER", "openai")
    ).strip().lower()
    if kind == "mood":
        full_prompt = (
            "Warm heartfelt editorial illustration for a Chinese lifestyle essay. "
            f"{prompt_en} "
            "Soft golden light, cozy gentle atmosphere, muted warm palette, "
            "painterly texture, emotional and comforting, film-photo mood, "
            "high quality, NO text, NO letters, NO Chinese characters, NO numbers, "
            "no watermark, no logo."
        )[:1800]
    else:
        full_prompt = (
            "Clean flat-design instructional illustration for a Chinese blog article. "
            f"{prompt_en} "
            "Soft modern colors, simple shapes, generous whitespace, infographic feel, "
            "high quality, NO text, NO letters, NO Chinese characters, NO numbers, "
            "no watermark, no logo."
        )[:1800]
    if provider in {"openai", "oai", "dalle", "dall-e"}:
        img = gen_openai(full_prompt)
    elif provider in {"dashscope", "wanx", "wanxiang", "ali", "qwen-image"}:
        img = gen_dashscope(full_prompt)
    else:
        raise RuntimeError(f"IMAGE_PROVIDER={provider} 不支持配图（template 仅限封面）")
    img = fit_cover(img, ILLUST_SIZE)
    return save_image(img, out_path)


def insert_after_anchor(lines: list[str], anchor: str, insert_md: str) -> tuple[list[str], bool]:
    """在 anchor 所在行之后插入图片行。找不到锚点时返回原列表与 False。"""
    anchor_s = anchor.strip()
    idx = -1
    for i, ln in enumerate(lines):
        if ln.strip() == anchor_s:
            idx = i
            break
    if idx < 0 and len(anchor_s) >= 12:
        # 模型偶尔轻微改写锚点：退化为前缀匹配
        prefix = anchor_s[:12]
        for i, ln in enumerate(lines):
            if ln.strip().startswith(prefix):
                idx = i
                break
    if idx < 0:
        return lines, False
    return lines[: idx + 1] + ["", insert_md] + lines[idx + 1 :], True


def illustrate_article(
    md_text: str,
    work_dir: Path,
    *,
    max_n: int | None = None,
    log=print,
) -> tuple[str, list[str]]:
    """为文章智能配图。返回 (新 Markdown, 报告行)。

    技术文只画真示意图（没有就不配）；情感/生活文配暖色氛围图。
    图片存 work_dir/images/，Markdown 相对路径引用；
    发布时由 content_images.replace_content_images 统一转微信图床。
    """
    if max_n is None:
        max_n = int(os.getenv("ARTICLE_MAX_IMAGES", "3") or 3)
    report: list[str] = []
    log("正在分析文章类型与配图点位…")
    plans = plan_illustrations(md_text, max_n=max_n)
    if not plans:
        report.append("[配图] 技术类文章且无合适示意图点位，按约定不配图")
        return md_text, report

    category = plans[0].get("category", "tech")
    kind_label = "暖色氛围图" if category == "life" else "教程示意图"
    log(f"文章类型判定：{'情感/生活' if category == 'life' else '技术/干货'}，配 {kind_label}")

    img_dir = work_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    lines = md_text.replace("\r\n", "\n").split("\n")
    done = 0
    for i, plan in enumerate(plans, 1):
        try:
            log(f"生成配图 {i}/{len(plans)}：{plan['caption'] or plan['anchor'][:20]}")
            out = img_dir / f"illust-{i}.jpg"
            gen_illustration(plan["prompt_en"], out, kind=plan.get("kind", "diagram"))
            img_md = f"![{plan['caption']}](images/illust-{i}.jpg)"
            lines, ok = insert_after_anchor(lines, plan["anchor"], img_md)
            if ok:
                done += 1
                report.append(f"[配图] 已插入：{plan['caption'] or out.name}")
            else:
                report.append(f"[配图] 未找到锚点，跳过：{plan['anchor'][:30]}")
        except Exception as e:
            report.append(f"[配图] 第{i}张失败：{e}")
    report.append(f"[配图] 完成 {done}/{len(plans)} 张（{kind_label}）")
    return "\n".join(lines), report
