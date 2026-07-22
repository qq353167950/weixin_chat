#!/usr/bin/env python3
"""本地冒烟自测（全部离线，不调用任何外部 API）。

覆盖：
  1. 排版引擎：四套主题、标题/引用/列表/代码/表格/图片/链接脚注/删除线/分隔线
  2. 标题与正文提取：常规、无一级标题、超长标题截断
  3. 摘要生成：去 Markdown 符号、长度限制
  4. 预览页生成
  5. 本地模板封面（Pillow，不联网）
  6. 正文图片统计（不实际上传）
  7. 写作时就地配图：gen: 占位标记的生成/替换/超限裁剪/未配置时清标记（生图打桩）
  8. 序号小标题不叠加 01 徽章

运行：
  python scripts/selftest.py
返回码 0 = 全部通过；非 0 = 有失败项。
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from content_images import count_external_images
from article_writer import scrub_citations
from generate_cover import generate_cover
from markdown_to_wechat_html import (
    THEMES,
    build_preview_html,
    extract_title_and_body,
    make_digest,
    markdown_to_wechat_html,
)

FIXTURE_MD = """# 测试文章标题

开头段落，包含**加粗**、*斜体*、~~删除线~~、`行内代码`。

这是[微信链接](https://mp.weixin.qq.com/s/abc)和[外部链接](https://example.com/page)。

## 第一个小标题

> 这是一句引用金句。
> 第二行引用。

- 无序列表一
- 无序列表二

1. 有序步骤一
2. 有序步骤二
3. 有序步骤三

### 三级标题

#### 四级标题

```python
def hello():
    print("world")
```

| 列A | 列B |
| --- | --- |
| 甲 | 乙 |
| 丙 | 丁 |

![示例图片](https://example.com/img.png)

---

结尾段落。
"""

_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "通过" if cond else "失败"
    print(f"  [{mark}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(f"{name}: {detail}")


def test_extract_title() -> None:
    print("[1] 标题与正文提取")
    title, body = extract_title_and_body(FIXTURE_MD)
    check("提取一级标题", title == "测试文章标题", f"实际: {title}")
    check("正文不含标题行", not body.startswith("#"), body[:30])

    t2, _ = extract_title_and_body("没有标题的第一行\n\n正文")
    check("无一级标题时取首行", t2 == "没有标题的第一行", t2)

    long_md = "# " + "长" * 100 + "\n\n正文"
    t3, _ = extract_title_and_body(long_md)
    check("超长标题截到 64 字", len(t3) == 64, f"长度: {len(t3)}")


def test_render_all_themes() -> None:
    print("[2] 排版引擎（四套主题）")
    _, body = extract_title_and_body(FIXTURE_MD)
    for theme in THEMES:
        html = markdown_to_wechat_html(body, theme=theme)
        ok = (
            html.startswith("<section")
            and "<h2" in html
            and "<h3" in html
            and "<h4" in html
            and "<blockquote" in html
            and "<ul" in html
            and "<ol" in html
            and "<pre" in html
            and "<table" in html
            and "<img" in html
            and "参考链接" in html          # 外链转脚注
            and "mp.weixin.qq.com" in html  # 微信链接保留 <a>
            and "line-through" in html      # 删除线
            and "· · ·" in html             # 分隔线
            and "class=" not in html        # 微信兼容：不允许 class
        )
        check(f"主题 {theme}（{THEMES[theme]['label']}）", ok)

    html = markdown_to_wechat_html(body, theme="default")
    check("外链不产生 <a href=\"https://example.com\">",
          '<a href="https://example.com' not in html)
    check("有序列表保留为 <ol>", "<ol" in html)


def test_typography() -> None:
    print("[2b] 排版细节（舒适度增强）")
    md = (
        "我在用AI写作3篇文章，效率提升200%。\n\n"
        "> 金句引用测试\n\n"
        "```python\nprint('hi')\n```\n\n"
        "| 列A | 列B |\n| --- | --- |\n| 甲 | 乙 |\n| 丙 | 丁 |\n| 戊 | 己 |\n"
    )
    html = markdown_to_wechat_html(md, theme="default")
    check("盘古之白（中英文之间加空格）",
          "用 AI 写作 3 篇" in html and "提升 200%" in html)
    check("段落两端对齐", "text-align:justify" in html)
    check("代码块 Mac 深色风", "#282c34" in html and "#fc625d" in html)
    check("引用卡片带大引号装饰", "❝" in html)
    check("表格斑马纹（第二行有底色）",
          html.count("background:#f2faf5") >= 2)  # 表头 + 偶数行
    check("文末 END 标记", ">END</span>" in html)
    check("行内代码不受盘古之白影响",
          "print" in markdown_to_wechat_html("行内`代码abc测试`保持原样", theme="default")
          or "代码abc测试" in markdown_to_wechat_html("行内`代码abc测试`保持原样", theme="default"))


def test_digest() -> None:
    print("[3] 摘要生成")
    _, body = extract_title_and_body(FIXTURE_MD)
    d = make_digest(body)
    ok_chars = all(c not in d for c in "#>*`|[]!~")
    check("摘要无 Markdown 符号", ok_chars, d)
    check("摘要非空且 ≤54 字", 0 < len(d) <= 54, f"长度: {len(d)}")
    check("空正文不崩溃", make_digest("") == "")


def test_preview() -> None:
    print("[4] 预览页生成")
    _, body = extract_title_and_body(FIXTURE_MD)
    html = markdown_to_wechat_html(body, theme="default")
    page = build_preview_html("测试标题", html, author="作者", theme_label="主题：default")
    check("预览页含标题与正文", "测试标题" in page and "<section" in page)
    check("预览页为完整 HTML 文档", page.strip().startswith("<!DOCTYPE html>"))


def test_template_cover() -> None:
    print("[5] 本地模板封面（Pillow）")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "cover.jpg"
        generate_cover("测试封面标题：普通人如何靠副业月入过万", out, theme="default")
        check("封面文件生成", out.exists() and out.stat().st_size > 1000)
        from PIL import Image

        with Image.open(out) as img:
            check("封面尺寸 900x383", img.size == (900, 383), str(img.size))


def test_count_images() -> None:
    print("[6] 正文图片统计")
    html = (
        '<img src="https://example.com/a.png">'
        '<img src="https://mmbiz.qpic.cn/xx.jpg">'
        '<img src="data:image/png;base64,xxx">'
        '<img src="local/pic.jpg">'
    )
    check("仅统计需转存图片（外链+本地）", count_external_images(html) == 2,
          str(count_external_images(html)))


def test_scrub_citations() -> None:
    print("[7] 引用角标清除")
    s = "数字还在涨。[[1]](https://x.com/a/1) 稳定了。[2](https://x.com/b) 裸的[3] 。"
    r = scrub_citations(s)
    check("角标链接全部移除", "https://" not in r and "[[1]]" not in r and "[3]" not in r, r)
    keep = scrub_citations("看[这篇](https://mp.weixin.qq.com/s/x)就够")
    check("正常文字链接保留", "mp.weixin.qq.com" in keep and "[这篇]" in keep, keep)


def test_version_compare() -> None:
    print("[8] 版本号比较（更新链路基石）")
    from version import compare_version

    check("1.2.3 < 1.2.4", compare_version("1.2.3", "1.2.4") == -1)
    check("1.10.0 > 1.9.9", compare_version("1.10.0", "1.9.9") == 1)
    check("相等", compare_version("1.6.3", "1.6.3") == 0)
    check("带 v 前缀", compare_version("v1.2.3", "1.2.4") == -1)
    check("带后缀 -beta", compare_version("1.2.3-beta", "1.2.3") == 0)


def test_friendly_changelog() -> None:
    print("[9] Release 说明条目化")
    from gui_server import _friendly_changelog

    items = _friendly_changelog("- 新增：功能A\n- 修复：[问题B](https://x.com) by @bot\n说明段落忽略")
    check("提取条目并剥链接署名", items == ["新增：功能A", "修复：问题B"], str(items))
    check("表格正文退化为通用文案",
          _friendly_changelog("| 文件 | 平台 |\n|---|---|\n| a.exe | Win |") == ["其他修复与优化"])


def test_wechat_errcode() -> None:
    print("[10] 微信错误码中文翻译")
    from wechat_client import _errcode_message

    check("40164 IP 白名单提示", "IP" in _errcode_message({"errcode": 40164, "errmsg": "x"}))
    check("未知码保留原始信息", "errcode=99999" in _errcode_message({"errcode": 99999, "errmsg": "y"}))


def test_strip_fence() -> None:
    print("[11] 整篇代码围栏剥离")
    from markdown_to_wechat_html import _strip_outer_fence

    check("剥外层围栏", _strip_outer_fence("```markdown\n# T\n正文\n```") == "# T\n正文")
    inner = "# T\n\n```python\ncode\n```\n\n尾"
    check("内部真代码块不动", _strip_outer_fence(inner) == inner)


def test_inline_images() -> None:
    print("[12] 写作时就地配图（gen: 占位标记）")
    import os

    import article_writer as aw

    sample = (
        "# 标题\n\n引言。\n\n"
        "![漏斗图](gen:diagram;a flat teal funnel)\n\n"
        "## 第一节\n\n正文。\n\n"
        "![](gen:mood;warm cozy scene)\n\n"
        "![多余](gen:diagram;extra)\n"
    )
    # 未配置生图 → 清标记，绝不留破图
    os.environ["ARTICLE_ILLUSTRATE"] = "1"
    os.environ.pop("IMAGE_PROVIDER", None)
    os.environ.pop("COVER_PROVIDER", None)
    with tempfile.TemporaryDirectory() as d:
        md, _ = aw.resolve_inline_images(sample, Path(d), log=lambda *a: None)
    check("未配置生图时清除所有占位", "gen:" not in md and "![" not in md)

    # 生图可用（打桩）→ 生成/替换/超限裁剪
    os.environ["IMAGE_PROVIDER"] = "openai"
    os.environ["ARTICLE_MAX_IMAGES"] = "2"
    calls: list[str] = []
    orig = aw.gen_illustration

    def fake_gen(prompt_en, out_path, kind="diagram"):
        calls.append(kind)
        out_path.write_bytes(b"x")
        return out_path

    aw.gen_illustration = fake_gen
    try:
        with tempfile.TemporaryDirectory() as d:
            md, _ = aw.resolve_inline_images(sample, Path(d), log=lambda *a: None)
    finally:
        aw.gen_illustration = orig
    check("受 max_n 约束只生成 2 张", len(calls) == 2, f"实际 {len(calls)}")
    check("类型解析 diagram/mood", calls == ["diagram", "mood"], str(calls))
    check("替换为本地图片引用", "images/illust-1.jpg" in md and "images/illust-2.jpg" in md)
    check("超限的第 3 张被清除", "images/illust-3.jpg" not in md and "gen:" not in md)


def test_ordinal_heading() -> None:
    print("[13] 序号小标题不叠加 01 徽章")
    body = (
        "## 第一节：写文\n\n正文。\n\n"
        "## 普通小标题\n\n正文。\n\n"
        "## 3. 步骤三\n\n正文。\n"
    )
    html = markdown_to_wechat_html(body, theme="default")
    # badge 序号胶囊的特征片段
    badges = html.count("padding:3px 10px;")
    check("仅普通小标题带徽章", badges == 1, f"徽章数 {badges}")
    check("序号标题走左色条", html.count("border-left:4px solid") >= 2)


def main() -> int:
    print("=" * 56)
    print("  本地冒烟自测（离线，不调用外部 API）")
    print("=" * 56)
    tests = [
        test_extract_title,
        test_render_all_themes,
        test_typography,
        test_digest,
        test_preview,
        test_template_cover,
        test_count_images,
        test_scrub_citations,
        test_version_compare,
        test_friendly_changelog,
        test_wechat_errcode,
        test_strip_fence,
        test_inline_images,
        test_ordinal_heading,
    ]
    for t in tests:
        try:
            t()
        except Exception as e:
            _failures.append(f"{t.__name__} 异常: {e}")
            print(f"  [异常] {t.__name__}: {e}")
            traceback.print_exc()
        print()

    if _failures:
        print(f"结果：{len(_failures)} 项失败")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("结果：全部通过 ✔")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
