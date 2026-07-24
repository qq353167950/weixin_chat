<script>
/* ================= 基础工具 ================= */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

function toast(msg, ms = 2600) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), ms);
}
/* toast 可点击复制内容（保存/上传报错时直接复制错误信息） */
$("#toast").addEventListener("click", async () => {
  const text = $("#toast").textContent;
  if (!text) return;
  try { await navigator.clipboard.writeText(text); } catch (_) {}
  $("#toast").textContent = "已复制";
  setTimeout(() => $("#toast").classList.remove("show"), 800);
});

async function api(path, opts = {}) {
  // 所有普通接口 20s 超时：服务假死时不再永久转圈（任务轮询单独长跑）
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs || 20000);
  let res;
  try {
    res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      signal: ctrl.signal,
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    if (e.name === "AbortError") throw new Error("请求超时，本地服务可能未响应");
    throw new Error("无法连接本地服务（程序是否已退出？）");
  } finally {
    clearTimeout(timer);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/* 全局忙碌门控：有后台任务时禁用所有「启动类」按钮，防误触发 409 */
const BUSY_BTNS = ["btn-search", "btn-custom", "btn-write", "btn-rewrite",
                   "btn-illustrate", "btn-cover-ai", "btn-cover-template", "btn-publish",
                   "btn-deai", "btn-save-md", "btn-export-md"];
let BUSY = false;
function setBusy(v) {
  BUSY = v;
  BUSY_BTNS.forEach(id => {
    const el = document.getElementById(id);
    if (el && !el._selfSpinning) el.disabled = v;
  });
}

/* 苹果风确认弹窗（替代原生 confirm） */
function askConfirm(title, text) {
  return new Promise(resolve => {
    $("#confirm-title").textContent = title;
    $("#confirm-text").textContent = text;
    const mask = $("#confirm-mask");
    mask.classList.add("show");
    const done = (ok) => {
      mask.classList.remove("show");
      $("#confirm-yes").onclick = $("#confirm-no").onclick = mask.onclick = null;
      resolve(ok);
    };
    $("#confirm-yes").onclick = () => done(true);
    $("#confirm-no").onclick = () => done(false);
    mask.onclick = (e) => { if (e.target === mask) done(false); };
  });
}

/* 轮询后台任务：日志写入 consoleEl，结束返回 result。cancellable 时日志区显示取消按钮 */
async function watchTask(taskId, consoleEl, btn, { cancellable = true } = {}) {
  consoleEl.classList.add("show");
  consoleEl.innerHTML = "";
  // 右上角「复制日志」：报错时可整段复制去反馈
  const copyBtn = document.createElement("button");
  copyBtn.className = "copy-log-btn";
  copyBtn.textContent = "复制日志";
  copyBtn.onclick = async () => {
    const text = consoleEl.innerText.replace(/^复制日志\s*/, "").trim();
    try { await navigator.clipboard.writeText(text); toast("日志已复制"); }
    catch (_) {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta);
      ta.select(); document.execCommand("copy"); ta.remove();
      toast("日志已复制");
    }
  };
  consoleEl.appendChild(copyBtn);
  let shown = 0;
  let cancelLine = null;
  // 实时进度行（原地刷新，区别于追加式日志）
  const progLine = document.createElement("div");
  progLine.style.color = "#8ab4f8";
  if (cancellable) {
    cancelLine = document.createElement("div");
    cancelLine.className = "cancel-line";
    const cbtn = document.createElement("button");
    cbtn.textContent = "取消任务";
    cbtn.onclick = async () => {
      cbtn.disabled = true;
      cbtn.textContent = "正在取消…（数秒内生效）";
      try { await api(`/api/task/${taskId}/cancel`, { method: "POST" }); } catch (_) {}
    };
    cancelLine.appendChild(cbtn);
  }
  if (btn) {
    btn.disabled = true; btn._selfSpinning = true; btn._old = btn.innerHTML;
    const label = (btn.dataset.label || btn.textContent || "").trim();
    btn.innerHTML = '<span class="spinner"></span><span>' + label + '</span>';
  }
  setBusy(true);
  let misses = 0;   // 轮询连续失败计数：偶发失败容忍，连续失败判定服务失联
  try {
    while (true) {
      let t;
      try {
        t = await api(`/api/task/${taskId}?since=${shown}`, { timeoutMs: 8000 });
        misses = 0;
      } catch (e) {
        if (++misses >= 4) throw new Error("与本地服务失联，请重启程序后到「历史」找回产出");
        await new Promise(r => setTimeout(r, 1200));
        continue;
      }
      // since 增量：log 只含第 shown 行之后的新行
      const logs = t.log || [];
      for (const line of logs) {
        const div = document.createElement("div");
        div.textContent = line;
        if (/成功|完成|干净/.test(line)) div.className = "okline";
        consoleEl.appendChild(div);
        shown++;
      }
      // 进度行/取消行保持在末尾（appendChild 对已有节点是移动）
      if (t.progress) {
        progLine.textContent = t.progress;
        consoleEl.appendChild(progLine);
      } else {
        progLine.remove();
      }
      if (cancelLine && t.status === "running") {
        consoleEl.appendChild(cancelLine);
      } else if (cancelLine) {
        cancelLine.remove();
      }
      consoleEl.scrollTop = consoleEl.scrollHeight;
      if (t.status === "done") { progLine.remove(); return t.result; }
      if (t.status === "cancelled") {
        progLine.remove();
        const div = document.createElement("div");
        div.textContent = "— 任务已取消 —";
        consoleEl.appendChild(div);
        throw new Error("已取消");
      }
      if (t.status === "error") {
        progLine.remove();
        const div = document.createElement("div");
        div.className = "err";
        div.textContent = "错误：" + t.error;
        consoleEl.appendChild(div);
        throw new Error(t.error);
      }
      await new Promise(r => setTimeout(r, 700));
    }
  } finally {
    if (cancelLine) cancelLine.remove();
    progLine.remove();
    if (btn) { btn.disabled = false; btn._selfSpinning = false; btn.innerHTML = btn._old; if (window.refreshIcons) refreshIcons(btn); }
    setBusy(false);
  }
}

/* ================= 页面切换 ================= */
const PAGES = ["topic", "article", "cover", "publish", "settings", "history"];
async function nav(page) {
  // 离开文章页时若有未保存内容，先保存完再切（否则预览拿到旧文、
  // 切回文章页时 GET 竞争可能用旧内容覆盖编辑器导致修改丢失）
  if (MD_DIRTY && page !== "article") await saveArticle({ silent: true });
  PAGES.forEach(p => {
    $(`#page-${p}`).classList.toggle("show", p === page);
    const btn = $(`.nav-step[data-page=${p}]`) || $(`.env-pill[data-page=${p}]`);
    if (btn) btn.classList.toggle("active", p === page);
  });
  if (page === "publish") {
    // 进发布页前若编辑器有未保存标题，先落盘再预览
    if (MD_DIRTY) await saveArticle({ silent: true });
    renderPreview({ forceTitle: $("#pub-title").dataset.edited === "1" });
  }
  if (page === "settings") loadSettings();
  if (page === "article") syncArticlePage();
  if (page === "history") loadRunsPage();
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (window.refreshIcons) refreshIcons();
}
$$("[data-page]").forEach(el => el.addEventListener("click", () => nav(el.dataset.page)));

/* ================= 全局状态 ================= */
let S = { topics: [], topic: null, theme: "default", themes: {}, env: {} };
let MD_DIRTY = false;      // 编辑器有未保存修改

function setDirty(v) {
  MD_DIRTY = v;
  window.MD_DIRTY = v;   // 浏览器模式兜底暴露
  // 桌面客户端：把脏状态推给 Python 侧（关窗确认用，见 gui_app.py Bridge）
  try { window.pywebview?.api?.set_dirty(v); } catch (_) {}
  $("#dirty-dot").style.display = v ? "" : "none";
}

/* 有未保存修改时关闭页面提醒 */
window.addEventListener("beforeunload", (e) => {
  if (MD_DIRTY) { e.preventDefault(); e.returnValue = ""; }
});

