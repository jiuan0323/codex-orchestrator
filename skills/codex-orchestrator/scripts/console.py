"""Zero-dependency compact mini-window console dashboard with live Codex CLI streaming."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402


def _norm_path(p: Optional[str]) -> str:
    if not p:
        return ""
    try:
        return str(Path(p).resolve()).lower().rstrip("\\/")
    except Exception:
        return p.lower().rstrip("\\/")


def _codex_sessions_root() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home) / "sessions"
    return Path.home() / ".codex" / "sessions"


def _read_session_meta(path: Path) -> Optional[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for _ in range(5):
                line = f.readline()
                if not line:
                    break
                obj = json.loads(line)
                if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
                    payload = obj["payload"]
                    return {
                        "session_id": payload.get("id") or payload.get("session_id") or path.stem,
                        "cwd": payload.get("cwd") or "",
                        "started_at": payload.get("timestamp") or obj.get("timestamp") or "",
                        "path": str(path),
                        "mtime": path.stat().st_mtime,
                    }
    except Exception:
        return None
    return None


def find_project_codex_sessions(project_roots: list[str], max_scan: int = 40) -> list[dict[str, Any]]:
    root = _codex_sessions_root()
    if not root.exists():
        return []
    targets = {_norm_path(r) for r in project_roots if r}
    files = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:max_scan]
    matched: list[dict[str, Any]] = []
    now = time.time()
    for fp in files:
        meta = _read_session_meta(fp)
        if not meta:
            continue
        cwd_norm = _norm_path(meta["cwd"])
        if targets and cwd_norm not in targets:
            continue
        meta["is_active"] = (now - meta["mtime"]) < 45
        meta["age_sec"] = int(max(0, now - meta["mtime"]))
        matched.append(meta)
        if len(matched) >= 12:
            break
    return matched


def parse_codex_session_events(path: Path, limit: int = 80) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return events

    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = (obj.get("timestamp") or "")[11:19]
        etype = obj.get("type")
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue

        if etype == "response_item":
            ptype = payload.get("type")
            if ptype == "message" and payload.get("role") == "assistant":
                parts = payload.get("content") or []
                texts = [
                    p.get("text", "").strip()
                    for p in parts
                    if isinstance(p, dict) and p.get("type") in ("output_text", "text") and p.get("text")
                ]
                body = "\n".join(texts).strip()
                if body:
                    events.append({"time": ts, "kind": "assistant", "label": "Codex", "text": body})
            elif ptype in ("custom_tool_call", "function_call"):
                name = payload.get("name") or "tool"
                raw_in = payload.get("input") or payload.get("arguments") or ""
                if not isinstance(raw_in, str):
                    raw_in = json.dumps(raw_in, ensure_ascii=False)
                events.append(
                    {
                        "time": ts,
                        "kind": "tool_call",
                        "label": f"$ {name}",
                        "text": raw_in.strip()[:1200],
                    }
                )
            elif ptype in ("custom_tool_call_output", "function_call_output"):
                out = payload.get("output")
                text_out = ""
                if isinstance(out, str):
                    text_out = out
                elif isinstance(out, list):
                    chunks = [
                        item["text"]
                        for item in out
                        if isinstance(item, dict) and isinstance(item.get("text"), str)
                    ]
                    text_out = "".join(chunks)
                text_out = text_out.strip()
                if text_out:
                    events.append(
                        {
                            "time": ts,
                            "kind": "tool_output",
                            "label": "输出",
                            "text": text_out[:1200],
                        }
                    )
        elif etype == "event_msg" and payload.get("type") == "task_started":
            events.append({"time": ts, "kind": "system", "label": "开始", "text": "Codex CLI 已启动新轮次"})
        elif etype == "event_msg" and payload.get("type") == "task_complete":
            events.append({"time": ts, "kind": "system", "label": "完成", "text": "Codex CLI 本轮执行完成"})

    return events[-limit:]


def open_mini_window(url: str, width: int = 480, height: int = 780) -> None:
    """Open URL as a clean frameless app mini-window (Edge/Chrome --app mode), fallback to default browser."""
    candidates: list[str] = []
    if sys.platform == "win32":
        candidates = [
            shutil.which("msedge") or "",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            shutil.which("chrome") or "",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    else:
        candidates = [
            shutil.which("google-chrome") or "",
            shutil.which("chromium") or "",
            shutil.which("microsoft-edge") or "",
        ]

    for exe in candidates:
        if exe and Path(exe).exists():
            try:
                subprocess.Popen(
                    [exe, f"--app={url}", f"--window-size={width},{height}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except OSError:
                continue
    webbrowser.open(url)


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Codex 实时小窗</title>
<style>
  :root {
    --bg: #0b0f14;
    --panel: #131922;
    --panel-2: #19222e;
    --term: #080b10;
    --border: #253041;
    --text: #eef3f8;
    --muted: #8a9bb0;
    --blue: #58a6ff;
    --green: #3fb950;
    --yellow: #e3b341;
    --red: #f85149;
    --purple: #bc8cff;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 13px;
    line-height: 1.5;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* Compact top bar */
  .top {
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    padding: 10px 12px 8px;
    flex-shrink: 0;
  }
  .top-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .live-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    font-weight: 700;
    color: var(--muted);
  }
  .live-pill.active { color: var(--green); }
  .live-pill.blocked { color: var(--red); }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
  .live-pill.active .dot { animation: pulse 1.3s infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }

  .pct { font-size: 12px; font-weight: 700; color: var(--blue); }
  .btn-row { display: flex; gap: 6px; align-items: center; }
  button, select, input {
    background: var(--panel-2);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 12px;
    font-family: inherit;
  }
  button { cursor: pointer; font-weight: 600; }
  button:hover { border-color: var(--blue); }
  button.warn { border-color: var(--yellow); color: var(--yellow); }
  button.ok { border-color: var(--green); color: var(--green); }
  button.primary { background: #1f6feb; border-color: #388bfd; color: #fff; }

  .bar-track { width: 100%; height: 6px; background: #1e2736; border-radius: 999px; overflow: hidden; margin-top: 8px; }
  .bar-fill { height: 100%; background: linear-gradient(90deg, #2ea043, #58a6ff); transition: width 0.3s; }

  /* Pinned current assignment from Antigravity */
  .mission {
    background: #101826;
    border-bottom: 1px solid var(--border);
    padding: 8px 12px;
    flex-shrink: 0;
  }
  .mission-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    font-size: 12px;
    color: var(--blue);
    font-weight: 700;
  }
  .mission-title {
    margin-top: 3px;
    font-size: 12.5px;
    color: var(--text);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .mission details { margin-top: 4px; }
  .mission summary { cursor: pointer; font-size: 11.5px; color: var(--muted); }
  .mission pre {
    margin: 6px 0 0;
    padding: 8px;
    background: var(--term);
    border: 1px solid var(--border);
    border-radius: 6px;
    max-height: 180px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 11.5px;
    color: #c9d6e4;
  }

  /* View tabs */
  .nav {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 10px;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
    gap: 6px;
  }
  .tabs { display: flex; gap: 4px; }
  .tab {
    background: transparent;
    border: 1px solid transparent;
    color: var(--muted);
    padding: 4px 9px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
  }
  .tab.active { background: #1f6feb; color: #fff; }

  /* Main scrollable area */
  .body {
    flex: 1;
    overflow-y: auto;
    background: var(--term);
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .term-item {
    border-radius: 7px;
    padding: 8px 10px;
    border: 1px solid #1d2635;
    background: #10151e;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 12px;
  }
  .term-item.k-assistant { border-left: 3px solid var(--blue); background: #0f1826; font-family: inherit; font-size: 12.5px; }
  .term-item.k-tool_call { border-left: 3px solid var(--green); }
  .term-item.k-tool_output { border-left: 3px solid #445266; background: #0b0f16; }
  .term-item.k-system { border-left: 3px solid var(--purple); }
  .term-head { display: flex; justify-content: space-between; font-size: 11px; color: var(--muted); margin-bottom: 4px; }
  .k-assistant .term-label { color: var(--blue); font-weight: 700; }
  .k-tool_call .term-label { color: var(--green); font-weight: 700; }
  .k-system .term-label { color: var(--purple); font-weight: 700; }
  .term-text { margin: 0; white-space: pre-wrap; word-break: break-word; color: #dbe4ef; font-family: inherit; }
  .term-item details summary { cursor: pointer; color: var(--muted); font-size: 11.5px; }

  .step-card {
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 9px 11px;
    background: var(--panel);
    border-left: 3px solid var(--border);
  }
  .step-card.st-done { border-left-color: var(--green); }
  .step-card.st-running { border-left-color: var(--blue); background: rgba(88,166,255,0.08); }
  .step-card.st-blocked { border-left-color: var(--red); background: rgba(248,81,73,0.08); }
  .step-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
  .badge { padding: 1px 7px; border-radius: 999px; font-size: 11px; font-weight: 700; }
  .b-done { background: rgba(63,185,80,0.2); color: var(--green); }
  .b-running { background: rgba(88,166,255,0.22); color: var(--blue); }
  .b-pending { background: rgba(139,155,176,0.16); color: var(--muted); }
  .b-blocked { background: rgba(248,81,73,0.22); color: var(--red); }
  .b-reviewing { background: rgba(188,140,255,0.22); color: var(--purple); }
  .b-failed, .b-rolled_back { background: rgba(227,179,65,0.22); color: var(--yellow); }

  /* Bottom quick input bar */
  .foot {
    background: var(--panel);
    border-top: 1px solid var(--border);
    padding: 8px 10px;
    display: flex;
    gap: 6px;
    flex-shrink: 0;
  }
  .foot input { flex: 1; min-width: 0; }
  .toast {
    position: fixed;
    right: 12px;
    bottom: 54px;
    background: #1f6feb;
    color: #fff;
    padding: 6px 12px;
    border-radius: 6px;
    font-size: 12px;
    display: none;
    z-index: 99;
  }
</style>
</head>
<body>
<div class="top">
  <div class="top-row">
    <span id="liveBadge" class="live-pill"><span class="dot"></span>连接中...</span>
    <span id="pctText" class="pct">0/0</span>
    <div class="btn-row">
      <button id="pauseBtn" class="warn" onclick="togglePause()">暂停</button>
      <button onclick="refreshAll()">刷新</button>
    </div>
  </div>
  <div class="bar-track"><div id="barFill" class="bar-fill" style="width:0%"></div></div>
</div>

<div class="mission">
  <div class="mission-head">
    <span id="missionTag">当前任务</span>
    <span id="changeName" style="color:var(--muted);font-weight:500;"></span>
  </div>
  <div id="missionTitle" class="mission-title">正在读取 Antigravity 派发的任务...</div>
  <details id="missionDetails">
    <summary>查看 Antigravity 发给 Codex 的完整指令</summary>
    <pre id="missionPrompt">暂无派发记录</pre>
  </details>
</div>

<div class="nav">
  <div class="tabs">
    <button id="tLive" class="tab active" onclick="setTab('live')">CLI 直播</button>
    <button id="tTasks" class="tab" onclick="setTab('tasks')">任务清单</button>
    <button id="tChat" class="tab" onclick="setTab('chat')">交接记录</button>
  </div>
  <select id="sessionSelect" style="max-width:155px;"></select>
</div>

<div id="mainBody" class="body"></div>

<div class="foot">
  <input id="noteInput" type="text" placeholder="叮嘱 AI 一句话（下次派发自动生效）..." />
  <button class="primary" onclick="saveNote()">发送</button>
</div>

<div id="toast" class="toast"></div>

<script>
const ST_CN = {
  done: "已完成 ✓",
  running: "运行中",
  pending: "排队中",
  blocked: "卡住了",
  reviewing: "待验收",
  failed: "失败",
  rolled_back: "已回滚"
};
const SENDER_CN = {
  antigravity: "Antigravity",
  codex: "Codex",
  gate: "门禁",
  human: "你"
};

let statusState = { changes: [], all_changes: [], tasks: [], decisions: [], recent_messages: [] };
let liveState = { sessions: [], selected_session_id: "", events: [] };
let selectedSession = "";
let activeTab = "live";

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;" }[c]));
}
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.style.display = "block";
  clearTimeout(window._tt);
  window._tt = setTimeout(() => el.style.display = "none", 2000);
}
function setTab(t) {
  activeTab = t;
  ["live","tasks","chat"].forEach(k => {
    document.getElementById("t" + k[0].toUpperCase() + k.slice(1)).classList.toggle("active", k === t);
  });
  renderBody();
}

async function refreshAll() {
  const q2 = selectedSession ? `?session=${encodeURIComponent(selectedSession)}&limit=80` : "?limit=80";
  const [r1, r2] = await Promise.all([fetch("/api/status?limit=50"), fetch("/api/live" + q2)]);
  statusState = await r1.json();
  liveState = await r2.json();
  renderAll();
}

function renderAll() {
  const allChanges = statusState.all_changes || statusState.changes || [];
  const curChange = allChanges[0] || null;
  window._curChange = curChange;

  const tasks = statusState.tasks || [];
  const doneCount = tasks.filter(t => t.status === "done").length;
  const pct = tasks.length ? Math.round((doneCount / tasks.length) * 100) : 0;
  const runningTask = tasks.find(t => t.status === "running");
  const blockedTask = tasks.find(t => t.status === "blocked");
  const focusTask = blockedTask || runningTask || tasks.find(t => t.status === "reviewing") || tasks.find(t => t.status === "pending") || tasks[tasks.length - 1];

  document.getElementById("pctText").textContent = `${doneCount}/${tasks.length} (${pct}%)`;
  document.getElementById("barFill").style.width = pct + "%";
  document.getElementById("changeName").textContent = curChange ? curChange.change_id : "";

  const isPaused = curChange && curChange.control_flag === "pause";
  const pBtn = document.getElementById("pauseBtn");
  pBtn.textContent = isPaused ? "继续" : "暂停";
  pBtn.className = isPaused ? "ok" : "warn";

  const anyLive = (liveState.sessions || []).some(s => s.is_active);
  const badge = document.getElementById("liveBadge");
  if (blockedTask) {
    badge.className = "live-pill blocked";
    badge.innerHTML = `<span class="dot"></span>卡住等拍板`;
  } else if (anyLive || runningTask) {
    badge.className = "live-pill active";
    badge.innerHTML = `<span class="dot"></span>Codex CLI 运行中`;
  } else {
    badge.className = "live-pill";
    badge.innerHTML = `<span class="dot"></span>空闲`;
  }

  // Current mission box
  if (focusTask) {
    document.getElementById("missionTag").textContent = `当前任务 ${focusTask.task_key} · ${ST_CN[focusTask.status] || focusTask.status}`;
    document.getElementById("missionTitle").textContent = focusTask.title;
  } else {
    document.getElementById("missionTag").textContent = "暂无任务";
    document.getElementById("missionTitle").textContent = "等待同步 OpenSpec 任务";
  }

  const msgs = statusState.recent_messages || [];
  const lastDispatch = [...msgs].reverse().find(m => m.msg_type === "dispatch" && (!focusTask || m.task_key === focusTask.task_key))
    || [...msgs].reverse().find(m => m.msg_type === "dispatch");
  document.getElementById("missionPrompt").textContent = lastDispatch ? lastDispatch.content : "暂无 Antigravity 派发记录";

  const noteInput = document.getElementById("noteInput");
  if (curChange && document.activeElement !== noteInput && !noteInput.value) {
    noteInput.placeholder = curChange.user_note ? `当前叮嘱：${curChange.user_note}` : "叮嘱 AI 一句话（下次派发自动生效）...";
  }

  // Session selector
  const ssel = document.getElementById("sessionSelect");
  const sessions = liveState.sessions || [];
  ssel.style.display = activeTab === "live" ? "block" : "none";
  ssel.innerHTML = sessions.length
    ? sessions.map((s, idx) => `<option value="${esc(s.session_id)}" ${s.session_id === liveState.selected_session_id ? "selected" : ""}>${s.is_active ? "🟢 " : ""}会话 #${sessions.length - idx}</option>`).join("")
    : `<option value="">无会话</option>`;
  ssel.onchange = () => { selectedSession = ssel.value; refreshAll(); };

  renderBody();
}

function renderBody() {
  const body = document.getElementById("mainBody");
  const ssel = document.getElementById("sessionSelect");
  if (ssel) ssel.style.display = activeTab === "live" ? "block" : "none";

  if (activeTab === "live") {
    const wasAtBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 60;
    const evts = liveState.events || [];
    if (!evts.length) {
      body.innerHTML = `<div style="color:var(--muted);padding:12px;">等待 Codex CLI 输出...</div>`;
      return;
    }
    body.innerHTML = evts.map(e => {
      if (e.kind === "tool_output") {
        return `<div class="term-item k-tool_output">
          <div class="term-head"><span class="term-label">${esc(e.label)}</span><span>${esc(e.time)}</span></div>
          <details><summary>展开输出（${e.text.length} 字）</summary><pre class="term-text" style="margin-top:4px;">${esc(e.text)}</pre></details>
        </div>`;
      }
      return `<div class="term-item k-${esc(e.kind)}">
        <div class="term-head"><span class="term-label">${esc(e.label)}</span><span>${esc(e.time)}</span></div>
        <pre class="term-text">${esc(e.text)}</pre>
      </div>`;
    }).join("");
    if (wasAtBottom) body.scrollTop = body.scrollHeight;
    return;
  }

  if (activeTab === "tasks") {
    const tasks = statusState.tasks || [];
    body.innerHTML = tasks.length
      ? tasks.map(t => `
        <div class="step-card st-${esc(t.status)}">
          <div class="step-top">
            <strong style="color:var(--blue)">任务 ${esc(t.task_key)}</strong>
            <span class="badge b-${esc(t.status)}">${esc(ST_CN[t.status] || t.status)}</span>
          </div>
          <div>${esc(t.title)}</div>
          ${t.status === "blocked" && t.last_blocker ? `<div style="color:var(--red);font-size:11.5px;margin-top:4px;">卡点：${esc(t.last_blocker)}</div>` : ""}
        </div>
      `).join("")
      : `<div style="color:var(--muted);padding:12px;">暂无任务</div>`;
    return;
  }

  // chat
  const msgs = (statusState.recent_messages || []).slice().reverse();
  body.innerHTML = msgs.length
    ? msgs.map(m => `
      <div class="term-item ${m.sender === "antigravity" ? "k-assistant" : "k-system"}">
        <div class="term-head">
          <span class="term-label">${esc(SENDER_CN[m.sender] || m.sender)} · ${esc(m.msg_type)}${m.task_key ? " #" + esc(m.task_key) : ""}</span>
          <span>${esc(m.created_at.slice(11,19))}</span>
        </div>
        <pre class="term-text">${esc(m.content)}</pre>
      </div>
    `).join("")
    : `<div style="color:var(--muted);padding:12px;">暂无交接记录</div>`;
}

async function togglePause() {
  const c = window._curChange;
  if (!c) return;
  const nextFlag = c.control_flag === "pause" ? "none" : "pause";
  await fetch("/api/control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ change_id: c.change_id, flag: nextFlag, note: c.user_note || "" })
  });
  toast(nextFlag === "pause" ? "已暂停下一步派发" : "已恢复继续");
  refreshAll();
}

async function saveNote() {
  const c = window._curChange;
  const input = document.getElementById("noteInput");
  const note = input.value.trim();
  if (!c || !note) return;
  await fetch("/api/control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ change_id: c.change_id, flag: c.control_flag || "none", note })
  });
  input.value = "";
  toast("已发送叮嘱，下次派发自动带给 Codex");
  refreshAll();
}

refreshAll();
setInterval(refreshAll, 1500);
</script>
</body>
</html>
"""


