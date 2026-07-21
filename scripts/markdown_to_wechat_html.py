#!/usr/bin/env python3
"""把 Markdown 转成公众号兼容的内联样式 HTML（排版引擎）。

微信编辑器的硬约束（决定了这里的实现方式）：
  - 只认内联 style；<style> 块、class、id 都会被剥离 → 所有样式必须写在标签上
  - 非微信域名的 <a> 会被过滤 → 外链统一转成脚注，文末"参考链接"集中列出
  - 外链 <img> 无法显示 → 需先走 uploadimg 换成微信图床（见 content_images.py）
  - 不支持 position / float 布局 → 只用流式排版 + 内联装饰元素

对外接口：
  THEMES                                   可选排版主题（含中文说明）
  markdown_to_wechat_html(md, theme)       正文 Markdown → 内联样式 HTML
  extract_title_and_body(md)               提取标题与正文
  make_digest(md_body, limit)              生成干净的摘要（去 Markdown 符号）
  build_preview_html(title, html, ...)     生成浏览器手机模拟预览页
  file_to_html(path, theme)                文件级便捷入口
"""

from __future__ import annotations

import html as html_mod
import re
from pathlib import Path

# ---------------- 主题定义 ----------------
# h2_mode: badge=序号徽章  center=居中杂志式  bar=左侧色条
THEMES: dict[str, dict[str, str]] = {
    "default": {
        "label": "微信绿·清爽干货",
        "text_color": "#333333",
        "font_size": "16px",
        "line_height": "1.8",
        "letter_spacing": "0.5px",
        "accent": "#07c160",
        "accent_grad": "linear-gradient(135deg,#07c160,#05a854)",
        "h2_mode": "badge",
        "h2_color": "#1a1a1a",
        "h3_color": "#1a1a1a",
        "strong_color": "#07a355",
        "quote_border": "#07c160",
        "quote_bg": "#f2faf5",
        "quote_color": "#5f6b66",
        "code_bg": "#f2f3f5",
        "code_color": "#c7254e",
        "link_color": "#576b95",
        "table_border": "#e8e8e8",
        "th_bg": "#f2faf5",
        "hr_color": "#9fd8b8",
        "caption_color": "#999999",
    },
    "hot": {
        "label": "暖橙红·情绪爆文",
        "text_color": "#3a3a3a",
        "font_size": "16px",
        "line_height": "1.85",
        "letter_spacing": "0.5px",
        "accent": "#ff6b35",
        "accent_grad": "linear-gradient(135deg,#ff8a3d,#ef4444)",
        "h2_mode": "badge",
        "h2_color": "#27272a",
        "h3_color": "#b91c1c",
        "strong_color": "#e64a19",
        "quote_border": "#ff8a3d",
        "quote_bg": "#fff7f2",
        "quote_color": "#8a6d60",
        "code_bg": "#fdf2ee",
        "code_color": "#c2410c",
        "link_color": "#576b95",
        "table_border": "#f0e0d6",
        "th_bg": "#fff3ec",
        "hr_color": "#f5c9b0",
        "caption_color": "#a1887f",
    },
    "elegant": {
        "label": "静谧蓝·杂志深度",
        "text_color": "#3b3f4a",
        "font_size": "16px",
        "line_height": "1.9",
        "letter_spacing": "0.6px",
        "accent": "#4c6ef5",
        "accent_grad": "linear-gradient(135deg,#5c7cfa,#4263eb)",
        "h2_mode": "center",
        "h2_color": "#1f2937",
        "h3_color": "#364fc7",
        "strong_color": "#4263eb",
        "quote_border": "#4c6ef5",
        "quote_bg": "#f3f6ff",
        "quote_color": "#5c637a",
        "code_bg": "#eef2ff",
        "code_color": "#3b5bdb",
        "link_color": "#576b95",
        "table_border": "#dfe4f5",
        "th_bg": "#eef2ff",
        "hr_color": "#b9c5f0",
        "caption_color": "#8b93a7",
    },
    "minimal": {
        "label": "黑白灰·极简高级",
        "text_color": "#2f2f2f",
        "font_size": "16px",
        "line_height": "1.85",
        "letter_spacing": "0.5px",
        "accent": "#111111",
        "accent_grad": "linear-gradient(135deg,#333333,#111111)",
        "h2_mode": "bar",
        "h2_color": "#111111",
        "h3_color": "#111111",
        "strong_color": "#111111",
        "quote_border": "#d9d9d9",
        "quote_bg": "#f7f7f7",
        "quote_color": "#666666",
        "code_bg": "#f2f2f2",
        "code_color": "#d63384",
        "link_color": "#576b95",
        "table_border": "#e5e5e5",
        "th_bg": "#fafafa",
        "hr_color": "#dddddd",
        "caption_color": "#999999",
    },
}