async function refreshState() {
  S = await api("/api/state");
  // 顶栏配置状态点：两项必填（写作模型+微信）就绪即绿；生图/搜索为可选
  const ok = S.env.wechat && S.env.llm;
  $("#env-dot").classList.toggle("ok", !!ok);
  // 步骤完成态
  $(".nav-step[data-page=topic]").classList.toggle("done", !!S.topic);
  $(".nav-step[data-page=article]").classList.toggle("done", !!S.has_article);
  $(".nav-step[data-page=cover]").classList.toggle("done", !!S.has_cover);
  $(".nav-step[data-page=publish]").classList.toggle("done", !!S.publish);
  // 封面页
  if (S.has_cover) {
    $("#cover-box").innerHTML = `<img src="/runfile/cover.jpg?t=${Date.now()}">`;
    $("#btn-goto-publish").disabled = false;
    $("#pub-cover-chip").textContent = "已就绪";
    $("#pub-cover-chip").className = "chip ok";
  } else {
    $("#btn-goto-publish").disabled = true;   // 新一篇/历史无封面时重新禁用
    $("#pub-cover-chip").textContent = "未生成";
    $("#pub-cover-chip").className = "chip warn";
  }
  // 发布成功卡
  if (S.publish) {
    $("#success-card").style.display = "";
    $("#publish-card").style.display = "none";
    $("#success-mid").textContent = S.publish.media_id;
  } else {
    $("#success-card").style.display = "none";
    $("#publish-card").style.display = "";
  }
  return S;
}

/* ================= 页1 选题 ================= */
// 历史记录管理（最近 3 条）。localStorage 损坏时静默重置，不炸脚本
function readHistory(key) {
  try {
    const v = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(v) ? v.filter(x => typeof x === "string") : [];
  } catch (_) {
    localStorage.removeItem(key);
    return [];
  }
}
function saveHistory(key, value) {
  if (!value || !value.trim()) return;
  const list = readHistory(key);
  const filtered = list.filter(v => v !== value);  // 去重
  filtered.unshift(value);                         // 插到最前
  localStorage.setItem(key, JSON.stringify(filtered.slice(0, 3)));  // 只保留 3 条
}
function loadHistory(key, datalistId) {
  const dl = $(datalistId);
  dl.innerHTML = "";
  readHistory(key).forEach(v => {
    const opt = document.createElement("option");
    opt.value = v;
    dl.appendChild(opt);
  });
}

// 页面加载时填充历史记录
loadHistory("history-domain", "#history-domain");
loadHistory("history-extra", "#history-extra");

$("#btn-search").addEventListener("click", async () => {
  try {
    const domain = $("#in-domain").value;
    const extra = $("#in-extra").value;

    // 保存到历史记录
    saveHistory("history-domain", domain);
    saveHistory("history-extra", extra);
    loadHistory("history-domain", "#history-domain");
    loadHistory("history-extra", "#history-extra");

    const { task } = await api("/api/topics/search", { method: "POST", body: {
      domain, extra,
      want_n: parseInt($("#in-want-n").value, 10) || 5,
    }});
    const result = await watchTask(task, $("#log-topic"), $("#btn-search"));
    renderTopics(result.topics);
    toast("选题整理完成，请挑一个");
  } catch (e) { toast("失败：" + e.message, 4000); }
});

function renderTopics(topics) {
  S.topics = topics;
  $("#topics-card").style.display = "";
  const list = $("#topics-list");
  list.innerHTML = "";
  topics.forEach((t, i) => {
    const div = document.createElement("div");
    div.className = "topic-card";
    // 手工构建 DOM 避免 innerHTML 转义问题（选题标题可能含 <>&" 等字符）
    const title = document.createElement("div");
    title.className = "t-title";
    const titleSpan = document.createElement("span");
    titleSpan.textContent = t.title;  // textContent 自动转义
    title.appendChild(titleSpan);
    if (t.score !== "" && t.score != null) {
      const badge = document.createElement("span");
      badge.className = "score-badge";
      badge.textContent = `潜力 ${t.score}`;
      title.appendChild(badge);
    }
    const meta = document.createElement("div");
    meta.className = "t-meta";
    // 转义所有字段（虽然是 LLM 返回，但可能含特殊字符）
    const esc = s => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    meta.innerHTML = `${esc(t.type)} · ${esc(t.audience || "大众读者")}<br>角度：${esc(t.angle || "—")}<br>理由：${esc(t.why || "—")}`;
    div.appendChild(title);
    div.appendChild(meta);
    div.addEventListener("click", async () => {
      try {
        const rs = await api("/api/topics/select", { method: "POST", body: t });
        $$(".topic-card").forEach(c => c.classList.remove("selected"));
        div.classList.add("selected");
        await refreshState();
        if (rs.new_run) {
          resetUiForRun();
          // 切换选题场景：候选列表和下一步按钮要保留
          $("#topics-card").style.display = "";
          toast("已切换新选题，旧文章已存入历史");
        }
        $("#btn-goto-article").disabled = false;
      } catch (e) { toast("选定失败：" + e.message, 4000); }
    });
    list.appendChild(div);
  });
  $("#topics-card").scrollIntoView({ behavior: "smooth" });
}

$("#btn-custom").addEventListener("click", async () => {
  const title = $("#in-custom-title").value.trim();
  if (!title) return toast("请先填主题");
  try {
    const rs = await api("/api/topics/select", { method: "POST", body: {
      title, angle: $("#in-custom-angle").value, type: $("#in-custom-type").value,
    }});
    await refreshState();
    if (rs.new_run) { resetUiForRun(); toast("已切换新选题，旧文章已存入历史"); }
    else toast("已选定主题");
    nav("article");
  } catch (e) { toast("选定失败：" + e.message, 4000); }
});
$("#btn-goto-article").addEventListener("click", () => nav("article"));

/* ================= 页2 文章 ================= */
/* 可读字数：与后端 article_text_char_count 对齐（排除标题行、代码、图片标记、Markdown 符号与空白） */
function articleTextCharCount(md) {
  if (!md) return 0;
  const lines = String(md).replace(/\r\n/g, "\n").split("\n");
  const body = [];
  let seenTitle = false;
  for (const line of lines) {
    if (!seenTitle && line.startsWith("# ")) { seenTitle = true; continue; }
    body.push(line);
  }
  let text = body.join("\n");
  text = text.replace(/```[\s\S]*?```/g, " ");
  text = text.replace(/!\[[^\]]*\]\([^)]*\)/g, " ");
  text = text.replace(/[#>*`~_|]/g, "");
  text = text.replace(/^\s*(?:[-+] |\d+[.)] )/gm, "");
  return text.replace(/\s+/g, "").length;
}
function setArtChars(md, preferred) {
  const n = (preferred != null && preferred !== "") ? Number(preferred) : articleTextCharCount(md || "");
  $("#art-chars").textContent = `${Number.isFinite(n) ? n : 0} 字`;
}
function syncArticlePage() {
  if (S.topic) {
    $("#art-topic-title").textContent = S.topic.title;
    $("#art-topic-meta").textContent =
      `${S.topic.type || ""} · ${S.topic.angle || "无预设角度"}`;
  }
  if (S.has_article) loadArticle();
}

async function loadArticle() {
  const r = await api("/api/article");
  const md = r.md || "";
  if (!md) return;
  $("#editor-card").style.display = "";
  $("#md-editor").value = md;
  setArtChars(md, r.chars);
  $("#btn-rewrite").style.display = "";
  $("#btn-illustrate").style.display = "";
  $("#btn-check-ai").style.display = "";
  setDirty(false);
  showAiChip(S.ai_hits || []);
  renderThemeSegEditor();
  refreshLivePreview();
  // 加载成稿后三处标题对齐
  const t = extractMdTitle(md);
  if (t) applyTitleEverywhere(t, "load");
  await maybeRestoreDraft();   // 异常退出留下的本地草稿比服务端新时提示恢复
}

function showAiChip(hits) {
  const chip = $("#chip-ai");
  const panel = $("#ai-hits-panel");
  const list = $("#ai-hits-list");
  chip.style.display = "";
  hits = Array.isArray(hits) ? hits : [];
  if (hits.length) {
    chip.className = "chip warn";
    chip.textContent = `AI腔 ${hits.length} 处：${hits.slice(0, 6).join("、")}`;
    chip.title = hits.join("、");
    $("#btn-deai").style.display = "";      // 有命中才亮出一键去味
    // 列出全部命中词，点击定位到编辑器中的下一处
    if (panel && list) {
      panel.classList.add("show");
      list.innerHTML = "";
      const md = ($("#md-editor") && $("#md-editor").value) || "";
      hits.forEach((phrase) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "ai-hit-btn";
        const count = md.split(phrase).length - 1;
        btn.innerHTML = "";
        const label = document.createElement("span");
        label.textContent = phrase;
        btn.appendChild(label);
        if (count > 0) {
          const c = document.createElement("span");
          c.className = "cnt";
          c.textContent = "×" + count;
          btn.appendChild(c);
        }
        btn.title = "点击定位到文中「" + phrase + "」";
        btn.addEventListener("click", () => jumpToAiPhrase(phrase));
        list.appendChild(btn);
      });
    }
  } else {
    chip.className = "chip ok";
    chip.textContent = "AI腔检测通过";
    chip.title = "";
    $("#btn-deai").style.display = "none";
    if (panel && list) {
      panel.classList.remove("show");
      list.innerHTML = "";
    }
  }
}