def make_handler(db_path: Path):
    project_fallback = str(db_path.parent.parent)

    class ConsoleHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            return json.loads(raw.decode("utf-8"))

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                body = HTML_PAGE.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if parsed.path == "/api/status":
                qs = parse_qs(parsed.query)
                change_id = (qs.get("change") or [None])[0] or None
                limit = int((qs.get("limit") or ["40"])[0])
                with db.connect(db_path) as conn:
                    data = db.fetch_status(conn, change_id=change_id, recent_messages=limit)
                    all_changes = [
                        dict(r)
                        for r in conn.execute("SELECT * FROM changes ORDER BY updated_at DESC").fetchall()
                    ]
                data["all_changes"] = all_changes
                data["db_path"] = str(db_path)
                self._send_json(data)
                return

            if parsed.path == "/api/live":
                qs = parse_qs(parsed.query)
                wanted_sid = (qs.get("session") or [None])[0] or None
                limit = int((qs.get("limit") or ["80"])[0])
                roots = [project_fallback]
                with db.connect(db_path) as conn:
                    for r in conn.execute("SELECT project_root, worktree_path FROM changes").fetchall():
                        if r["project_root"]:
                            roots.append(r["project_root"])
                        if r["worktree_path"]:
                            roots.append(r["worktree_path"])
                sessions = find_project_codex_sessions(roots)
                target = None
                if wanted_sid:
                    target = next((s for s in sessions if s["session_id"] == wanted_sid), None)
                if not target and sessions:
                    target = sessions[0]
                events = parse_codex_session_events(Path(target["path"]), limit=limit) if target else []
                self._send_json(
                    {
                        "sessions": sessions,
                        "selected_session_id": target["session_id"] if target else "",
                        "events": events,
                    }
                )
                return

            self._send_json({"ok": False, "error": "Not found"}, status=HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                body = self._read_json()
            except Exception as exc:
                self._send_json({"ok": False, "error": f"Invalid JSON: {exc}"}, status=HTTPStatus.BAD_REQUEST)
                return

            if parsed.path == "/api/control":
                change_id = body.get("change_id")
                flag = body.get("flag", "none")
                note = body.get("note")
                if not change_id or flag not in ("none", "pause", "abort"):
                    self._send_json({"ok": False, "error": "Invalid change_id or flag"}, status=HTTPStatus.BAD_REQUEST)
                    return
                with db.connect(db_path) as conn:
                    db.set_control(conn, change_id, flag, note)
                    db.add_message(conn, change_id, "human", "note", f"control={flag} note={note or ''}")
                self._send_json({"ok": True})
                return

            if parsed.path == "/api/decide":
                change_id = body.get("change_id")
                topic = (body.get("topic") or "").strip()
                decision = (body.get("decision") or "").strip()
                decided_by = body.get("by", "human")
                if not change_id or not topic or not decision:
                    self._send_json({"ok": False, "error": "Missing change_id/topic/decision"}, status=HTTPStatus.BAD_REQUEST)
                    return
                with db.connect(db_path) as conn:
                    did = db.add_decision(conn, change_id, topic, decision, decided_by=decided_by)
                    db.add_message(conn, change_id, decided_by, "decision", f"{topic}: {decision}")
                self._send_json({"ok": True, "decision_id": did})
                return

            self._send_json({"ok": False, "error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    return ConsoleHandler


def serve_console(db_path: Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    db.init_db(db_path)
    server = ThreadingHTTPServer((host, port), make_handler(db_path))
    url = f"http://{host}:{port}/"
    print(f"Codex Orchestrator Console running at {url} (DB: {db_path})", flush=True)
    if open_browser:
        open_mini_window(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Codex Orchestrator Mini Console")
    parser.add_argument("--project", default=".", help="Target project root directory")
    parser.add_argument("--db", default=None, help="Optional override path to SQLite DB")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    ns = parser.parse_args()
    target_db = db.resolve_db_path(Path(ns.project).expanduser().resolve(), ns.db)
    serve_console(target_db, host=ns.host, port=ns.port, open_browser=ns.open)
