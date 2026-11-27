import { App } from "@modelcontextprotocol/ext-apps";

const isStandalone = window.parent === window;
const app = new App({ name: "Antigravity × Codex", version: "0.4.0" }, {});

async function callTool(name, args = {}) {
  if (isStandalone) {
    const r = await fetch("/api/tool", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, arguments: args }),
    });
    return await r.json();
  }
  return await app.callServerTool({ name, arguments: args });
}

let state = {
  sidebar_projects: [],
  active_project: "",
  project_name: "",
  change: null,
  selected_session_id: "",
  rounds: [],
  telemetry: {},
  tasks: [],
};
let selectedProject = "";
let selectedSession = "";
let searchQuery = "";
let autoFollow = true;
let themeMode = "auto"; // auto | light | dark
let hostTheme = "light";
let surfaceMode = "solid"; // solid | frosted | glass
const narrowWindow = window.matchMedia("(max-width: 720px)");
let polling = false;
let snapshotJson = "";
let sidebarHtml = "";
let feedHtml = "";
const expandedDiffs = new Map();
const expandedPrompts = new Set();

const ICON = {
  sparkle: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M12 2L14.6 9.4L22 12L14.6 14.6L12 22L9.4 14.6L2 12L9.4 9.4L12 2Z" fill="url(#agG)"/><defs><linearGradient id="agG" x1="2" y1="2" x2="22" y2="22"><stop stop-color="#4F8DF7"/><stop offset="0.5" stop-color="#9B72CB"/><stop offset="1" stop-color="#D96570"/></linearGradient></defs></svg>`,
  codex: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5L7 12l2.5 2.5M14.5 9.5L17 12l-2.5 2.5M13 8.5l-2 7"/></svg>`,
  folder: `<svg width="13.5" height="13.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>`,
  search: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>`,
  thought: `<svg width="13.5" height="13.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8.5 8.5 0 0 1-8.5 8.5H7l-4 2 1.4-4.2A8.5 8.5 0 1 1 21 12z"/></svg>`,
  file: `<svg width="12.5" height="12.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>`,
  copy: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`,
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtTokens(n) {
  n = Number(n || 0);
  if (n >= 1000000) return (n / 1000000).toFixed(2) + "M";
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return String(n);
}

function formatInline(text) {
  const format = (escaped) => escaped.replace(/`([^`\n]+)`|\*\*([^*\n]+)\*\*|\*([^*\n]+)\*|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, code, bold, italic, label, href) => {
      if (code) return `<code class="ic">${code}</code>`;
      if (bold) return `<strong>${format(bold)}</strong>`;
      if (italic) return `<em>${format(italic)}</em>`;
      return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    });
  return format(esc(text));
}

function renderMarkdown(text) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let paragraph = [];
  let list = "";
  const endParagraph = () => {
    if (paragraph.length) blocks.push(`<p>${paragraph.join("<br>")}</p>`);
    paragraph = [];
  };
  const endList = () => {
    if (list) blocks.push(`</${list}>`);
    list = "";
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = line.match(/^ {0,3}(`{3,}|~{3,})[\w+-]*\s*$/);
    const heading = line.match(/^ {0,3}(#{1,6})\s+(.+)$/);
    const item = line.match(/^ {0,3}([-*+]|\d+\.)\s+(.+)$/);
    if (item) {
      endParagraph();
      const tag = item[1].endsWith(".") ? "ol" : "ul";
      if (tag !== list) {
        endList();
        list = tag;
        blocks.push(tag === "ol" ? `<ol start="${parseInt(item[1], 10)}">` : "<ul>");
      }
      blocks.push(`<li>${formatInline(item[2])}</li>`);
      continue;
    }

    endList();
    if (fence) {
      endParagraph();
      const closing = new RegExp(`^ {0,3}${fence[1][0]}{${fence[1].length},}\\s*$`);
      const code = [];
      while (++i < lines.length && !closing.test(lines[i])) code.push(lines[i]);
      blocks.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
    } else if (heading) {
      endParagraph();
      const level = heading[1].length;
      blocks.push(`<h${level}>${formatInline(heading[2])}</h${level}>`);
    } else if (line.trim()) {
      paragraph.push(formatInline(line));
    } else {
      endParagraph();
    }
  }

  endList();
  endParagraph();
  return blocks.join("");
}

function toast(msg) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(window._tt);
  window._tt = setTimeout(() => el.classList.remove("show"), 2000);
}

function applyAppearance() {
  const effectiveTheme = themeMode === "auto" ? hostTheme : themeMode;
  document.documentElement.setAttribute("data-theme", effectiveTheme);
  document.documentElement.setAttribute("data-surface", surfaceMode);
  const themeLabels = { auto: "自动", light: "浅色", dark: "深色" };
  const surfLabels = { solid: "纯净", frosted: "磨砂", glass: "液态玻璃" };
  document.getElementById("themeBtn").textContent = themeLabels[themeMode];
  document.getElementById("surfaceBtn").textContent = surfLabels[surfaceMode];
}

function renderReviewDiff(filePath, diffText) {
  const lines = (diffText || "").split("\n");
  let oldLn = 0, newLn = 0, adds = 0, dels = 0;
  const rows = [];
  for (const line of lines) {
    const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if (hunk) {
      oldLn = parseInt(hunk[1], 10);
      newLn = parseInt(hunk[2], 10);
      rows.push(`<div class="drow hunk"><span class="ln"></span><span class="ln"></span><span class="dc">${esc(line)}</span></div>`);
      continue;
    }
    if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ") || line.startsWith("index ")) {
      continue;
    }
    if (line.startsWith("+")) {
      adds++;
      rows.push(`<div class="drow add"><span class="ln"></span><span class="ln">${newLn++}</span><span class="dc">${esc(line)}</span></div>`);
    } else if (line.startsWith("-")) {
      dels++;
      rows.push(`<div class="drow del"><span class="ln">${oldLn++}</span><span class="ln"></span><span class="dc">${esc(line)}</span></div>`);
    } else {
      const oStr = oldLn > 0 ? oldLn++ : "";
      const nStr = newLn > 0 ? newLn++ : "";
      rows.push(`<div class="drow"><span class="ln">${oStr}</span><span class="ln">${nStr}</span><span class="dc">${esc(line)}</span></div>`);
    }
  }
  return `
    <div class="inline-diff">
      <div class="diff-bar">
        <div class="diff-stats"><span class="stat-add">+${adds}</span><span class="stat-del">-${dels}</span><code class="mono-path">${esc(filePath)}</code></div>
        <button class="copy-btn" data-copy="${esc(filePath)}" title="复制路径">${ICON.copy} 复制路径</button>
      </div>
      <div class="diff-scroll">${rows.join("") || `<div class="muted" style="padding:8px;">暂无差异行</div>`}</div>
    </div>
  `;
}

function renderSidebar() {
  const listEl = document.getElementById("sidebarList");
  const groups = state.sidebar_projects || [];
  const q = searchQuery.trim().toLowerCase();

  const html = groups.length ? groups.map((g) => {
    const filtered = (g.sessions || []).filter((s) => !q || s.title.toLowerCase().includes(q) || g.name.toLowerCase().includes(q));
    if (q && !filtered.length) return "";
    return `
      <div class="proj-group">
        <div class="proj-head">
          <span class="proj-name">${ICON.folder}<span>${esc(g.name)}</span></span>
          <span class="proj-cnt">${filtered.length}</span>
        </div>
        <div class="sess-list">
          ${filtered.map((s) => {
            const active = s.session_id === state.selected_session_id;
            return `<button class="sess-card ${active ? "active" : ""}" data-proj="${esc(s.project_path)}" data-sid="${esc(s.session_id)}">
              <div class="sess-title-row">
                <span class="sess-title">${esc(s.title)}</span>
                ${s.is_active ? `<span class="live-dot"></span>` : ""}
              </div>
              <div class="sess-meta">✦→◎ · ${esc(s.started_label)}</div>
            </button>`;
          }).join("")}
        </div>
      </div>
    `;
  }).join("") : `<div class="empty-side">暂无监听项目</div>`;

  if (html === sidebarHtml) return;
  const scrollTop = listEl.scrollTop;
  listEl.innerHTML = sidebarHtml = html;
  listEl.scrollTop = scrollTop;

  listEl.querySelectorAll(".sess-card").forEach((btn) => {
    btn.addEventListener("click", () => {
      selectedProject = btn.getAttribute("data-proj") || "";
      selectedSession = btn.getAttribute("data-sid") || "";
      expandedDiffs.clear();
      expandedPrompts.clear();
      if (narrowWindow.matches) setSidebarOpen(false);
      pollState();
    });
  });
}

function renderMain({ follow = false, sessionChanged = false } = {}) {
  const tel = state.telemetry || {};
  document.getElementById("headerTitle").textContent = state.project_name || "Antigravity × Codex";

  const subMeta = [
    "Antigravity → Codex",
    tel.model || "gpt-6.2-sol",
    tel.effort || "high",
    `${tel.turns || 0} 轮`,
    `${fmtTokens(tel.input_tokens)} tokens`,
  ].join(" · ");
  document.getElementById("headerSubText").textContent = subMeta;

  const cachePill = document.getElementById("cachePill");
  cachePill.textContent = `${tel.cache_hit_pct ?? 0}% 缓存`;
  document.getElementById("cachePopover").innerHTML = `
    <div class="pop-row"><span>模型</span><strong>${esc(tel.model || "-")} (${esc(tel.effort || "-")})</strong></div>
    <div class="pop-row"><span>总输入 Token</span><strong>${fmtTokens(tel.input_tokens)}</strong></div>
    <div class="pop-row"><span>缓存命中</span><strong style="color:var(--pill-pass-fg)">${fmtTokens(tel.cached_input_tokens)} (${tel.cache_hit_pct ?? 0}%)</strong></div>
    <div class="pop-row"><span>输出 / 思考</span><strong>${fmtTokens(tel.output_tokens)} / ${fmtTokens(tel.reasoning_output_tokens)}</strong></div>
  `;

  // Floating dock pause state
  const ch = state.change;
  const isPaused = ch && ch.control_flag === "pause";
  const dockPause = document.getElementById("dockPauseBtn");
  dockPause.textContent = isPaused ? "恢复继续" : "暂停下一步";
  dockPause.className = isPaused ? "dock-btn ok" : "dock-btn warn";

  const rounds = state.rounds || [];
  const anyLive = rounds.some((r) => r.is_active);
  const statusEl = document.getElementById("liveStatusText");
  statusEl.innerHTML = anyLive
    ? `<span class="live-dot pulse"></span>实时监控`
    : `<span class="idle-dot"></span>已就绪`;

  const feed = document.getElementById("chatFeed");
  const html = rounds.length ? rounds.map((r, rIdx) => {
    const stepStatus = r.is_active
      ? `正在执行 · ${r.commands.length} 条命令 · ${r.files_changed.length} 个文件`
      : `已完成执行 · ${r.commands.length} 条命令 · ${r.files_changed.length} 个文件 · ${r.duration_sec}秒`;

    const promptKey = `${state.selected_session_id}:${r.round_no}`;
    const isLong = r.dispatch_brief.length > 260;
    const isExpanded = expandedPrompts.has(promptKey);

    const filesHtml = r.files_changed.map((f) => {
      const hasDiff = expandedDiffs.has(f.path);
      return `
        <div class="exec-row">
          <div class="exec-left">
            <span class="pill-edit">${esc(f.action)}</span>
            <code class="mono-path">${esc(f.path)}</code>
          </div>
          <button class="link-btn diff-toggle" data-file="${esc(f.path)}">${hasDiff ? "收起变更 ▴" : "查看变更 ▾"}</button>
        </div>
        ${hasDiff ? renderReviewDiff(f.path, expandedDiffs.get(f.path) || "") : ""}
      `;
    }).join("");

    const cmdsHtml = r.commands.map((c, i) => {
      const badgeCls = c.status === "fail" ? "cmd-fail" : (c.status === "running" ? "cmd-run" : "cmd-pass");
      const badgeTxt = c.status === "fail" ? "失败" : (c.status === "running" ? "运行中..." : `通过${c.duration ? " · " + c.duration : ""}`);
      const command = `<code class="mono-cmd" title="${esc(c.cmd)}">$ ${esc(c.cmd)}</code>`;
      const badge = `<span class="cmd-pill ${badgeCls}">${esc(badgeTxt)}</span>`;
      return c.output ? `
        <details class="cmd-details" data-detail-key="${esc(`${r.round_no}:cmd:${c.call_id || i}`)}">
          <summary class="exec-row cmd-summary" aria-label="${esc(`命令输出：${c.cmd}，${badgeTxt}`)}">
            ${command}${badge}<span class="disclosure" aria-hidden="true"></span>
          </summary>
          <pre class="cmd-output" tabindex="0"><code>${esc(c.output)}</code></pre>
        </details>
      ` : `<div class="exec-row">${command}${badge}</div>`;
    }).join("");

    const chipsHtml = r.files_changed.length
      ? `<div class="chip-row">${r.files_changed.map((f) => `<span class="file-chip">${ICON.file}<span>${esc(f.path)}</span></span>`).join("")}</div>`
      : "";

    const divider = rIdx > 0 ? `<div class="round-divider"><span>第 ${r.round_no} 轮 · ${esc(r.dispatch_time)}</span></div>` : "";

    return `
      ${divider}
      <div class="turn-block">
        <div class="ag-wrap">
          <div class="ag-meta">
            <span>${esc(r.dispatch_time)}</span>
            <span class="round-pill">第${r.round_no}轮派单</span>
            <span>Antigravity</span>
            <span class="avatar">${ICON.sparkle}</span>
          </div>
          <div class="ag-bubble ${isLong && !isExpanded ? "clamped" : ""}">
            <div class="ag-text markdown">${renderMarkdown(r.dispatch_brief)}</div>
            ${isLong ? `<button class="expand-btn" data-pkey="${esc(promptKey)}">${isExpanded ? "收起全文 ▴" : "展开全文 ▾"}</button>` : ""}
          </div>
        </div>

        <div class="cx-wrap">
          <div class="cx-meta">
            <span class="avatar">${ICON.codex}</span>
            <strong>Codex</strong>
            <span class="muted">${esc(r.model)} · ${esc(r.effort)}</span>
          </div>

          <details class="exec-card" data-detail-key="${r.round_no}" open>
            <summary class="exec-summary">
              <span><span class="star ${r.is_active ? "spin" : ""}">✦</span> ${esc(stepStatus)}</span>
              <span class="disclosure" aria-hidden="true"></span>
            </summary>
            <div class="exec-inner">
              ${r.commentary ? `<div class="thought-box"><span class="t-ic">${ICON.thought}</span><span>${esc(r.commentary)}</span></div>` : ""}
              ${filesHtml}
              ${cmdsHtml}
            </div>
          </details>

          ${r.final_summary ? `
            <div class="summary-card">
              <div class="summary-text markdown">${renderMarkdown(r.final_summary)}</div>
              ${chipsHtml}
            </div>
          ` : ""}
        </div>
      </div>
    `;
  }).join("") : `<div class="empty-main">选择左侧项目或会话，即可实时查看 Antigravity → Codex 执行流</div>`;

  if (html === feedHtml && !sessionChanged) return;
  const scrollTop = feed.scrollTop;
  const nearBottom = feed.scrollHeight - feed.clientHeight - scrollTop <= 24;
  const openDetails = new Map();
  if (!sessionChanged) {
    feed.querySelectorAll("details[data-detail-key]").forEach((detail) => {
      openDetails.set(detail.dataset.detailKey, detail.open);
    });
  }
  feed.innerHTML = feedHtml = html;
  feed.querySelectorAll("details[data-detail-key]").forEach((detail) => {
    if (openDetails.has(detail.dataset.detailKey)) detail.open = openDetails.get(detail.dataset.detailKey);
  });

  feed.querySelectorAll(".expand-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const k = btn.getAttribute("data-pkey") || "";
      if (expandedPrompts.has(k)) expandedPrompts.delete(k);
      else expandedPrompts.add(k);
      renderMain();
    });
  });

  feed.querySelectorAll(".diff-toggle").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const fpath = btn.getAttribute("data-file") || "";
      if (expandedDiffs.has(fpath)) {
        expandedDiffs.delete(fpath);
        renderMain();
        return;
      }
      expandedDiffs.set(fpath, "正在加载实时 Diff...");
      renderMain();
      try {
        const res = await callTool("app_get_diff", {
          project: selectedProject || state.active_project,
          file: fpath,
        });
        expandedDiffs.set(fpath, res?.structuredContent?.diff || "(暂无变更)");
      } catch (e) {
        expandedDiffs.set(fpath, "读取 Diff 失败");
      }
      renderMain();
    });
  });

  feed.querySelectorAll(".copy-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const text = btn.getAttribute("data-copy") || "";
      try {
        await navigator.clipboard.writeText(text);
        toast("已复制路径");
      } catch (e) {
        toast(text);
      }
    });
  });

  feed.scrollTop = follow && autoFollow && (sessionChanged || nearBottom)
    ? feed.scrollHeight
    : sessionChanged ? 0 : scrollTop;
}

function applySnapshot(nextState) {
  const json = JSON.stringify(nextState);
  if (json === snapshotJson) return;
  snapshotJson = json;
  const sessionChanged = nextState.active_project !== state.active_project
    || nextState.selected_session_id !== state.selected_session_id;
  state = nextState;
  if (!selectedProject && state.active_project) selectedProject = state.active_project;
  if (!selectedSession && state.selected_session_id) selectedSession = state.selected_session_id;
  renderSidebar();
  renderMain({ follow: true, sessionChanged });
}

async function pollState() {
  if (polling) return;
  polling = true;
  try {
    const res = await callTool("app_poll_state", {
      ...(selectedProject ? { project: selectedProject } : {}),
      ...(selectedSession ? { session: selectedSession } : {}),
    });
    if (res?.structuredContent) applySnapshot(res.structuredContent);
  } catch (e) {
    // ignore transient close
  } finally {
    polling = false;
  }
}

async function togglePause() {
  const ch = state.change;
  if (!ch) { toast("当前项目暂无活跃 Change"); return; }
  const nextFlag = ch.control_flag === "pause" ? "none" : "pause";
  await callTool("app_set_control", {
    project: selectedProject || state.active_project,
    change_id: ch.change_id,
    flag: nextFlag,
    note: ch.user_note || "",
  });
  toast(nextFlag === "pause" ? "已暂停下一步派发" : "已恢复继续");
  pollState();
}

async function sendDockNote() {
  const ch = state.change;
  const input = document.getElementById("dockNoteInput");
  const note = input.value.trim();
  if (!ch || !note) return;
  await callTool("app_set_control", {
    project: selectedProject || state.active_project,
    change_id: ch.change_id,
    flag: ch.control_flag || "none",
    note,
  });
  input.value = "";
  toast("叮嘱已保存，下一轮派发自动生效");
  pollState();
}

app.ontoolresult = (result) => {
  if (result?.structuredContent) applySnapshot(result.structuredContent);
};
app.onhostcontextchanged = (ctx) => {
  if (ctx?.theme === "dark" || ctx?.theme === "light") {
    hostTheme = ctx.theme;
    applyAppearance();
  }
};

document.getElementById("brandIcon").innerHTML = `${ICON.sparkle}${ICON.codex}`;
document.getElementById("searchIcon").innerHTML = ICON.search;
document.getElementById("searchInput").addEventListener("input", (e) => {
  searchQuery = e.target.value;
  renderSidebar();
});
document.getElementById("followToggle").addEventListener("change", (e) => {
  autoFollow = e.target.checked;
  if (autoFollow) {
    const feed = document.getElementById("chatFeed");
    feed.scrollTop = feed.scrollHeight;
  }
});
document.getElementById("themeBtn").addEventListener("click", () => {
  const order = ["auto", "light", "dark"];
  themeMode = order[(order.indexOf(themeMode) + 1) % order.length];
  applyAppearance();
});
document.getElementById("surfaceBtn").addEventListener("click", () => {
  const order = ["solid", "frosted", "glass"];
  surfaceMode = order[(order.indexOf(surfaceMode) + 1) % order.length];
  applyAppearance();
});
document.getElementById("refreshBtn").addEventListener("click", pollState);
const shell = document.getElementById("appShell");
const sidebarToggle = document.getElementById("toggleSidebarBtn");
function setSidebarOpen(open) {
  shell.classList.toggle("hide-side", !open);
  sidebarToggle.setAttribute("aria-expanded", String(open));
}
setSidebarOpen(!narrowWindow.matches);
narrowWindow.addEventListener("change", (e) => setSidebarOpen(!e.matches));
sidebarToggle.addEventListener("click", () => {
  setSidebarOpen(shell.classList.contains("hide-side"));
});
document.getElementById("dockPauseBtn").addEventListener("click", togglePause);
document.getElementById("dockSendBtn").addEventListener("click", sendDockNote);
document.getElementById("dockNoteInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendDockNote();
});

(async () => {
  if (!isStandalone) {
    await app.connect();
    const ctx = app.getHostContext();
    if (ctx?.theme === "dark" || ctx?.theme === "light") hostTheme = ctx.theme;
  } else {
    hostTheme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  applyAppearance();
  await pollState();
  setInterval(pollState, 2000);
})();