/* 在 Markdown 编辑器中定位并选中 AI 腔词语（从上次位置继续找下一处） */
const _aiSeekPos = {};
function jumpToAiPhrase(phrase) {
  const ed = $("#md-editor");
  if (!ed) return;
  const text = ed.value || "";
  if (!phrase || !text.includes(phrase)) {
    toast("正文里暂时找不到「" + phrase + "」");
    return;
  }
  // 确保编辑器可见
  const card = $("#editor-card");
  if (card && card.style.display === "none") card.style.display = "";
  let from = _aiSeekPos[phrase] || 0;
  let idx = text.indexOf(phrase, from);
  if (idx < 0) {
    from = 0;
    idx = text.indexOf(phrase, 0);
  }
  if (idx < 0) return;
  _aiSeekPos[phrase] = idx + phrase.length;
  ed.focus();
  ed.setSelectionRange(idx, idx + phrase.length);
  // 滚到选区附近（textarea 近似）
  try {
    const before = text.slice(0, idx);
    const lines = before.split("\n").length;
    const lineHeight = 22;
    ed.scrollTop = Math.max(0, (lines - 4) * lineHeight);
  } catch (_) {}
  ed.classList.remove("ai-flash");
  void ed.offsetWidth;
  ed.classList.add("ai-flash");
  setTimeout(() => ed.classList.remove("ai-flash"), 900);
  toast("已定位：「" + phrase + "」· 再点可跳下一处", 2200);
}

/* 手动检测 AI 味（对编辑器当前内容） */
$("#btn-check-ai").addEventListener("click", async () => {
  const md = $("#md-editor").value;
  if (!md.trim()) { toast("还没有文章内容"); return; }
  try {
    const r = await api("/api/article/check_ai", { method: "POST", body: { md }});
    showAiChip(r.ai_hits || []);
    if (r.ai_hits && r.ai_hits.length) {
      toast(`检测到 ${r.ai_hits.length} 处 AI 腔：${r.ai_hits.join("、")}`, 5000);
    } else {
      toast("检测通过，没有 AI 腔");
    }
  } catch (e) { toast("检测失败：" + e.message, 3500); }
});

/* 一键去 AI 味：先保存再让模型润色 */
$("#btn-deai").addEventListener("click", async () => {
  if (MD_DIRTY) {
    const ok = await saveArticle({ silent: true });
    if (!ok) return;
  }
  try {
    const { task } = await api("/api/article/deai", { method: "POST", body: {} });
    const r = await watchTask(task, $("#log-article"), $("#btn-deai"));
    await loadArticle();
    showAiChip(r.ai_hits || []);
    toast(r.changed ? "去味完成" : "本来就很干净");
  } catch (e) { toast("失败：" + e.message, 4000); }
});

async function writeArticle(extraHint) {
  if (!S.topic) { toast("请先选定选题"); return nav("topic"); }
  let extra = $("#in-user-extra").value.trim();
  if (extraHint) extra = (extra ? extra + "；" : "") + extraHint;
  try {
    const { task } = await api("/api/article/write", { method: "POST", body: { mode: "llm", user_extra: extra }});
    const r = await watchTask(task, $("#log-article"), $("#btn-write"));
    await refreshState();
    await loadArticle();
    showAiChip(r.ai_hits || []);
    toast("文章已生成，可直接编辑");
  } catch (e) {
    const msg = e.message || String(e);
    // 字数/结构校验失败：给足时间读提示，并可一键跳转设置改阈值
    if (/可读字数|基础校验|成稿最/.test(msg)) {
      toast(msg, 10000);
      const go = await askConfirm(
        "成稿未通过字数校验",
        "需要把「设置 → 文章增强」里的成稿最少/最多可读字数改成覆盖本次实际字数的区间，保存后再生成。\n\n是否现在打开设置？"
      );
      if (go) {
        await nav("settings");
        setTimeout(() => {
          $$("#settings-body .set-group").forEach(g => {
            const t = g.querySelector("h4")?.textContent || "";
            if (t.includes("文章增强")) {
              g.classList.add("attention");
              g.scrollIntoView({ behavior: "smooth", block: "center" });
            }
          });
        }, 350);
      }
    } else {
      toast("失败：" + msg, 4000);
    }
  }
}
$("#btn-write").addEventListener("click", () => writeArticle());
$("#btn-rewrite").addEventListener("click", () => writeArticle("请换个开头和案例重写，更口语"));

/* 保存文章：silent 时不弹提示（用于切页/发布前自动保存） */
async function saveArticle({ silent = false } = {}) {
  const md = $("#md-editor").value;
  if (!md.trim()) return false;
  try {
    const r = await api("/api/article", { method: "POST", body: { md }});
    setArtChars(md, r.chars);
    showAiChip(r.ai_hits || []);
    setDirty(false);
    localStorage.removeItem("draft-backup");   // 已落盘，本地兜底可清
    await refreshState();
    // 保存后把 # 标题同步到发布页（避免只改编辑器、发布页仍是旧标题）
    const t = extractMdTitle(md);
    if (t) {
      setPubTitleUI(t, { markEdited: false });
      setArticleHeaderTitle(t);
    }
    if (!silent) toast("已保存");
    return true;
  } catch (e) {
    toast("保存失败：" + e.message, 4000);
    return false;
  }
}
$("#btn-save-md").addEventListener("click", () => saveArticle());

/* 编辑即标记未保存 + 防抖刷新右侧实时预览 */
let _liveTimer = null;
let _liveSeq = 0;          // 响应乱序保护：只采用最新一次请求的结果
let _composing = false;    // 输入法组合中：不刷新预览（避免半截拼音混入渲染）
const LIVE_EMPTY = '<p class="empty-hint">开始输入后这里实时显示最终排版效果</p>';
async function refreshLivePreview() {
  const md = $("#md-editor").value;
  if (!md.trim()) { $("#live-preview").innerHTML = LIVE_EMPTY; return; }
  const seq = ++_liveSeq;
  try {
    const r = await api("/api/render_text", { method: "POST", body: { md, theme: S.theme }, timeoutMs: 15000 });
    if (seq !== _liveSeq) return;   // 已有更新的请求发出，丢弃旧响应
    $("#live-preview").innerHTML = r.html;
  } catch (_) { /* 渲染失败保持上一帧，不打扰输入 */ }
}
function scheduleLivePreview() {
  clearTimeout(_liveTimer);
  _liveTimer = setTimeout(refreshLivePreview, 500);
}
$("#md-editor").addEventListener("compositionstart", () => { _composing = true; });
$("#md-editor").addEventListener("compositionend", () => {
  _composing = false;
  scheduleLivePreview();   // 组合完成后补一次刷新
});
/* 本地草稿兜底：编辑内容防抖写 localStorage，崩溃/断电不丢稿 */
let _draftTimer = null;
function saveDraftLocal() {
  clearTimeout(_draftTimer);
  _draftTimer = setTimeout(() => {
    try {
      localStorage.setItem("draft-backup", JSON.stringify({
        run: S.run || "", md: $("#md-editor").value, at: Date.now(),
      }));
    } catch (_) {}   // 配额满等异常静默
  }, 2000);
}
async function maybeRestoreDraft() {
  // 服务端文章比本地备份旧（异常退出没保存成功）时提示恢复
  try {
    const raw = localStorage.getItem("draft-backup");
    if (!raw) return;
    const d = JSON.parse(raw);
    if (!d.md || !d.md.trim() || d.run !== (S.run || "")) return;
    const server = $("#md-editor").value;
    if (server === d.md) { localStorage.removeItem("draft-backup"); return; }
    if (d.md.length > server.length) {
      const ok = await askConfirm("发现未保存的草稿",
        `本地有一份更完整的编辑备份（可读约 ${articleTextCharCount(d.md)} 字，当前 ${articleTextCharCount(server)} 字），恢复它吗？`);
      if (ok) {
        $("#md-editor").value = d.md;
        setDirty(true);
        setArtChars(d.md);
        refreshLivePreview();
        toast("草稿已恢复，记得保存");
      } else {
        localStorage.removeItem("draft-backup");
      }
    }
  } catch (_) { localStorage.removeItem("draft-backup"); }
}

