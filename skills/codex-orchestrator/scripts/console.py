"""Zero-dependency visual console dashboard with live Codex CLI terminal streaming."""

from __future__ import annotations

import json
import os
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
                    events.append({"time": ts, "kind": "assistant", "label": "Codex 说明", "text": body})
            elif ptype in ("custom_tool_call", "function_call"):
                name = payload.get("name") or "tool"
                raw_in = payload.get("input") or payload.get("arguments") or ""
                if not isinstance(raw_in, str):
                    raw_in = json.dumps(raw_in, ensure_ascii=False)
                events.append(
                    {
                        "time": ts,
                        "kind": "tool_call",
                        "label": f"执行 {name}",
                        "text": raw_in.strip()[:1200],
                    }
                )
            elif ptype in ("custom_tool_call_output", "function_call_output"):
                out = payload.get("output")
                text_out = ""
                if isinstance(out, str):
                    text_out = out
                elif isinstance(out, list):
                    chunks = []
                    for item in out:
                        if isinstance(item, dict) and isinstance(item.get("text"), str):
                            chunks.append(item["text"])
                    text_out = "".join(chunks)
                text_out = text_out.strip()
                if text_out:
                    events.append(
                        {
                            "time": ts,
                            "kind": "tool_output",
                            "label": "命令输出",
                            "text": text_out[:1200],
                        }
                    )
        elif etype == "event_msg" and payload.get("type") == "task_started":
            events.append({"time": ts, "kind": "system", "label": "会话启动", "text": "Codex CLI 已开始执行新轮次"})
        elif etype == "event_msg" and payload.get("type") == "task_complete":
            events.append({"time": ts, "kind": "system", "label": "轮次完成", "text": "Codex CLI 已完成本轮执行"})

    return events[-limit:]


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Codex 任务实时看板</title>
<style>
  :root {
    --bg: #0b0f14;
    --panel: #131922;
    --panel-2: #19212c;
    --term-bg: #090c10;
    --border: #263040;
    --text: #eef2f7;
    --muted: #8b9bb0;
    --blue: #58a6ff;
    --green: #3fb950;
    --yellow: #e3b341;
    --red: #f85149;
    --purple: #bc8cff;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 14px;
    line-height: 1.55;
  }
  .topbar {
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    padding: 14px 24px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    position: sticky;
    top: 0;
    z-index: 20;
  }
  .title-wrap { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .title-wrap h1 { margin: 0; font-size: 18px; font-weight: 700; }
  .live-pill {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    background: rgba(139,155,176,0.14);
    color: var(--muted);
  }
  .live-pill.active { background: rgba(63,185,80,0.18); color: var(--green); }
  .live-pill.blocked { background: rgba(248,81,73,0.2); color: var(--red); }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
  .live-pill.active .dot { animation: pulse 1.4s infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.35; transform: scale(0.8); } }

  .controls { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  button, select, input {
    background: var(--panel-2);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 7px 12px;
    font-size: 13px;
    font-family: inherit;
  }
  button { cursor: pointer; font-weight: 600; transition: 0.15s; }
  button:hover { border-color: var(--blue); }
  button.primary { background: #1f6feb; border-color: #388bfd; color: #fff; }
  button.warn { border-color: var(--yellow); color: var(--yellow); }
  button.ok { border-color: var(--green); color: var(--green); }

  .container { max-width: 1520px; margin: 0 auto; padding: 18px 24px 36px; display: flex; flex-direction: column; gap: 16px; }

  .hero {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 20px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .hero-top { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
  .hero-summary { font-size: 15px; font-weight: 600; }
  .hero-sub { font-size: 12px; color: var(--muted); }
  .progress-track { width: 100%; height: 10px; background: #1e2736; border-radius: 999px; overflow: hidden; }
  .progress-fill { height: 100%; background: linear-gradient(90deg, #2ea043, #58a6ff); border-radius: 999px; transition: width 0.35s ease; }
  .note-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .note-row input { flex: 1; min-width: 240px; }

  .split { display: grid; grid-template-columns: 440px 1fr; gap: 16px; align-items: start; }
  @media (max-width: 1040px) { .split { grid-template-columns: 1fr; } }

  .panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
  }
  .panel-head {
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
  }
  .panel-head h2 { margin: 0; font-size: 14px; font-weight: 700; }
  .panel-body { padding: 14px 16px; }

  .timeline { display: flex; flex-direction: column; gap: 10px; }
  .step-card {
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
    background: var(--panel-2);
    border-left: 4px solid var(--border);
  }
  .step-card.st-done { border-left-color: var(--green); }
  .step-card.st-running { border-left-color: var(--blue); background: rgba(88,166,255,0.08); }
  .step-card.st-blocked { border-left-color: var(--red); background: rgba(248,81,73,0.08); }
  .step-card.st-reviewing { border-left-color: var(--purple); }
  .step-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
  .step-key { font-weight: 700; font-size: 13px; color: var(--blue); }
  .badge { padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; }
  .b-done { background: rgba(63,185,80,0.2); color: var(--green); }
  .b-running { background: rgba(88,166,255,0.22); color: var(--blue); }
  .b-pending { background: rgba(139,155,176,0.16); color: var(--muted); }
  .b-blocked { background: rgba(248,81,73,0.22); color: var(--red); }
  .b-reviewing { background: rgba(188,140,255,0.22); color: var(--purple); }
  .b-failed, .b-rolled_back { background: rgba(227,179,65,0.22); color: var(--yellow); }
  .step-title { font-size: 13px; color: var(--text); }
  .step-meta { margin-top: 6px; font-size: 11px; color: var(--muted); display: flex; gap: 10px; flex-wrap: wrap; }

  .tabs { display: flex; gap: 6px; }
  .tab {
    padding: 5px 12px;
    border-radius: 7px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid transparent;
    color: var(--muted);
    background: transparent;
  }
  .tab.active { background: #1f6feb; color: #fff; }

  .terminal {
    background: var(--term-bg);
    padding: 14px;
    height: 620px;
    overflow-y: auto;
    font-family: ui-monospace, SFMono-Regular, Consolas, "Cascadia Code", monospace;
    font-size: 12.5px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .term-item {
    border-radius: 8px;
    padding: 10px 12px;
    border: 1px solid #1e2633;
    background: #10151d;
  }
  .term-item.k-assistant { border-left: 3px solid var(--blue); background: #0f1724; }
  .term-item.k-tool_call { border-left: 3px solid var(--green); }
  .term-item.k-tool_output { border-left: 3px solid #48566a; background: #0c1017; }
  .term-item.k-system { border-left: 3px solid var(--purple); }
  .term-head { display: flex; justify-content: space-between; font-size: 11px; color: var(--muted); margin-bottom: 5px; }
  .term-label { font-weight: 700; }
  .k-assistant .term-label { color: var(--blue); }
  .k-tool_call .term-label { color: var(--green); }
  .k-system .term-label { color: var(--purple); }
  .term-text { margin: 0; white-space: pre-wrap; word-break: break-word; color: #d8e1ec; font-family: inherit; }
  details summary { cursor: pointer; color: var(--muted); font-size: 12px; }

  .chat-list { display: flex; flex-direction: column; gap: 10px; height: 620px; overflow-y: auto; padding: 14px; background: var(--term-bg); }
  .bubble { padding: 10px 12px; border-radius: 10px; border: 1px solid var(--border); background: var(--panel-2); }
  .bubble.s-antigravity { border-left: 3px solid var(--blue); }
  .bubble.s-codex { border-left: 3px solid var(--purple); }
  .bubble.s-human { border-left: 3px solid var(--green); }
  .bubble-head { display: flex; justify-content: space-between; font-size: 12px; color: var(--muted); margin-bottom: 6px; }
  .bubble pre { margin: 0; white-space: pre-wrap; word-break: break-word; font-family: inherit; font-size: 12.5px; color: #dce4ee; max-height: 220px; overflow-y: auto; }

  .dec-box { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border); }
  .dec-item { font-size: 12px; padding: 8px 10px; border-radius: 8px; background: var(--panel-2); border: 1px solid var(--border); margin-bottom: 6px; }
  .toast { position: fixed; right: 20px; bottom: 20px; background: #1f6feb; color: #fff; padding: 10px 16px; border-radius: 8px; display: none; font-weight: 600; z-index: 99; }
</style>
</head>
<body>
<header class="topbar">
  <div class="title-wrap">
    <h1>Codex 实时任务看板</h1>
    <span id="liveBadge" class="live-pill"><span class="dot"></span>检测中...</span>
    <select id="changeSelect"></select>
  </div>
  <div class="controls">
    <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:5px;">
      <input type="checkbox" id="autoRefresh" checked /> 自动刷新
    </label>
    <button onclick="refreshAll()">刷新</button>
  </div>
</header>

<div class="container">
  <section class="hero">
    <div class="hero-top">
      <div>
        <div class="hero-summary" id="heroSummary">正在加载项目进度...</div>
        <div class="hero-sub" id="heroSub"></div>
      </div>
      <div class="controls">
        <button class="warn" onclick="setControl('pause')">暂停下一步</button>
        <button class="ok" onclick="setControl('none')">恢复继续</button>
      </div>
    </div>
    <div class="progress-track"><div id="progressFill" class="progress-fill" style="width:0%"></div></div>
    <div class="note-row">
      <input type="text" id="userNoteInput" placeholder="想叮嘱 AI 一句话？写在这里点保存，下一步派发会自动带给 Codex..." />
      <button class="primary" onclick="saveNote()">保存叮嘱</button>
    </div>
  </section>

  <div class="split">
    <section class="panel">
      <div class="panel-head">
        <h2>任务进度清单</h2>
        <span id="taskRatio" class="hero-sub"></span>
      </div>
      <div class="panel-body">
        <div id="timeline" class="timeline"></div>
        <details class="dec-box">
          <summary>已锁定的技术决策（点击展开 / 新增）</summary>
          <div id="decisionsList" style="margin-top:10px;"></div>
          <div class="note-row" style="margin-top:8px;">
            <input type="text" id="decTopic" placeholder="主题" style="min-width:90px;flex:0.4;" />
            <input type="text" id="decBody" placeholder="已拍板的规则..." style="min-width:140px;" />
            <button class="primary" onclick="addDecision()">添加</button>
          </div>
        </details>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <div class="tabs">
          <button id="tabLive" class="tab active" onclick="switchTab('live')">Codex CLI 实时运行黑框</button>
          <button id="tabChat" class="tab" onclick="switchTab('chat')">Antigravity ↔ Codex 交接记录</button>
        </div>
        <select id="sessionSelect" style="max-width:260px;"></select>
      </div>
      <div id="liveView" class="terminal"></div>
      <div id="chatView" class="chat-list" style="display:none;"></div>
    </section>
  </div>
</div>

<div id="toast" class="toast"></div>

<script>
const STATUS_CN = {
  done: "已完成 ✓",
  running: "Codex 正在写代码...",
  pending: "排队中",
  blocked: "卡住等拍板",
  reviewing: "待验收",
  failed: "执行失败",
  rolled_back: "已回滚"
};
const SENDER_CN = {
  antigravity: "Antigravity（总控）",
  codex: "Codex CLI（执行）",
  gate: "自动验证门禁",
  human: "你（人工指示）"
};

let statusState = { changes: [], all_changes: [], tasks: [], decisions: [], recent_messages: [] };
let liveState = { sessions: [], selected_session_id: "", events: [] };
let selectedChange = "";
let selectedSession = "";
let activeTab = "live";

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;" }[c]));
}
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.style.display = "block";
  clearTimeout(window._t);
  window._t = setTimeout(() => el.style.display = "none", 2200);
}
function switchTab(t) {
  activeTab = t;
  document.getElementById("tabLive").classList.toggle("active", t === "live");
  document.getElementById("tabChat").classList.toggle("active", t === "chat");
  document.getElementById("liveView").style.display = t === "live" ? "flex" : "none";
  document.getElementById("chatView").style.display = t === "chat" ? "flex" : "none";
}

async function refreshAll() {
  const q1 = selectedChange ? `?change=${encodeURIComponent(selectedChange)}&limit=40` : "?limit=40";
  const q2 = selectedSession ? `?session=${encodeURIComponent(selectedSession)}&limit=80` : "?limit=80";
  const [r1, r2] = await Promise.all([fetch("/api/status" + q1), fetch("/api/live" + q2)]);
  statusState = await r1.json();
  liveState = await r2.json();
  renderAll();
}

function renderAll() {
  const allChanges = statusState.all_changes || statusState.changes || [];
  const sel = document.getElementById("changeSelect");
  sel.innerHTML = allChanges.map(c => `<option value="${esc(c.change_id)}" ${c.change_id === (selectedChange || allChanges[0]?.change_id) ? "selected" : ""}>${esc(c.change_id)}</option>`).join("") || `<option value="">暂无任务</option>`;
  sel.onchange = () => { selectedChange = sel.value; refreshAll(); };

  const curChange = allChanges.find(c => c.change_id === selectedChange) || allChanges[0];
  window._curChange = curChange;

  const tasks = statusState.tasks || [];
  const doneCount = tasks.filter(t => t.status === "done").length;
  const runningTask = tasks.find(t => t.status === "running");
  const blockedTask = tasks.find(t => t.status === "blocked");
  const pct = tasks.length ? Math.round((doneCount / tasks.length) * 100) : 0;

  document.getElementById("progressFill").style.width = pct + "%";
  document.getElementById("taskRatio").textContent = `${doneCount} / ${tasks.length}（${pct}%）`;

  const anyLive = (liveState.sessions || []).some(s => s.is_active);
  const badge = document.getElementById("liveBadge");
  if (blockedTask) {
    badge.className = "live-pill blocked";
    badge.innerHTML = `<span class="dot"></span>任务 ${esc(blockedTask.task_key)} 卡住等待拍板`;
  } else if (anyLive || runningTask) {
    badge.className = "live-pill active";
    badge.innerHTML = `<span class="dot"></span>Codex CLI 正在实时运行中`;
  } else {
    badge.className = "live-pill";
    badge.innerHTML = `<span class="dot"></span>当前空闲`;
  }

  const runningText = runningTask
    ? `当前正在执行：任务 ${runningTask.task_key}`
    : (doneCount === tasks.length && tasks.length > 0 ? "全部任务已完成！" : "等待下一步派发");
  const pauseTag = curChange && curChange.control_flag !== "none" ? ` · [已设状态: ${curChange.control_flag}]` : "";
  document.getElementById("heroSummary").textContent = `${curChange ? curChange.change_id : "未初始化"} — 已完成 ${doneCount}/${tasks.length}（${pct}%）· ${runningText}${pauseTag}`;
  document.getElementById("heroSub").textContent = curChange ? `项目目录：${curChange.project_root}` : "";

  const noteInput = document.getElementById("userNoteInput");
  if (curChange && document.activeElement !== noteInput) {
    noteInput.value = curChange.user_note || "";
  }

  // Timeline
  const tl = document.getElementById("timeline");
  if (!tasks.length) {
    tl.innerHTML = `<div class="hero-sub">还没有同步任务节点。</div>`;
  } else {
    tl.innerHTML = tasks.map(t => `
      <div class="step-card st-${esc(t.status)}">
        <div class="step-top">
          <span class="step-key">任务 ${esc(t.task_key)}</span>
          <span class="badge b-${esc(t.status)}">${esc(STATUS_CN[t.status] || t.status)}</span>
        </div>
        <div class="step-title">${esc(t.title)}</div>
        <div class="step-meta">
          ${t.result_commit ? `<span>已提交 ${esc(t.result_commit.slice(0,7))}</span>` : ""}
          ${t.retry_count > 1 ? `<span>重试 ${t.retry_count} 次</span>` : ""}
          ${t.status === "blocked" && t.last_blocker ? `<span style="color:var(--red)">卡点：${esc(t.last_blocker)}</span>` : ""}
        </div>
      </div>
    `).join("");
  }

  // Decisions
  const decs = statusState.decisions || [];
  document.getElementById("decisionsList").innerHTML = decs.length
    ? decs.map(d => `<div class="dec-item"><strong>${esc(d.topic)}</strong>：${esc(d.decision)}</div>`).join("")
    : `<div class="hero-sub">暂无决策记录</div>`;

  // Session dropdown
  const ssel = document.getElementById("sessionSelect");
  const sessions = liveState.sessions || [];
  ssel.innerHTML = sessions.length
    ? sessions.map((s, idx) => {
        const tag = s.is_active ? "🟢 运行中" : `${s.age_sec}秒前`;
        const sid = s.session_id;
        return `<option value="${esc(sid)}" ${sid === liveState.selected_session_id ? "selected" : ""}>会话 #${sessions.length - idx} (${tag}) · ${esc(sid.slice(0,8))}</option>`;
      }).join("")
    : `<option value="">未检测到 Codex CLI 会话</option>`;
  ssel.onchange = () => { selectedSession = ssel.value; refreshAll(); };

  // Live terminal
  const lv = document.getElementById("liveView");
  const wasAtBottom = lv.scrollHeight - lv.scrollTop - lv.clientHeight < 60;
  const evts = liveState.events || [];
  if (!evts.length) {
    lv.innerHTML = `<div style="color:var(--muted);padding:12px;">等待 Codex CLI 输出...（只要该项目有 Codex CLI 在运行，这里会实时滚屏显示每一步操作）</div>`;
  } else {
    lv.innerHTML = evts.map(e => {
      if (e.kind === "tool_output") {
        return `<div class="term-item k-tool_output">
          <div class="term-head"><span class="term-label">${esc(e.label)}</span><span>${esc(e.time)}</span></div>
          <details><summary>点击展开命令输出（${e.text.length} 字符）</summary><pre class="term-text" style="margin-top:6px;">${esc(e.text)}</pre></details>
        </div>`;
      }
      return `<div class="term-item k-${esc(e.kind)}">
        <div class="term-head"><span class="term-label">${esc(e.label)}</span><span>${esc(e.time)}</span></div>
        <pre class="term-text">${esc(e.text)}</pre>
      </div>`;
    }).join("");
    if (wasAtBottom) lv.scrollTop = lv.scrollHeight;
  }

  // Chat log
  const msgs = (statusState.recent_messages || []).slice().reverse();
  document.getElementById("chatView").innerHTML = msgs.length
    ? msgs.map(m => `
      <div class="bubble s-${esc(m.sender)}">
        <div class="bubble-head">
          <strong>${esc(SENDER_CN[m.sender] || m.sender)} · ${esc(m.msg_type)}${m.task_key ? " (任务 " + esc(m.task_key) + ")" : ""}</strong>
          <span>${esc(m.created_at)}</span>
        </div>
        <pre>${esc(m.content)}</pre>
      </div>
    `).join("")
    : `<div style="color:var(--muted);padding:12px;">暂无交接记录</div>`;
}

async function setControl(flag) {
  const c = window._curChange;
  if (!c) return;
  const note = document.getElementById("userNoteInput").value;
  await fetch("/api/control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ change_id: c.change_id, flag, note })
  });
  toast(flag === "pause" ? "已暂停下一步派发" : "已恢复正常调度");
  refreshAll();
}
async function saveNote() {
  const c = window._curChange;
  if (!c) return;
  await setControl(c.control_flag || "none");
  toast("叮嘱已保存，下次派发自动生效");
}
async function addDecision() {
  const c = window._curChange;
  if (!c) return;
  const topic = document.getElementById("decTopic").value.trim();
  const decision = document.getElementById("decBody").value.trim();
  if (!topic || !decision) return;
  await fetch("/api/decide", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ change_id: c.change_id, topic, decision, by: "human" })
  });
  document.getElementById("decTopic").value = "";
  document.getElementById("decBody").value = "";
  toast("已添加锁定决策");
  refreshAll();
}

refreshAll();
setInterval(() => {
  if (document.getElementById("autoRefresh").checked) refreshAll();
}, 1500);
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
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Codex Orchestrator Local Console")
    parser.add_argument("--project", default=".", help="Target project root directory")
    parser.add_argument("--db", default=None, help="Optional override path to SQLite DB")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    ns = parser.parse_args()
    target_db = db.resolve_db_path(Path(ns.project).expanduser().resolve(), ns.db)
    serve_console(target_db, host=ns.host, port=ns.port, open_browser=ns.open)