_FONT_STACK = (
    "-apple-system,BlinkMacSystemFont,'Helvetica Neue','PingFang SC',"
    "'Hiragino Sans GB','Microsoft YaHei',Arial,sans-serif"
)
_MONO_STACK = "Menlo,Consolas,'Courier New',monospace"

# 代码块统一深色（One Dark 配色）：四套主题下都有高级感且对比充分
_CODE_BLOCK_BG = "#282c34"
_CODE_BLOCK_TEXT = "#abb2bf"
_CODE_DOTS_BAR = (
    '<section style="padding:10px 14px;background:#21252b;'
    'border-radius:10px 10px 0 0;line-height:1;font-size:0;">'
    '<span style="display:inline-block;width:11px;height:11px;border-radius:50%;'
    'background:#fc625d;"></span>'
    '<span style="display:inline-block;width:11px;height:11px;border-radius:50%;'
    'background:#fdbc40;margin-left:7px;"></span>'
    '<span style="display:inline-block;width:11px;height:11px;border-radius:50%;'
    'background:#35cd4b;margin-left:7px;"></span></section>'
)

# 盘古之白：中文与英文/数字之间加空格，仅作用于渲染输出，不改动原稿
_PANGU_CJK_LATIN = re.compile(r"([一-鿿])([A-Za-z0-9])")
_PANGU_LATIN_CJK = re.compile(r"([A-Za-z0-9])([一-鿿])")


def _pangu(text: str) -> str:
    text = _PANGU_CJK_LATIN.sub(r"\1 \2", text)
    return _PANGU_LATIN_CJK.sub(r"\1 \2", text)

# 微信自家域名的链接不会被过滤，可保留为真实 <a>
_WECHAT_LINK_RE = re.compile(r"^https?://mp\.weixin\.qq\.com/", re.I)

_IMG_LINE_RE = re.compile(r'^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)$')
_OL_ITEM_RE = re.compile(r"^(\d+)[.、)]\s+(.+)$")
_UL_ITEM_RE = re.compile(r"^[-*+]\s+(.+)$")
_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$")


