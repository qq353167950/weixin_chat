# 一键：公众号写作 → 排版 → 草稿箱

> **两种用法，推荐图形界面：**
> - **图形界面**：双击 `GUI.bat`，浏览器打开苹果风格操作页（选题 → 文章 → 封面 → 预览发布 → 设置）
> - **命令行**：双击 `START_HERE.bat`，终端交互式全流程
>
> 首次使用：填 `.env`（或直接在 GUI 设置页填）→ `show_my_ip.bat` 查 IP → **自己**去公众号后台加 IP 白名单

目标链路：

```text
真实联网搜索热点 → 大模型整理候选选题 → 你来挑
        ↓
抓取参考文章原文学语感（反AI腔约束）→ 大模型写文章（可改可重写）
        ↓
可选：为步骤/案例自动生成教程配图并插入文中
        ↓
AI 生成封面（可换可重生成 / 可上传自己的图）
        ↓
四套主题排版 → 手机模拟预览 → 满意再上传
        ↓
正文站外图片自动转微信图床（不裂图）
        ↓
调用微信官方 draft/add → 草稿箱
        ↓
你在后台预览，确认后手动发布（推荐）
```

> 默认只进**草稿箱**，不自动群发/发布，避免误发。

---

## 0. 你需要具备什么

| 条件 | 说明 |
|------|------|
| 已认证公众号 | 订阅号/服务号均可，需有接口权限 |
| AppID + AppSecret | 公众号后台 → 设置与开发 → 基本配置 |
| IP 白名单 | 运行脚本的机器出口 IP 必须加白 |
| 本机 Python 3.10+ | 不依赖 Docker |

---

## 1. 快速开始（推荐）

双击 `START_HERE.bat`：首次自动建环境、开 `.env` 让你填 Key；之后每次双击直接进全流程。

命令行方式：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# 编辑 .env，四组配置独立：LLM_*（写作）/ SEARCH_*（搜索）/ IMAGE_*（生图）/ WECHAT_*（草稿箱）
python scripts/pipeline.py
```

---

## 2. 排版主题（正文样式）

| 主题 | 风格 | 适合 |
|------|------|------|
| `default` | 微信绿·清爽干货 | 干货文、教程 |
| `hot` | 暖橙红·情绪爆文 | 情感文、观点文 |
| `elegant` | 静谧蓝·杂志深度 | 深度长文、行业分析 |
| `minimal` | 黑白灰·极简高级 | 极简审美、随笔 |

排版能力：小标题自动序号装饰、引用金句卡片、有序/无序列表、代码块、表格、
图片（含图注）、删除线、分隔符；**外部链接自动转成文末「参考链接」脚注**
（微信会过滤外链 `<a>`，脚注是唯一稳妥做法）；微信域名链接保留可点击。

全流程会在上传前生成 `preview.html`（手机宽度模拟）并自动打开浏览器，
不满意可当场换主题重新预览，满意才上传。

---

## 3. 单独用命令行发布已有 Markdown

```bash
# 先 dry-run 看排版（不调微信接口），--open-preview 自动打开手机模拟预览
python scripts/one_click_publish.py --md samples/demo.md --dry-run --theme elegant --open-preview

# 真推草稿箱（正文站外图片自动转微信图床）
python scripts/one_click_publish.py --md samples/demo.md --cover samples/cover.jpg --author "你的名字" --theme default
```

成功后控制台会打印 `media_id`，去 **公众号后台 → 草稿箱** 查看。

---

## 4. 官方接口要点（踩坑）

1. **新增草稿**：`POST /cgi-bin/draft/add`
2. **封面**：必须是**永久素材** `media_id`（`material/add_material`）
3. **正文 HTML**：支持 HTML（仅内联样式），会去 JS
4. **正文图片**：外链图会被过滤；本项目已自动 `media/uploadimg` 换成微信图床 URL（jpg/png、单张 ≤1MB，超限自动压缩）
5. **中文 JSON**：`ensure_ascii=False` + UTF-8，避免乱码/参数错误
6. **标题**：≤ 64 字（脚本自动截断）
7. **摘要**：≤ 120 字（脚本自动生成干净摘要，无 Markdown 符号）
8. 草稿发布后会从草稿箱移除（正常行为）

---

## 5. 目录结构

```text
wechat-auto-publish-windows/
├── GUI.bat                         # 图形界面入口（推荐双击这个）
├── START_HERE.bat                  # 命令行全流程入口
├── .env.example                    # 配置模板（四组独立）
├── requirements.txt
├── gui/index.html                  # 苹果风格前端单页
├── samples/demo.md                 # 示例文章
├── runs/<时间戳>/                  # 每次流程的产出（文章/封面/配图/HTML/预览）
└── scripts/
    ├── gui_server.py               # GUI 本地服务（Flask，复用下方模块）
    ├── pipeline.py                 # 命令行全流程
    ├── article_writer.py           # 选题整理/反AI腔写作/参考抓取/教程配图
    ├── one_click_publish.py        # 命令行一键发布已有 Markdown
    ├── markdown_to_wechat_html.py  # 排版引擎（四套主题）
    ├── content_images.py           # 正文图片转微信图床（含压缩）
    ├── env_store.py                # GUI 设置页的 .env 读写
    ├── wechat_client.py            # 微信官方 API 客户端
    ├── topic_search.py             # 真实联网搜索热点
    ├── llm_client.py               # 写作大模型客户端
    ├── generate_cover_ai.py        # AI 生图封面
    ├── generate_cover.py           # 本地模板封面（兜底）
    ├── http_util.py                # 短连接 HTTP（防 Windows 10053）
    └── selftest.py                 # 离线冒烟自测
```

---

## 6. 本地验证

```bash
# 离线冒烟自测（排版/摘要/封面/预览，不调任何外部 API）
python scripts/selftest.py

# 排版 dry-run（不调微信）
python scripts/one_click_publish.py --md samples/demo.md --dry-run
```

---

## 7. 常见错误

| 现象 | 原因 | 处理 |
|------|------|------|
| 40164 / 无效 IP | 出口 IP 未加白 | 后台 IP 白名单加入运行机 IP |
| 40001 / 42001 | token 错或过期 | 检查 AppSecret，重取 token |
| 40007 | 封面 media_id 无效 | 必须用永久素材接口 |
| 正文图片空白 | 外链图片 | 已自动转存；若失败看控制台报告 |
| 45003 等 | 编码问题 | JSON `ensure_ascii=False` |

查本机出口 IP：

```bash
curl -s https://api.ipify.org && echo
```

---

## 8. 下一步可选增强

1. 定时批量：选题库 + cron 定时写稿进草稿
2. 自动发布 `/freepublish/submit`（务必二次确认，不建议默认开）
3. 发布前 Webhook 通知（企微/飞书/邮件）