let _editorTitleTimer = null;
$("#md-editor").addEventListener("input", () => {
  if (!MD_DIRTY) setDirty(true);   // 只在状态翻转时走 pywebview 桥，减少每键开销
  const md = $("#md-editor").value;
  setArtChars(md);
  if (!_composing) scheduleLivePreview();
  saveDraftLocal();
  // 编辑器里改了 # 一级标题 → 同步到发布页 / 文章页标题 / 预览
  clearTimeout(_editorTitleTimer);
  _editorTitleTimer = setTimeout(() => {
    if (_titleSyncing) return;
    const t = extractMdTitle(md);
    if (t) applyTitleEverywhere(t, "editor");
  }, 450);
});

/* 复制排版后内容：富文本进剪贴板，可直接粘贴进公众号编辑器 */
$("#btn-copy-rich").addEventListener("click", async () => {
  const node = $("#live-preview");
  if (!node.innerText.trim()) { toast("先写点内容再复制"); return; }
  try {
    const blob = new Blob([node.innerHTML], { type: "text/html" });
    const plain = new Blob([node.innerText], { type: "text/plain" });
    await navigator.clipboard.write([
      new ClipboardItem({ "text/html": blob, "text/plain": plain }),
    ]);
    toast("已复制排版内容，可直接粘贴到公众号编辑器");
  } catch (_) {
    // 兜底：选中节点走 execCommand
    const range = document.createRange();
    range.selectNodeContents(node);
    const sel = getSelection();
    sel.removeAllRanges(); sel.addRange(range);
    document.execCommand("copy");
    sel.removeAllRanges();
    toast("已复制排版内容");
  }
});

/* 编辑页内的主题切换（与发布页共用 S.theme） */
function renderThemeSegEditor() {
  const seg = $("#theme-seg-editor");
  seg.innerHTML = "";
  Object.entries(S.themes).forEach(([key, label]) => {
    const b = document.createElement("button");
    b.textContent = label.split("·")[0].trim();
    b.title = label;
    b.classList.toggle("on", key === S.theme);
    b.addEventListener("click", () => { S.theme = key; renderThemeSegEditor(); renderThemeSeg(); refreshLivePreview(); });
    seg.appendChild(b);
  });
}

/* ============ 编辑器格式工具栏 ============ */
/* 对 textarea 选区包裹/插入 Markdown 或内联样式 */
function edSurround(before, after, placeholder) {
  const ed = $("#md-editor");
  const s = ed.selectionStart, e = ed.selectionEnd;
  const sel = ed.value.slice(s, e) || placeholder || "文字";
  ed.setRangeText(before + sel + after, s, e, "select");
  ed.focus();
  ed.dispatchEvent(new Event("input"));
}
/* 行首前缀（标题/引用/列表）：作用于选区覆盖的每一行 */
function edLinePrefix(prefix, ordered) {
  const ed = $("#md-editor");
  const v = ed.value;
  let s = ed.selectionStart, e = ed.selectionEnd;
  const ls = v.lastIndexOf("\n", s - 1) + 1;                 // 选区首行行首
  let le = v.indexOf("\n", e); if (le < 0) le = v.length;    // 选区末行行尾
  const block = v.slice(ls, le);
  const lines = block.split("\n");
  const out = lines.map((ln, i) => {
    const clean = ln.replace(/^(#{1,6}\s+|>\s+|[-*+]\s+|\d+[.]\s+)/, "");
    return (ordered ? `${i + 1}. ` : prefix) + clean;
  }).join("\n");
  ed.setRangeText(out, ls, le, "select");
  ed.focus();
  ed.dispatchEvent(new Event("input"));
}
/* 内联样式 span（字体/字号/颜色，排版引擎会透传安全样式） */
function edSpanStyle(styleFrag) {
  const ed = $("#md-editor");
  const s = ed.selectionStart, e = ed.selectionEnd;
  if (s === e) { toast("请先选中要设置的文字"); return; }
  let sel = ed.value.slice(s, e);
  // 已是本工具生成的 span：合并样式而不是嵌套
  const m = sel.match(/^<span style="([^"]*)">([\s\S]*)<\/span>$/);
  if (m) {
    const prop = styleFrag.split(":")[0];
    const kept = m[1].split(";").filter(x => x.trim() && !x.trim().startsWith(prop)).join(";");
    sel = `<span style="${kept ? kept + ";" : ""}${styleFrag}">${m[2]}</span>`;
  } else {
    sel = `<span style="${styleFrag}">${sel}</span>`;
  }
  ed.setRangeText(sel, s, e, "select");
  ed.focus();
  ed.dispatchEvent(new Event("input"));
}
function edClearFormat() {
  const ed = $("#md-editor");
  const s = ed.selectionStart, e = ed.selectionEnd;
  if (s === e) { toast("请先选中要清除格式的文字"); return; }
  let sel = ed.value.slice(s, e);
  sel = sel.replace(/<span style="[^"]*">([\s\S]*?)<\/span>/g, "$1");
  ed.setRangeText(sel, s, e, "select");
  ed.focus();
  ed.dispatchEvent(new Event("input"));
}

$("#md-toolbar").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-md]");
  if (!btn) return;
  const act = btn.dataset.md;
  if (act === "bold") edSurround("**", "**");
  else if (act === "italic") edSurround("*", "*");
  else if (act === "strike") edSurround("~~", "~~");
  else if (act === "code") edSurround("`", "`", "代码");
  else if (act === "h2") edLinePrefix("## ");
  else if (act === "h3") edLinePrefix("### ");
  else if (act === "quote") edLinePrefix("> ");
  else if (act === "ul") edLinePrefix("- ");
  else if (act === "ol") edLinePrefix("", true);
  else if (act === "clear") edClearFormat();
});

/* 字体下拉：本机常见中文字体（前端探测可用性） */
(function initFontSelect() {
  const CANDIDATES = [
    ["微软雅黑", "Microsoft YaHei"], ["宋体", "SimSun"], ["黑体", "SimHei"],
    ["楷体", "KaiTi"], ["仿宋", "FangSong"], ["苹方", "PingFang SC"],
    ["华文细黑", "STXihei"], ["华文楷体", "STKaiti"], ["幼圆", "YouYuan"],
    ["隶书", "LiSu"], ["等线", "DengXian"], ["Arial", "Arial"],
    ["Georgia", "Georgia"], ["Courier New", "Courier New"],
  ];
  const sel = $("#tb-font");
  const probe = (name) => {
    try { return document.fonts.check(`16px "${name}"`); } catch (_) { return true; }
  };
  CANDIDATES.forEach(([label, family]) => {
    if (!probe(family)) return;
    const opt = document.createElement("option");
    opt.value = family;
    opt.textContent = label;
    opt.style.fontFamily = family;
    sel.appendChild(opt);
  });
})();
$("#tb-font").addEventListener("change", (e) => {
  if (e.target.value) edSpanStyle(`font-family:${e.target.value}`);
  e.target.value = "";
});
$("#tb-size").addEventListener("change", (e) => {
  if (e.target.value) edSpanStyle(`font-size:${e.target.value}`);
  e.target.value = "";
});
$("#tb-color").addEventListener("input", (e) => {
  edSpanStyle(`color:${e.target.value}`);
});

/* Ctrl+B/I 快捷键与 Tab 缩进（编辑器内） */
$("#md-editor").addEventListener("keydown", (e) => {
  if (e.key === "Tab") {
    // Tab 插入两空格而不是跳焦点（Markdown 缩进）
    e.preventDefault();
    const ed = e.target;
    ed.setRangeText("  ", ed.selectionStart, ed.selectionEnd, "end");
    ed.dispatchEvent(new Event("input"));
    return;
  }
  if (!(e.ctrlKey || e.metaKey)) return;
  const k = e.key.toLowerCase();
  if (k === "b") { e.preventDefault(); edSurround("**", "**"); }
  else if (k === "i") { e.preventDefault(); edSurround("*", "*"); }
});

/* Ctrl+S / Cmd+S 保存文章（拦截浏览器保存网页）；后台任务写文件期间禁止保存防覆盖 */
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    if ($("#page-article").classList.contains("show") && $("#editor-card").style.display !== "none") {
      e.preventDefault();
      if (BUSY) { toast("后台任务进行中，稍后再保存（防止互相覆盖）"); return; }
      saveArticle();
    }
  }
});

/* 导出 .md / 打开产出目录 */
$("#btn-export-md").addEventListener("click", async () => {
  if (MD_DIRTY) await saveArticle({ silent: true });
  const a = document.createElement("a");
  a.href = "/api/article/export";
  a.download = "";
  a.click();
});
$("#btn-open-folder").addEventListener("click", async () => {
  try { await api("/api/open_folder", { method: "POST", body: {} }); }
  catch (e) { toast(e.message, 3000); }
});