class _Renderer:
    """持有主题配置与文档级状态（脚注、小标题计数）。"""

    def __init__(self, cfg: dict[str, str]):
        self.t = cfg
        self.footnotes: list[tuple[str, str]] = []  # (显示文本, url)
        self.h2_count = 0

    # ---------- 行内格式 ----------
    # 编辑器工具栏生成的内联样式 span：只透传严格白名单内的属性
    # 注意 escape(quote=False) 不转义双引号，这里直接匹配 "
    _SAFE_SPAN_RE = re.compile(
        r'&lt;span style="((?:\s*(?:font-family|font-size|color)\s*:[^;"<>&]{1,60};?)+)"&gt;'
        r'(.*?)&lt;/span&gt;',
        re.S,
    )

    def _restore_safe_spans(self, text: str) -> str:
        """把被转义的白名单 span 还原为真实标签（微信支持内联样式）。"""
        def _repl(m: re.Match) -> str:
            style = m.group(1).strip().rstrip(";")
            return f'<span style="{style};">{m.group(2)}</span>'

        return self._SAFE_SPAN_RE.sub(_repl, text)

    def inline(self, text: str) -> str:
        """转义 + 行内 Markdown。顺序：转义 → 白名单 span 还原 → 行内代码保护 → 图片 → 链接 → 粗斜删。"""
        text = html_mod.escape(text, quote=False)

        # 编辑器字体/字号/颜色 span（其他任何 HTML 保持转义原样显示）
        text = self._restore_safe_spans(text)

        # 行内代码先抠出来保护，避免内部 * _ 被再处理
        code_slots: list[str] = []

        def _stash_code(m: re.Match) -> str:
            code_slots.append(m.group(1))
            return f"\x00CODE{len(code_slots) - 1}\x00"

        text = re.sub(r"`([^`]+)`", _stash_code, text)

        # 盘古之白：中英文之间加空隙（行内代码已被占位符保护，不受影响）
        text = _pangu(text)

        # 行内图片（罕见，正文图片通常独占一行）
        text = re.sub(
            r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)',
            lambda m: f'<img src="{m.group(2)}" alt="{m.group(1)}" '
                      f'style="max-width:100%;vertical-align:middle;border-radius:4px;">',
            text,
        )

        # 链接：微信域名保留 <a>，外链转脚注
        text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", self._link_repl, text)

        # 粗体 / 斜体 / 删除线（斜体加主题色：中文字形斜体不明显，颜色补足强调感）
        text = re.sub(
            r"\*\*(.+?)\*\*",
            rf'<strong style="color:{self.t["strong_color"]};font-weight:700;">\1</strong>',
            text,
        )
        text = re.sub(
            r"(?<!\*)\*([^*]+?)\*(?!\*)",
            rf'<em style="font-style:italic;color:{self.t["accent"]};">\1</em>',
            text,
        )
        text = re.sub(
            r"~~(.+?)~~",
            r'<span style="text-decoration:line-through;color:#999999;">\1</span>',
            text,
        )

        # 放回行内代码
        for idx, code in enumerate(code_slots):
            chip = (
                f'<code style="background:{self.t["code_bg"]};color:{self.t["code_color"]};'
                f'padding:2px 6px;border-radius:4px;font-size:14px;'
                f'font-family:{_MONO_STACK};">{code}</code>'
            )
            text = text.replace(f"\x00CODE{idx}\x00", chip)
        return text

    def _link_repl(self, m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if _WECHAT_LINK_RE.match(url):
            return (
                f'<a href="{url}" style="color:{self.t["link_color"]};'
                f'text-decoration:none;border-bottom:1px solid {self.t["link_color"]};">{label}</a>'
            )
        self.footnotes.append((re.sub(r"<[^>]+>", "", label), url))
        n = len(self.footnotes)
        return (
            f'<span style="color:{self.t["link_color"]};">{label}</span>'
            f'<sup style="color:{self.t["accent"]};font-weight:700;font-size:12px;">[{n}]</sup>'
        )

    # ---------- 块级元素 ----------
    def paragraph(self, text: str) -> str:
        return (
            f'<p style="margin:16px 0;font-size:{self.t["font_size"]};'
            f'line-height:{self.t["line_height"]};color:{self.t["text_color"]};'
            f'letter-spacing:{self.t["letter_spacing"]};text-align:justify;">'
            f"{self.inline(text)}</p>"
        )

    def h2(self, text: str) -> str:
        self.h2_count += 1
        num = f"{self.h2_count:02d}"
        mode = self.t["h2_mode"]
        inner = self.inline(text)
        if mode == "center":
            return (
                '<h2 style="margin:36px 0 18px;text-align:center;font-weight:400;">'
                f'<span style="display:block;font-size:12px;color:{self.t["accent"]};'
                f'letter-spacing:4px;margin-bottom:6px;">{num}</span>'
                f'<span style="display:inline-block;font-size:19px;font-weight:700;'
                f'color:{self.t["h2_color"]};padding:0 4px 8px;line-height:1.4;'
                f'border-bottom:3px solid {self.t["accent"]};">{inner}</span></h2>'
            )
        if mode == "bar":
            return (
                f'<h2 style="margin:32px 0 16px;font-size:18px;font-weight:700;'
                f'color:{self.t["h2_color"]};border-left:4px solid {self.t["accent"]};'
                f'padding-left:12px;line-height:1.5;">{inner}</h2>'
            )
        # badge（默认）
        return (
            '<h2 style="margin:32px 0 16px;font-size:18px;line-height:1.5;">'
            f'<span style="display:inline-block;background:{self.t["accent_grad"]};'
            f'color:#ffffff;font-size:13px;font-weight:700;padding:3px 10px;'
            f'border-radius:4px;margin-right:10px;vertical-align:middle;">{num}</span>'
            f'<span style="font-weight:700;color:{self.t["h2_color"]};'
            f'vertical-align:middle;">{inner}</span></h2>'
        )

    def h3(self, text: str) -> str:
        inner = self.inline(text)
        if self.t["h2_mode"] == "bar":  # 极简主题：小标题不加装饰
            return (
                f'<h3 style="margin:24px 0 12px;font-size:16px;font-weight:700;'
                f'color:{self.t["h3_color"]};">{inner}</h3>'
            )
        return (
            '<h3 style="margin:24px 0 12px;font-size:16px;font-weight:700;'
            f'color:{self.t["h3_color"]};">'
            f'<span style="display:inline-block;width:6px;height:16px;'
            f'background:{self.t["accent"]};border-radius:3px;margin-right:8px;'
            f'vertical-align:-2px;"></span>{inner}</h3>'
        )

    def h4(self, text: str) -> str:
        return (
            f'<h4 style="margin:20px 0 10px;font-size:15px;font-weight:700;'
            f'color:{self.t["h3_color"]};">{self.inline(text)}</h4>'
        )

    def bullet_list(self, items: list[str], ordered: bool) -> str:
        tag = "ol" if ordered else "ul"
        # 技巧：li 用主题色让"项目符号/序号"带色，内容用 span 恢复正文色
        lis = "".join(
            f'<li style="margin:8px 0;font-size:{self.t["font_size"]};'
            f'line-height:{self.t["line_height"]};color:{self.t["accent"]};">'
            f'<span style="color:{self.t["text_color"]};'
            f'letter-spacing:{self.t["letter_spacing"]};text-align:justify;">'
            f"{self.inline(x)}</span></li>"
            for x in items
        )
        return f'<{tag} style="margin:16px 0;padding-left:1.6em;">{lis}</{tag}>'

    def blockquote(self, lines: list[str]) -> str:
        body = "<br>".join(self.inline(x) for x in lines)
        # 卡片式金句：大引号装饰 + 圆角 + 主题色细线
        return (
            f'<blockquote style="margin:20px 0;padding:14px 18px 16px;'
            f'border-left:4px solid {self.t["quote_border"]};background:{self.t["quote_bg"]};'
            f'border-radius:0 12px 12px 0;font-size:15px;line-height:1.9;">'
            f'<span style="display:block;font-size:26px;line-height:1;'
            f'color:{self.t["quote_border"]};font-family:Georgia,serif;'
            f'margin-bottom:2px;">❝</span>'
            f'<span style="color:{self.t["quote_color"]};text-align:justify;'
            f'display:block;">{body}</span></blockquote>'
        )

    def code_block(self, lines: list[str]) -> str:
        code = html_mod.escape("\n".join(lines))
        # Mac 窗口三点装饰 + One Dark 深色底：技术文的高级感来源
        return (
            '<section style="margin:20px 0;border-radius:10px;overflow:hidden;'
            'box-shadow:0 4px 14px rgba(0,0,0,0.12);">'
            f"{_CODE_DOTS_BAR}"
            f'<pre style="margin:0;padding:14px 16px;background:{_CODE_BLOCK_BG};'
            f'overflow-x:auto;font-size:13px;line-height:1.75;">'
            f'<code style="font-family:{_MONO_STACK};color:{_CODE_BLOCK_TEXT};'
            f'white-space:pre;">{code}</code></pre></section>'
        )

    def figure(self, src: str, alt: str = "") -> str:
        cap = ""
        if alt.strip():
            cap = (
                f'<p style="margin:10px 0 0;font-size:13px;'
                f'color:{self.t["caption_color"]};text-align:center;">'
                f"▲ {html_mod.escape(alt.strip())}</p>"
            )
        return (
            '<section style="margin:24px 0;text-align:center;">'
            f'<img src="{src}" alt="{html_mod.escape(alt)}" '
            'style="max-width:100%;border-radius:10px;display:block;margin:0 auto;'
            'box-shadow:0 4px 16px rgba(0,0,0,0.10);">'
            f"{cap}</section>"
        )

    def hr(self) -> str:
        return (
            f'<p style="margin:30px 0;text-align:center;color:{self.t["hr_color"]};'
            'font-size:13px;letter-spacing:8px;">· · ·</p>'
        )

    def table(self, header: list[str], rows: list[list[str]]) -> str:
        tb = self.t["table_border"]
        ths = "".join(
            f'<th style="border:1px solid {tb};background:{self.t["th_bg"]};'
            f'padding:9px 12px;color:{self.t["h2_color"]};font-weight:700;'
            f'text-align:left;">{self.inline(c)}</th>'
            for c in header
        )
        trs = ""
        for ri, row in enumerate(rows):
            # 斑马纹：偶数行铺浅底色，长表格更易逐行阅读
            row_bg = f"background:{self.t['th_bg']};" if ri % 2 == 1 else ""
            tds = "".join(
                f'<td style="border:1px solid {tb};padding:9px 12px;{row_bg}'
                f'color:{self.t["text_color"]};">{self.inline(c)}</td>'
                for c in row
            )
            trs += f"<tr>{tds}</tr>"
        return (
            '<section style="margin:20px 0;overflow-x:auto;">'
            f'<table style="border-collapse:collapse;width:100%;font-size:14px;'
            f'line-height:1.7;"><thead><tr>{ths}</tr></thead>'
            f"<tbody>{trs}</tbody></table></section>"
        )

    def end_mark(self) -> str:
        """文末收尾装饰：细线 + END 标记（公众号文章通用惯例）。"""
        return (
            '<section style="margin:40px 0 8px;text-align:center;">'
            f'<span style="display:inline-block;width:36px;height:1px;'
            f'background:{self.t["hr_color"]};vertical-align:middle;"></span>'
            f'<span style="display:inline-block;margin:0 12px;font-size:12px;'
            f'letter-spacing:3px;color:{self.t["accent"]};font-weight:700;'
            f'vertical-align:middle;">END</span>'
            f'<span style="display:inline-block;width:36px;height:1px;'
            f'background:{self.t["hr_color"]};vertical-align:middle;"></span></section>'
        )

    def footnote_section(self) -> str:
        if not self.footnotes:
            return ""
        lines = "".join(
            f'<p style="margin:6px 0;font-size:12px;color:{self.t["caption_color"]};'
            f'line-height:1.7;word-break:break-all;">[{i}] '
            f"{html_mod.escape(text)}：{html_mod.escape(url)}</p>"
            for i, (text, url) in enumerate(self.footnotes, 1)
        )
        return (
            f'<section style="margin-top:36px;padding-top:14px;'
            f'border-top:1px dashed {self.t["table_border"]};">'
            f'<p style="margin:0 0 10px;font-size:14px;font-weight:700;'
            f'color:{self.t["h2_color"]};">参考链接</p>{lines}</section>'
        )


def _split_table_row(line: str) -> list[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [c.strip() for c in row.split("|")]


def _strip_outer_fence(md: str) -> str:
    """剥掉包住整篇文章的代码围栏。

    LLM 常把全文包在 ```markdown ... ``` 里输出，此时渲染引擎会把
    整篇当一个大代码块 → 草稿箱里全文被框在代码框里。仅当围栏在
    首尾且中间没有成对围栏时才剥（正文里的真代码块不受影响）。
    """
    text = md.strip()
    m = re.match(r"^```[a-zA-Z]*\s*\n(.*)\n```\s*$", text, flags=re.S)
    if not m:
        return md
    inner = m.group(1)
    # 中间还有围栏说明首尾的 ``` 各自属于内部代码块，不能剥
    if "```" in inner:
        return md
    return inner


def markdown_to_wechat_html(md: str, theme: str = "default") -> str:
    """正文 Markdown → 公众号可粘贴的内联样式 HTML。"""
    cfg = THEMES.get(theme, THEMES["default"])
    r = _Renderer(cfg)
    md = _strip_outer_fence(md)
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    blocks: list[str] = []
    ul_buf: list[str] = []
    ol_buf: list[str] = []
    quote_buf: list[str] = []
    code_buf: list[str] = []
    in_code = False

    def flush_lists() -> None:
        nonlocal ul_buf, ol_buf
        if ul_buf:
            blocks.append(r.bullet_list(ul_buf, ordered=False))
            ul_buf = []
        if ol_buf:
            blocks.append(r.bullet_list(ol_buf, ordered=True))
            ol_buf = []

    def flush_quote() -> None:
        nonlocal quote_buf
        if quote_buf:
            blocks.append(r.blockquote(quote_buf))
            quote_buf = []

    def flush_all() -> None:
        flush_lists()
        flush_quote()

    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i].rstrip()
        stripped = raw.strip()

        # ---- 代码块 ----
        if in_code:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                blocks.append(r.code_block(code_buf))
                code_buf = []
                in_code = False
            else:
                code_buf.append(lines[i])
            i += 1
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            flush_all()
            in_code = True
            code_buf = []
            i += 1
            continue

        # ---- 空行 ----
        if not stripped:
            flush_all()
            i += 1
            continue

        # ---- 表格 ----
        if stripped.startswith("|") and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1].strip()):
            flush_all()
            header = _split_table_row(stripped)
            i += 2
            rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                cells = _split_table_row(lines[i].strip())
                # 列数对齐表头
                if len(cells) < len(header):
                    cells += [""] * (len(header) - len(cells))
                rows.append(cells[: len(header)])
                i += 1
            blocks.append(r.table(header, rows))
            continue

        # ---- 标题 ----
        if stripped.startswith("#### "):
            flush_all()
            blocks.append(r.h4(stripped[5:].strip()))
            i += 1
            continue
        if stripped.startswith("### "):
            flush_all()
            blocks.append(r.h3(stripped[4:].strip()))
            i += 1
            continue
        if stripped.startswith("## "):
            flush_all()
            blocks.append(r.h2(stripped[3:].strip()))
            i += 1
            continue
        if stripped.startswith("# "):
            # 一级标题已进草稿 title 字段，正文中出现时按 h2 呈现
            flush_all()
            blocks.append(r.h2(stripped[2:].strip()))
            i += 1
            continue

        # ---- 引用 ----
        if stripped.startswith(">"):
            flush_lists()
            quote_buf.append(stripped.lstrip(">").strip())
            i += 1
            continue

        # ---- 独立成行的图片 ----
        m = _IMG_LINE_RE.match(stripped)
        if m:
            flush_all()
            blocks.append(r.figure(m.group(2), m.group(1)))
            i += 1
            continue

        # ---- 分隔线 ----
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            flush_all()
            blocks.append(r.hr())
            i += 1
            continue

        # ---- 列表 ----
        m = _UL_ITEM_RE.match(stripped)
        if m:
            flush_quote()
            if ol_buf:
                flush_lists()
            ul_buf.append(m.group(1).strip())
            i += 1
            continue
        m = _OL_ITEM_RE.match(stripped)
        if m:
            flush_quote()
            if ul_buf:
                flush_lists()
            ol_buf.append(m.group(2).strip())
            i += 1
            continue

        # ---- 普通段落 ----
        flush_all()
        blocks.append(r.paragraph(stripped))
        i += 1

    flush_all()
    if in_code and code_buf:
        blocks.append(r.code_block(code_buf))
    # 收尾顺序：正文 → END 标记 → 参考链接（附录性质，放最后）
    blocks.append(r.end_mark())
    blocks.append(r.footnote_section())

    body = "\n".join(b for b in blocks if b)
    return (
        f'<section style="max-width:100%;margin:0 auto;padding:0 4px;'
        f'font-family:{_FONT_STACK};">{body}</section>'
    )