/* 下一步：封面（先保存再走） */
$("#btn-goto-cover").addEventListener("click", async () => {
  if (MD_DIRTY) await saveArticle({ silent: true });
  nav("cover");
});

$("#btn-illustrate").addEventListener("click", async () => {
  if (MD_DIRTY) {
    const ok = await saveArticle({ silent: true });
    if (!ok) return;
  }
  try {
    const { task } = await api("/api/article/illustrate", { method: "POST", body: {} });
    await watchTask(task, $("#log-article"), $("#btn-illustrate"));
    await loadArticle();
    toast("配图完成，已插入文章");
  } catch (e) { toast("失败：" + e.message, 4000); }
});

/* ================= 页3 封面 ================= */
$("#btn-cover-ai").addEventListener("click", async () => {
  try {
    const { task } = await api("/api/cover/generate", { method: "POST", body: {
      style: $("#in-cover-style").value,
    }});
    await watchTask(task, $("#log-cover"), $("#btn-cover-ai"));
    await refreshState();
    toast("封面已生成");
  } catch (e) { toast("失败：" + e.message, 4000); }
});

$("#btn-cover-template").addEventListener("click", async () => {
  try {
    const { task } = await api("/api/cover/generate", { method: "POST", body: { template: true }});
    await watchTask(task, $("#log-cover"), $("#btn-cover-template"));
    await refreshState();
    toast("模板封面已生成");
  } catch (e) { toast("失败：" + e.message, 4000); }
});

$("#in-cover-file").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch("/api/cover/upload", { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    if (res.ok) { await refreshState(); toast("封面已上传"); }
    else toast("上传失败：" + (data.error || `HTTP ${res.status}`), 4000);
  } catch (e) {
    toast("上传失败：" + e.message, 4000);
  }
  ev.target.value = "";
});

/* ================= 页4 预览发布 ================= */
function renderThemeSeg() {
  const seg = $("#theme-seg");
  seg.innerHTML = "";
  Object.entries(S.themes).forEach(([key, label]) => {
    const b = document.createElement("button");
    b.textContent = label.split("·")[0].trim();
    b.title = label;
    b.classList.toggle("on", key === S.theme);
    b.addEventListener("click", () => { S.theme = key; renderThemeSeg(); renderPreview(); });
    seg.appendChild(b);
  });
}

/* 标题/摘要/作者：编辑保护 + 实时字数（23/64 式，接近上限变色） */
const PUB_LIMITS = { "pub-title": 64, "pub-digest": 120, "pub-author": 8 };
function updateCharCount(id) {
  const el = $(`#${id}`);
  const cnt = $(`#cnt-${id.replace("pub-", "")}`);
  const max = PUB_LIMITS[id];
  const n = el.value.length;
  cnt.textContent = `${n}/${max}`;
  cnt.className = "char-count" + (n >= max ? " full" : n >= max * 0.85 ? " near" : "");
}