def extract_title_and_body(md: str) -> tuple[str, str]:
    """从 Markdown 提取标题与正文。优先取第一个 # 一级标题。"""
    lines = md.replace("\r\n", "\n").split("\n")
    title = ""
    body_lines: list[str] = []
    for idx, line in enumerate(lines):
        if line.startswith("# ") and not title:
            title = line[2:].strip()
            body_lines = lines[idx + 1 :]
            break
    if not title:
        # 没有一级标题，用第一行非空内容
        for line in lines:
            if line.strip():
                title = re.sub(r"^#+\s*", "", line.strip())
                break
        body_lines = lines
    # 草稿接口 title 上限 64 字
    return title[:64], "\n".join(body_lines).strip()


def make_digest(md_body: str, limit: int = 54) -> str:
    """从正文 Markdown 生成干净摘要（草稿 digest 字段，上限 120 字）。"""
    text = re.sub(r"```.*?```", " ", md_body, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#>*`~|_]", " ", text)
    text = re.sub(r"^\s*[-+]\s+", "", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max(1, min(limit, 120))]


def build_preview_html(
    title: str,
    content_html: str,
    *,
    author: str = "",
    theme_label: str = "",
) -> str:
    """生成一张手机宽度的本地预览页（浏览器打开，模拟公众号阅读效果）。"""
    meta_parts = [p for p in (author, theme_label, "本地预览，非最终效果") if p]
    meta = " · ".join(meta_parts)
    safe_title = html_mod.escape(title)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>预览 - {safe_title}</title>
</head>
<body style="margin:0;padding:24px 0;background:#ededed;font-family:{_FONT_STACK};">
<div style="max-width:414px;margin:0 auto;background:#ffffff;border-radius:8px;
            box-shadow:0 2px 16px rgba(0,0,0,0.08);overflow:hidden;">
  <div style="padding:28px 20px 4px;">
    <h1 style="margin:0 0 10px;font-size:22px;font-weight:700;color:#1a1a1a;line-height:1.4;">{safe_title}</h1>
    <p style="margin:0;font-size:13px;color:#999999;">{html_mod.escape(meta)}</p>
  </div>
  <div style="padding:8px 20px 48px;">
{content_html}
  </div>
</div>
<p style="text-align:center;font-size:12px;color:#b0b0b0;margin-top:16px;">
  宽度按 iPhone 竖屏模拟 · 实际效果以公众号后台预览为准
</p>
</body>
</html>
"""


def file_to_html(md_path: str | Path, theme: str = "default") -> tuple[str, str]:
    text = Path(md_path).read_text(encoding="utf-8")
    title, body_md = extract_title_and_body(text)
    html_content = markdown_to_wechat_html(body_md, theme=theme)
    return title, html_content