/* —— 标题三处同步：编辑器 # 标题 · 发布页输入框 · 手机预览 —— */
function extractMdTitle(md) {
  const m = String(md || "").match(/^#\s+(.+)$/m);
  return m ? m[1].trim().slice(0, 64) : "";
}
function replaceMdTitle(md, title) {
  const t = String(title || "").trim().slice(0, 64);
  if (!t) return md;
  const text = String(md || "");
  if (/^#\s+/m.test(text)) return text.replace(/^#\s+.+$/m, "# " + t);
  return "# " + t + "\n\n" + text.replace(/^\s+/, "");
}
function setPubTitleUI(title, { markEdited = false } = {}) {
  const el = $("#pub-title");
  if (!el) return;
  el.value = String(title || "").slice(0, 64);
  if (markEdited) el.dataset.edited = "1";
  else delete el.dataset.edited;
  updateCharCount("pub-title");
}
function setArticleHeaderTitle(title) {
  const t = String(title || "").trim();
  if (!t) return;
  if (S.topic) S.topic.title = t;
  const el = $("#art-topic-title");
  if (el) el.textContent = t;
}

/**
 * source: "pub" | "editor" | "load"
 * - pub：发布页改标题 → 写回编辑器/#行 + 落盘 + 刷预览
 * - editor：编辑器改 # 标题 → 同步发布页（未手动锁时）+ 刷预览
 * - load：从文件加载 → 三处对齐，不标 edited
 */
let _titleSyncing = false;
let _pubTitleTimer = null;
let _pubPreviewTimer = null;
async function applyTitleEverywhere(title, source) {
  const t = String(title || "").trim().slice(0, 64);
  if (!t || _titleSyncing) return;
  _titleSyncing = true;
  try {
    if (source === "pub" || source === "load") {
      setPubTitleUI(t, { markEdited: source === "pub" });
    } else if (source === "editor") {
      // 编辑器改了标题：覆盖发布页显示（发布页若刚手改过也会被编辑器覆盖，保证同源）
      setPubTitleUI(t, { markEdited: false });
    }
    setArticleHeaderTitle(t);

    if (source === "pub") {
      const ed = $("#md-editor");
      let md = ed ? ed.value : "";
      if (!md.trim() && S.has_article) {
        try {
          const r = await api("/api/article");
          md = r.md || "";
          if (ed && md) ed.value = md;
        } catch (_) {}
      }
      if (md.trim()) {
        const next = replaceMdTitle(md, t);
        if (ed && next !== ed.value) {
          ed.value = next;
          setArtChars(next);
          if (!MD_DIRTY) setDirty(true);
          scheduleLivePreview();
        }
        // 落盘，保证预览/再次打开/发布都读到新标题
        try {
          await api("/api/article", { method: "POST", body: { md: next || md } });
          setDirty(false);
          localStorage.removeItem("draft-backup");
          await refreshState();
        } catch (e) {
          toast("标题同步保存失败：" + e.message, 3500);
        }
      }
    }

    if ($("#page-publish") && $("#page-publish").classList.contains("show")) {
      schedulePublishPreview();
    }
  } finally {
    _titleSyncing = false;
  }
}
function schedulePublishPreview() {
  clearTimeout(_pubPreviewTimer);
  _pubPreviewTimer = setTimeout(() => renderPreview({ forceTitle: true }), 280);
}

Object.keys(PUB_LIMITS).forEach(id => {
  $(`#${id}`).addEventListener("input", (e) => {
    e.target.dataset.edited = "1";
    updateCharCount(id);
    if (id === "pub-title") {
      clearTimeout(_pubTitleTimer);
      _pubTitleTimer = setTimeout(() => {
        applyTitleEverywhere(e.target.value, "pub");
      }, 400);
    } else if (id === "pub-digest" || id === "pub-author") {
      // 摘要/作者仅影响发布预览展示（作者叠在预览页）
      if (id === "pub-author") schedulePublishPreview();
    }
  });
});

async function renderPreview({ forceTitle = false } = {}) {
  if (!S.has_article) { $("#preview-frame").srcdoc = "<p style='padding:40px;color:#999;font-family:sans-serif'>还没有文章</p>"; return; }
  try {
    const pubTitle = ($("#pub-title").value || "").trim();
    const useOverride = forceTitle || $("#pub-title").dataset.edited === "1";
    const body = { theme: S.theme };
    if (useOverride && pubTitle) body.title = pubTitle;
    const r = await api("/api/render", { method: "POST", body });
    $("#preview-frame").srcdoc = r.preview;
    // 标题：若用户正在编辑发布页标题则保留输入，否则用文件/覆盖结果回填
    if ($("#pub-title").dataset.edited !== "1") {
      setPubTitleUI(r.title || "", { markEdited: false });
      setArticleHeaderTitle(r.title || "");
    } else if (forceTitle && pubTitle) {
      // 已带 override 渲染，输入框保持用户值
      updateCharCount("pub-title");
    }
    const fill = (id, val) => {
      const el = $(`#${id}`);
      if (el.dataset.edited !== "1") el.value = val;
      updateCharCount(id);
    };
    fill("pub-digest", r.digest);
    fill("pub-author", r.author || "");
  } catch (e) { toast(e.message, 3000); }
}

$("#btn-publish").addEventListener("click", async () => {
  const ok = await askConfirm("上传到草稿箱？", "只进草稿箱，不会自动群发。\n你可以在公众号后台预览后再手动发布。");
  if (!ok) return;
  try {
    const { task } = await api("/api/publish", { method: "POST", body: {
      theme: S.theme,
      title: $("#pub-title").value.trim(),
      digest: $("#pub-digest").value.trim(),
      author: $("#pub-author").value.trim(),
    }});
    // 上传不提供取消：中途截断可能产生半成品素材
    await watchTask(task, $("#log-publish"), $("#btn-publish"), { cancellable: false });
    await refreshState();
    toast("已进入草稿箱", 4000);
  } catch (e) { toast("失败：" + e.message, 5000); }
});

/* 发布成功：一键打开公众号后台（系统默认浏览器） */
$("#btn-open-mp").addEventListener("click", async () => {
  try { await api("/api/open_url", { method: "POST", body: { url: "https://mp.weixin.qq.com/" } }); }
  catch (_) { window.open("https://mp.weixin.qq.com/", "_blank"); }
});

$("#btn-copy-mid").addEventListener("click", async () => {
  const mid = $("#success-mid").textContent;
  try {
    await navigator.clipboard.writeText(mid);
    toast("已复制 media_id");
  } catch (_) {
    // 剪贴板 API 不可用时兜底（如非安全上下文）
    const ta = document.createElement("textarea");
    ta.value = mid;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    toast("已复制 media_id");
  }
});

function resetUiForRun() {
  $("#topics-card").style.display = "none";
  $("#editor-card").style.display = "none";
  $("#md-editor").value = "";
  setDirty(false);
  ["log-topic", "log-article", "log-cover", "log-publish"].forEach(id =>
    $(`#${id}`).classList.remove("show"));
  $("#cover-box").innerHTML = "还没有封面";
  $("#live-preview").innerHTML = LIVE_EMPTY;
  $("#art-topic-title").textContent = "未选定选题";
  $("#art-topic-meta").textContent = "";
  $("#art-chars").textContent = "";
  $("#chip-ai").style.display = "none";
  $("#btn-rewrite").style.display = "none";
  $("#btn-illustrate").style.display = "none";
  $("#btn-check-ai").style.display = "none";
  $("#btn-deai").style.display = "none";
  $("#btn-goto-article").disabled = true;
  // 清掉发布页的手动修改标记，让新文章的标题/摘要能重新回填
  ["pub-title", "pub-digest", "pub-author"].forEach(id => {
    const el = $(`#${id}`);
    el.value = "";
    delete el.dataset.edited;
  });
}

$("#btn-new-run").addEventListener("click", async () => {
  await api("/api/new_run", { method: "POST" });
  await refreshState();
  resetUiForRun();
  toast("新一篇开始");
  nav("topic");
});

/* ================= 页6 历史 ================= */
async function loadRunsPage() {
  const box = $("#history-body");
  box.innerHTML = "加载中…";
  try {
    const { runs } = await api("/api/runs");
    if (!runs.length) { box.innerHTML = '<p class="empty-hint">还没有历史产出</p>'; return; }
    box.innerHTML = "";
    runs.forEach(r => {
      const row = document.createElement("div");
      row.className = "run-row";
      const stamp = r.run.replace(
        /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}(?:-\d+)?$/, "$1-$2-$3 $4:$5");
      // 缩略图（路径由后端校验，安全）
      if (r.has_cover) {
        const img = document.createElement("img");
        img.className = "thumb";
        img.src = `/api/runs/cover/${r.run}?t=${Date.now()}`;
        img.alt = "";
        row.appendChild(img);
      } else {
        const ph = document.createElement("div");
        ph.className = "thumb";
        row.appendChild(ph);
      }
      // 标题可能含 <>& 等字符（来自 LLM/用户），必须 textContent
      const info = document.createElement("div");
      info.className = "info";
      const t = document.createElement("div");
      t.className = "t";
      t.textContent = r.title || "（未写文章）";
      const m = document.createElement("div");
      m.className = "m";
      m.textContent = stamp + (r.media_id ? " · 已进草稿箱" : "") + (r.current ? " · 当前" : "");
      info.appendChild(t);
      info.appendChild(m);
      row.appendChild(info);
      const btn = document.createElement("button");
      btn.className = "btn secondary";
      btn.textContent = r.current ? "编辑中" : "打开";
      btn.disabled = !!r.current;
      btn.addEventListener("click", async () => {
        if (MD_DIRTY) {
          const ok = await askConfirm("当前文章有未保存修改", "打开历史记录会丢弃这些修改，继续吗？");
          if (!ok) return;
          setDirty(false);
        }
        try {
          await api("/api/runs/open", { method: "POST", body: { run: r.run }});
          await refreshState();
          resetUiForRun();
          if (S.topics && S.topics.length) renderTopics(S.topics);
          toast("已打开：" + (r.title || r.run));
          nav(S.has_article ? "article" : "topic");
        } catch (e) { toast("打开失败：" + e.message, 4000); }
      });
      row.appendChild(btn);
      // 打开目录（看文章/封面原始文件）
      const openBtn = document.createElement("button");
      openBtn.className = "btn ghost";
      openBtn.textContent = "目录";
      openBtn.title = "在资源管理器打开该产出目录";
      openBtn.addEventListener("click", async () => {
        try { await api("/api/open_folder", { method: "POST", body: { run: r.run } }); }
        catch (e) { toast(e.message, 3000); }
      });
      row.appendChild(openBtn);
      // 删除按钮（当前编辑中的不可删）
      const del = document.createElement("button");
      del.className = "btn ghost";
      del.textContent = "删除";
      del.style.color = "var(--red)";
      del.disabled = !!r.current;
      del.addEventListener("click", async () => {
        const ok = await askConfirm("删除这条历史？",
          `「${r.title || r.run}」的文章、封面、配图将全部删除，不可恢复。`);
        if (!ok) return;
        try {
          await api("/api/runs/delete", { method: "POST", body: { run: r.run }});
          row.remove();
          toast("已删除");
          if (!$("#history-body .run-row")) $("#history-body").innerHTML = '<p class="empty-hint">还没有历史产出</p>';
        } catch (e) { toast("删除失败：" + e.message, 4000); }
      });
      row.appendChild(del);
      box.appendChild(row);
    });
  } catch (e) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = `加载失败：${e.message}`;  // textContent 自动转义
    box.innerHTML = "";
    box.appendChild(p);
  }
}

/* ================= 连接测试 ================= */
function testLog(msg, isErr) {
  const box = $("#log-test");
  box.classList.add("show");
  const div = document.createElement("div");
  div.textContent = msg;
  div.className = isErr ? "err" : "okline";
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}
async function runTest(btn, path, label) {
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "测试中…";
  testLog(`— 开始${label} —`, false);
  try {
    const r = await api(path, { method: "POST", body: {}, timeoutMs: 60000 });
    testLog((r.ok ? "[OK] " : "[FAIL] ") + r.message, !r.ok);
  } catch (e) {
    testLog("[FAIL] " + e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}
$("#btn-test-llm").addEventListener("click", (e) => runTest(e.target, "/api/test/llm", "写作模型测试"));
$("#btn-test-wechat").addEventListener("click", (e) => runTest(e.target, "/api/test/wechat", "公众号连接测试"));
$("#btn-my-ip").addEventListener("click", async (e) => {
  e.target.disabled = true;
  try {
    const r = await api("/api/my_ip", { timeoutMs: 15000 });
    testLog(`本机出口 IP：${r.ip}（点击本行复制，去公众号后台加白名单）`, false);
    const line = $("#log-test").lastChild;
    line.style.cursor = "pointer";
    line.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(r.ip); toast("IP 已复制"); } catch (_) {}
    });
  } catch (err) { testLog("[FAIL] " + err.message, true); }
  finally { e.target.disabled = false; }
});

/* ================= 页5 设置 ================= */
let SETTINGS_SCHEMA = null;
/* 设置项标题：可选 Lucide 圆圈感叹号，悬停显示说明（不占布局高度） */
function fieldCapHtml(label, hint) {
  const safeLabel = String(label || "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  if (!hint) return `<span class="cap">${safeLabel}</span>`;
  const safeTip = String(hint)
    .replace(/&/g, "&amp;").replace(/"/g, "&quot;")
    .replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return `<span class="cap">${safeLabel}`
    + `<span class="field-tip" data-tip="${safeTip}" tabindex="0" role="img" aria-label="${safeTip}">`
    + `<i data-lucide="info"></i></span></span>`;
}

/* tip 浮层：挂 body + fixed，避免被 .card overflow / 顶栏挡住 */
function ensureFieldTipBubble() {
  let el = document.getElementById("field-tip-bubble");
  if (!el) {
    el = document.createElement("div");
    el.id = "field-tip-bubble";
    el.setAttribute("role", "tooltip");
    document.body.appendChild(el);
  }
  return el;
}
function placeFieldTipBubble(anchor) {
  const tip = (anchor.getAttribute("data-tip") || "").trim();
  if (!tip) return;
  const bubble = ensureFieldTipBubble();
  bubble.textContent = tip;
  bubble.classList.add("show");
  // 先显示再量尺寸
  const r = anchor.getBoundingClientRect();
  const bw = bubble.offsetWidth;
  const bh = bubble.offsetHeight;
  const gap = 8;
  const vw = window.innerWidth;
  // 默认朝上：不遮挡下方输入框与表单内容
  let top = r.top - gap - bh;
  let placeAbove = true;
  // 仅当上方不够时才落到下方
  if (top < 8) {
    top = r.bottom + gap;
    placeAbove = false;
  }
  // 水平居中于图标，并夹在视口内
  let left = r.left + r.width / 2 - bw / 2;
  left = Math.max(8, Math.min(left, vw - bw - 8));
  bubble.style.top = Math.round(top) + "px";
  bubble.style.left = Math.round(left) + "px";
  bubble.dataset.place = placeAbove ? "above" : "below";
}
function hideFieldTipBubble() {
  const bubble = document.getElementById("field-tip-bubble");
  if (bubble) bubble.classList.remove("show");
}
function bindFieldTips(root) {
  const scope = root || document;
  scope.querySelectorAll(".field-tip[data-tip]").forEach((el) => {
    if (el._tipBound) return;
    el._tipBound = true;
    el.addEventListener("mouseenter", () => placeFieldTipBubble(el));
    el.addEventListener("mouseleave", hideFieldTipBubble);
    el.addEventListener("focus", () => placeFieldTipBubble(el));
    el.addEventListener("blur", hideFieldTipBubble);
  });
}
document.addEventListener("scroll", hideFieldTipBubble, true);
window.addEventListener("resize", hideFieldTipBubble);
// 初始页面上的 tip（文章页补充要求等）
document.addEventListener("DOMContentLoaded", () => bindFieldTips());
// 脚本在 DOM 末尾执行时 DOM 已就绪
if (document.readyState !== "loading") setTimeout(() => bindFieldTips(), 0);

async function loadSettings() {
  const { schema, values } = await api("/api/settings");
  SETTINGS_SCHEMA = schema;
  // 回填关窗行为偏好（独立于 .env）
  api("/api/close_action").then(r => { $("#in-close-action").value = r.action; }).catch(() => {});
  const box = $("#settings-body");
  box.innerHTML = "";
  schema.forEach(group => {
    const g = document.createElement("div");
    g.className = "set-group";
    g.innerHTML = `<h4>${group.group}</h4><div class="gdesc">${group.desc}</div>`;
    const grid = document.createElement("div");
    grid.className = "set-grid";
    group.fields.forEach(f => {
      const val = values[f.key] ?? "";
      if (f.type === "toggle") {
        const row = document.createElement("div");
        row.className = "toggle-row";
        row.innerHTML = `${fieldCapHtml(f.label, f.hint)}
          <label class="switch"><input type="checkbox" data-key="${f.key}" ${val === "1" ? "checked" : ""}>
          <span class="track"></span></label>`;
        grid.appendChild(row);
      } else if (f.type === "select") {
        const lab = document.createElement("label");
        lab.className = "field";
        lab.innerHTML = `${fieldCapHtml(f.label, f.hint)}
          <select data-key="${f.key}">${f.options.map(o => {
            // 选项可为 "value" 或 ["value", "中文标签"]
            const [v, lbl] = Array.isArray(o) ? o : [o, o];
            return `<option value="${v}" ${v === val ? "selected" : ""}>${lbl}</option>`;
          }).join("")}</select>`;
        grid.appendChild(lab);
      } else {
        const lab = document.createElement("label");
        lab.className = "field";
        // value 需完整转义：.env 值可能含 & < > "（如 URL 带查询参数）
        const safeVal = String(val).replace(/&/g, "&amp;").replace(/</g, "&lt;")
          .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
        if (f.secret) {
          // 密钥输入框：带眼睛按钮，点击切换明文/圆点
          lab.innerHTML = `${fieldCapHtml(f.label, f.hint)}
            <span class="secret-wrap">
              <input type="password" data-key="${f.key}" value="${safeVal}"
                     placeholder="••••••" autocomplete="off">
              <button type="button" class="eye-btn" title="显示/隐藏"><i data-lucide="eye"></i></button>
            </span>`;
          const inp = lab.querySelector("input");
          lab.querySelector(".eye-btn").addEventListener("click", (ev) => {
            const show = inp.type === "password";
            inp.type = show ? "text" : "password";
            ev.currentTarget.style.opacity = show ? "1" : "";
            const ic = ev.currentTarget.querySelector("[data-lucide]");
            if (ic) {
              ic.setAttribute("data-lucide", show ? "eye-off" : "eye");
              if (window.lucide) lucide.createIcons({ nodes: [ev.currentTarget] });
            }
          });
        } else {
          lab.innerHTML = `${fieldCapHtml(f.label, f.hint)}
            <input type="text" data-key="${f.key}" value="${safeVal}"
                   placeholder="" autocomplete="off">`;
        }
        grid.appendChild(lab);
      }
    });
    g.appendChild(grid);
    box.appendChild(g);
  });
  if (window.lucide) lucide.createIcons();
  bindFieldTips(box);
}

$("#btn-save-settings").addEventListener("click", async () => {
  const values = {};
  $$("#settings-body [data-key]").forEach(el => {
    values[el.dataset.key] = el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value;
  });
  try {
    await api("/api/settings", { method: "POST", body: { values }});
    // 关窗行为偏好单独存（ui_state.json，非 .env）
    await api("/api/close_action", { method: "POST", body: { action: $("#in-close-action").value }});
    await refreshState();
    toast("设置已保存并生效");
  } catch (e) { toast("失败：" + e.message, 4000); }
});

/* 浏览器回退模式：显示「退出」按钮（桌面窗口关窗即退，无需此入口）
   pywebview 注入时机可能晚于脚本执行，延迟判定 */
setTimeout(() => {
  if (!window.pywebview) $("#btn-quit-app").style.display = "";
}, 1500);
$("#btn-quit-app").addEventListener("click", async () => {
  const ok = await askConfirm("退出程序？", "将停止本地服务，未保存的编辑会丢失。");
  if (!ok) return;
  try {
    await api("/api/quit", { method: "POST", body: { force: BUSY } });
  } catch (e) {
    if (!/失联|超时/.test(e.message)) { toast(e.message, 3500); return; }
  }
  document.body.innerHTML = '<div style="display:flex;height:100vh;align-items:center;justify-content:center;color:#6e6e73;font-size:15px;background:#f5f5f7;font-family:inherit">程序已退出，可以关闭此页面了</div>';
});

/* ================= 首次启动引导 ================= */
function dismissWelcome() {
  const mask = $("#welcome-mask");
  mask.classList.add("leaving");
  setTimeout(() => { mask.classList.remove("show", "leaving"); }, 450);
  localStorage.setItem("welcomed", "1");
}

function maybeShowWelcome() {
  // 触发条件：两项必填（写作模型 / 微信）都未配置，且本机没看过引导
  const unconfigured = !S.env.llm && !S.env.wechat;
  if (!unconfigured) return;
  if (localStorage.getItem("welcomed") === "1") return;
  $("#welcome-mask").classList.add("show");
  $("#btn-welcome-go").onclick = () => {
    dismissWelcome();
    nav("settings");
    // 设置页渲染完成后高亮两组必填（写作大模型 + 微信公众号）
    setTimeout(() => {
      $$("#settings-body .set-group").forEach(g => {
        const t = g.querySelector("h4")?.textContent || "";
        if (t.includes("写作大模型") || t.includes("微信公众号")) {
          g.classList.add("attention");
        }
      });
      const first = $("#settings-body .set-group.attention");
      if (first) first.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 350);
  };
  $("#btn-welcome-skip").onclick = dismissWelcome;
}

/* ================= 启动 ================= */
/* 刷新页面后恢复运行中任务的进度轮询（任务名 → 对应日志面板与完成动作） */
const TASK_RESUME = {
  "搜索选题":  { log: "log-topic",   page: "topic",
                 done: async (r) => { if (r && r.topics) renderTopics(r.topics); } },
  "生成文章":  { log: "log-article", page: "article",
                 done: async () => { await loadArticle(); } },
  "生成配图":  { log: "log-article", page: "article",
                 done: async () => { await loadArticle(); } },
  "去除AI味":  { log: "log-article", page: "article",
                 done: async () => { await loadArticle(); } },
  "生成封面":  { log: "log-cover",   page: "cover",  done: async () => {} },
  "上传草稿箱": { log: "log-publish", page: "publish", done: async () => {} },
};

async function resumeRunningTasks() {
  for (const t of S.tasks || []) {
    const conf = TASK_RESUME[t.name];
    if (!conf) continue;
    nav(conf.page);
    toast(`「${t.name}」仍在后台运行，已恢复进度`);
    watchTask(t.id, $(`#${conf.log}`), null)
      .then(async (r) => { await refreshState(); await conf.done(r); toast(`「${t.name}」完成`); })
      .catch((e) => toast(`「${t.name}」${e.message === "已取消" ? "已取消" : "失败：" + e.message}`, 4000));
    return;  // 同时最多恢复一个（正常流程也只会有一个在跑）
  }
}

/* ============ 关窗选择层（桌面客户端点 X 时由 Python 侧 evaluate_js 调起） ============ */
window.showCloseDialog = function () {
  $("#close-remember").checked = false;
  $("#close-mask").classList.add("show");
};
function closeDialogDone(action) {
  const remember = $("#close-remember").checked;
  $("#close-mask").classList.remove("show");
  try { window.pywebview?.api?.close_choice(action, remember); } catch (_) {}
}
$("#close-exit").addEventListener("click", async () => {
  // 有未保存修改先自动保存一把，减少丢稿
  if (MD_DIRTY) await saveArticle({ silent: true });
  closeDialogDone("exit");
});
$("#close-tray").addEventListener("click", () => closeDialogDone("tray"));
$("#close-cancel").addEventListener("click", () => $("#close-mask").classList.remove("show"));
$("#close-mask").addEventListener("click", (e) => {
  if (e.target === $("#close-mask")) $("#close-mask").classList.remove("show");
});

/* ============ 版本更新检查 ============ */
async function loadVersion() {
  try {
    const r = await api("/api/version");
    $("#version-text").textContent = r.version;
  } catch (e) {
    $("#version-text").textContent = "?";
  }
}

async function checkUpdate(manual = false) {
  try {
    const r = await api("/api/check_update", { timeoutMs: 15000 });
    if (r.has_update) {
      showUpdateDialog(r);
    } else if (manual) {
      toast(r.error ? "检查失败：" + r.error : "已是最新版本", 3000);
    }
  } catch (e) {
    if (manual) toast("检查更新失败：" + e.message, 3500);
  }
}

/* 更新弹层：1. 2. 3. 条目化展示 + 内置下载安装 */
function showUpdateDialog(info) {
  $("#up-ver").textContent = "v" + info.remote_version;
  const ol = $("#up-items");
  ol.innerHTML = "";
  const items = (info.changelog_items && info.changelog_items.length)
    ? info.changelog_items : ["其他修复与优化"];
  items.forEach(t => {
    const li = document.createElement("li");
    li.textContent = t;
    ol.appendChild(li);
  });
  $("#up-progress").style.display = "none";
  $("#up-actions").style.display = "";
  $("#update-mask").classList.add("show");

  $("#up-later").onclick = () => $("#update-mask").classList.remove("show");
  $("#up-install").onclick = async () => {
    // Windows 单 exe 走程序内自替换更新；其他平台回退浏览器下载
    const isExe = /\.exe$/i.test(info.download_url || "");
    if (!isExe) {
      if (info.release_url || info.download_url) { api("/api/open_url", { method: "POST", body: { url: info.release_url || info.download_url } }).catch(() => {}); }
      $("#update-mask").classList.remove("show");
      return;
    }
    $("#up-actions").style.display = "none";
    $("#up-progress").style.display = "";
    try {
      await api("/api/update/download", { method: "POST", body: { url: info.download_url }});
      while (true) {
        const p = await api("/api/update/progress", { timeoutMs: 8000 });
        $("#up-bar").style.width = (p.percent || 0) + "%";
        $("#up-progress-text").textContent =
          p.status === "downloading" ? `正在下载… ${p.percent || 0}%` :
          p.status === "ready" ? "下载完成，正在启动安装…" : "准备中…";
        if (p.status === "ready") break;
        if (p.status === "error") throw new Error(p.error || "下载失败");
        await new Promise(res => setTimeout(res, 600));
      }
      await api("/api/update/install", { method: "POST" });
      $("#up-progress-text").textContent = "更新完成，正在切换到新版本…";
      // 旧窗口随即被服务端隐藏并退出，无需额外文案
    } catch (e) {
      $("#up-progress").style.display = "none";
      $("#up-actions").style.display = "";
      toast("更新失败：" + e.message + "，可到 GitHub 手动下载", 5000);
      if (info.release_url || info.download_url) { api("/api/open_url", { method: "POST", body: { url: info.release_url || info.download_url } }).catch(() => {}); }
    }
  };
}

$("#btn-version").addEventListener("click", () => checkUpdate(true));


/* ================= UI: Lucide + ambient canvas ================= */
function refreshIcons(root) {
  if (!window.lucide) return;
  try {
    if (root) lucide.createIcons({ nodes: [root] });
    else lucide.createIcons();
  } catch (_) {}
}
window.refreshIcons = refreshIcons;

function initAmbient() {
  const c = document.getElementById("ambient-canvas");
  if (!c) return;
  const ctx = c.getContext("2d");
  let w, h, dpr, t0 = performance.now();
  /* Soft pastel blobs for light macOS glass look */
  const blobs = [
    { x: 0.20, y: 0.18, r: 0.38, hue: 210, sp: 0.00009 },
    { x: 0.78, y: 0.25, r: 0.32, hue: 280, sp: 0.00007 },
    { x: 0.55, y: 0.80, r: 0.36, hue: 170, sp: 0.00006 },
    { x: 0.12, y: 0.72, r: 0.26, hue: 35, sp: 0.0001 },
  ];
  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = window.innerWidth; h = window.innerHeight;
    c.width = Math.floor(w * dpr);
    c.height = Math.floor(h * dpr);
    c.style.width = w + "px";
    c.style.height = h + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function frame(now) {
    const t = now - t0;
    ctx.clearRect(0, 0, w, h);
    ctx.globalCompositeOperation = "multiply";
    for (let i = 0; i < blobs.length; i++) {
      const b = blobs[i];
      const ox = Math.sin(t * b.sp + i * 1.7) * 0.06;
      const oy = Math.cos(t * b.sp * 0.85 + i) * 0.05;
      const x = (b.x + ox) * w, y = (b.y + oy) * h;
      const R = Math.max(w, h) * b.r;
      const g = ctx.createRadialGradient(x, y, 0, x, y, R);
      g.addColorStop(0, "hsla(" + b.hue + ", 70%, 78%, 0.22)");
      g.addColorStop(0.45, "hsla(" + b.hue + ", 55%, 85%, 0.08)");
      g.addColorStop(1, "hsla(0,0%,100%,0)");
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(x, y, R, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalCompositeOperation = "source-over";
    requestAnimationFrame(frame);
  }
  resize();
  window.addEventListener("resize", resize);
  requestAnimationFrame(frame);
  const stage = document.querySelector(".app-stage");
  if (stage) {
    window.addEventListener("pointermove", (e) => {
      const px = (e.clientX / Math.max(w, 1) - 0.5) * 14;
      const py = (e.clientY / Math.max(h, 1) - 0.5) * 10;
      stage.style.setProperty("--mx", px.toFixed(2) + "px");
      stage.style.setProperty("--my", py.toFixed(2) + "px");
    }, { passive: true });
  }
}

document.addEventListener("pointermove", (e) => {
  const btn = e.target.closest && e.target.closest(".btn.lg, .btn.primary-glow");
  if (!btn) return;
  const r = btn.getBoundingClientRect();
  const x = e.clientX - r.left - r.width / 2;
  const y = e.clientY - r.top - r.height / 2;
  btn.style.transform = "translate(" + (x * 0.12) + "px," + (y * 0.18) + "px)";
}, { passive: true });
document.addEventListener("pointerout", (e) => {
  const btn = e.target.closest && e.target.closest(".btn.lg, .btn.primary-glow");
  if (btn && !btn.contains(e.relatedTarget)) btn.style.transform = "";
}, true);

(async function init() {
  initAmbient();
  refreshIcons();
  bindFieldTips();
  await refreshState();
  renderThemeSeg();
  $("#in-domain").value = "";
  nav(S.publish ? "publish" : (S.has_article ? "article" : "topic"));
  if (S.topics && S.topics.length) renderTopics(S.topics);
  maybeShowWelcome();
  await resumeRunningTasks();
  // 加载版本号并自动检查更新（不弹窗打扰用户）
  await loadVersion();
  checkUpdate(false);  // 后台静默检查，有更新才弹窗
  refreshIcons();
  bindFieldTips();
})();
</script>
</body>
</html>
