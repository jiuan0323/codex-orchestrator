"""Zero-dependency local web console dashboard for Codex Orchestrator SQLite WAL DB."""

from __future__ import annotations

import json
import sys
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Codex Orchestrator Console</title>
<style>
  :root {
    --bg: #0d1117;
    --panel: #161b22;
    --panel-2: #1c222b;
    --border: #30363d;
    --text: #e6edf3;
    --muted: #8b949e;
    --accent: #58a6ff;
    --green: #3fb950;
    --yellow: #d29922;
    --red: #f85149;
    --purple: #bc8cff;
    --cyan: #39c5cf;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: var(--bg);
    color: var(--text);
    font-size: 14px;
    line-height: 1.5;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 24px;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    z-index: 10;
  }
  .brand { display: flex; align-items: center; gap: 12px; }
  .brand h1 { margin: 0; font-size: 17px; font-weight: 600; }
  .db-path { font-size: 12px; color: var(--muted); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
  .toolbar { display: flex; align-items: center; gap: 10px; }
  button, select, input, textarea {
    background: var(--panel-2);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
    font-family: inherit;
  }
  button { cursor: pointer; font-weight: 500; transition: border-color 0.15s, background 0.15s; }
  button:hover { border-color: var(--accent); background: #222933; }
  button.primary { background: #1f6feb; border-color: #388bfd; color: #fff; }
  button.primary:hover { background: #388bfd; }
  button.warn { border-color: var(--yellow); color: var(--yellow); }
  button.danger { border-color: var(--red); color: var(--red); }
  button.success { border-color: var(--green); color: var(--green); }
  main { max-width: 1440px; margin: 0 auto; padding: 20px 24px 48px; display: flex; flex-direction: column; gap: 18px; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
  .kpi {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 16px;
  }
  .kpi .label { color: var(--muted); font-size: 12px; }
  .kpi .val { font-size: 24px; font-weight: 700; margin-top: 4px; }
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
    background: rgba(255,255,255,0.015);
    gap: 12px;
    flex-wrap: wrap;
  }
  .card-header h2 { margin: 0; font-size: 14px; font-weight: 600; }
  .card-body { padding: 14px 16px; }
  .control-bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
  .control-bar input[type="text"] { flex: 1; min-width: 240px; }
  .meta-line { display: flex; flex-wrap: wrap; gap: 16px; font-size: 12px; color: var(--muted); margin-bottom: 12px; }
  .mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
  th { color: var(--muted); font-weight: 600; font-size: 12px; background: rgba(255,255,255,0.01); }
  tr:last-child td { border-bottom: none; }
  .pill {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  .st-pending { background: rgba(139,148,158,0.18); color: var(--muted); }
  .st-running { background: rgba(88,166,255,0.2); color: var(--accent); }
  .st-blocked { background: rgba(248,81,73,0.22); color: var(--red); }
  .st-reviewing { background: rgba(188,140,255,0.22); color: var(--purple); }
  .st-done { background: rgba(63,185,80,0.22); color: var(--green); }
  .st-failed, .st-rolled_back { background: rgba(210,153,34,0.22); color: var(--yellow); }
  .st-none { background: rgba(63,185,80,0.16); color: var(--green); }
  .st-pause { background: rgba(210,153,34,0.22); color: var(--yellow); }
  .st-abort { background: rgba(248,81,73,0.22); color: var(--red); }
  .grid-2 { display: grid; grid-template-columns: 1fr 1.4fr; gap: 18px; }
  @media (max-width: 980px) { .grid-2 { grid-template-columns: 1fr; } }
  .decision-item {
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--panel-2);
    margin-bottom: 8px;
  }
  .decision-item .top { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; color: var(--muted); }
  .decision-item .topic { color: var(--accent); font-weight: 600; }
  .decision-item .body { margin-top: 4px; }
  .msg-list { display: flex; flex-direction: column; gap: 10px; max-height: 540px; overflow-y: auto; }
  .msg {
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--panel-2);
    padding: 10px 12px;
  }
  .msg-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; font-size: 12px; color: var(--muted); }
  .sender-antigravity { color: var(--accent); font-weight: 600; }
  .sender-codex { color: var(--purple); font-weight: 600; }
  .sender-gate { color: var(--cyan); font-weight: 600; }
  .sender-human { color: var(--green); font-weight: 600; }
  pre.msg-body {
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 12px;
    color: #c9d1d9;
    max-height: 220px;
    overflow-y: auto;
  }
  .empty { color: var(--muted); font-style: italic; padding: 12px 0; }
  .form-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
  .form-row input { flex: 1; min-width: 140px; }
  .toast {
    position: fixed;
    right: 20px;
    bottom: 20px;
    background: #1f6feb;
    color: #fff;
    padding: 10px 14px;
    border-radius: 6px;
    font-size: 13px;
    display: none;
    box-shadow: 0 6px 20px rgba(0,0,0,0.4);
  }
</style>
</head>
<body>
<header>
  <div class="brand">
    <h1>Codex Orchestrator Console</h1>
    <span id="dbPath" class="db-path">loading...</span>
  </div>
  <div class="toolbar">
    <select id="changeSelect"></select>
    <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted);">
      <input type="checkbox" id="autoRefresh" checked /> 自动刷新 (2s)
    </label>
    <button onclick="loadStatus()">立即刷新</button>
  </div>
</header>

<main>
  <section class="kpis" id="kpis"></section>

  <section class="card" id="controlCard" style="display:none;">
    <div class="card-header">
      <h2>变更控制面（Human-in-the-loop）</h2>
      <span id="controlBadge"></span>
    </div>
    <div class="card-body">
      <div class="meta-line" id="changeMeta"></div>
      <div class="control-bar">
        <button class="warn" onclick="setControl('pause')">暂停派发 (Pause)</button>
        <button class="success" onclick="setControl('none')">恢复正常 (Clear)</button>
        <button class="danger" onclick="setControl('abort')">中止变更 (Abort)</button>
        <input type="text" id="userNoteInput" placeholder="给 Antigravity 留一句补充指示（下次派发自动注入 Codex）..." />
        <button class="primary" onclick="saveNote()">保存指示</button>
      </div>
    </div>
  </section>

  <section class="card">
    <div class="card-header">
      <h2>OpenSpec 任务节点 &amp; Codex 会话绑定</h2>
      <span class="db-path" id="taskCount"></span>
    </div>
    <div class="card-body" style="padding:0;overflow-x:auto;">
      <table>
        <thead>
          <tr>
            <th>Change / Task</th>
            <th>状态</th>
            <th>Codex Session ID</th>
            <th>Git 锚点 (base → result)</th>
            <th>阻塞 / 重试</th>
            <th>最近阻塞信息</th>
          </tr>
        </thead>
        <tbody id="tasksBody"></tbody>
      </table>
    </div>
  </section>

  <section class="grid-2">
    <div class="card">
      <div class="card-header">
        <h2>已锁定决策 (Decisions)</h2>
      </div>
      <div class="card-body">
        <div id="decisionsList"></div>
        <div class="form-row">
          <input type="text" id="decTopic" placeholder="主题 (如: error-handling)" />
          <input type="text" id="decBody" placeholder="已拍板的决策内容..." />
          <button class="primary" onclick="addDecision()">注入决策</button>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <h2>沟通记录中间件 (Messages)</h2>
        <select id="senderFilter" onchange="renderMessages()">
          <option value="all">全部来源</option>
          <option value="antigravity">Antigravity</option>
          <option value="codex">Codex</option>
          <option value="gate">Hard Gate</option>
          <option value="human">Human</option>
        </select>
      </div>
      <div class="card-body">
        <div class="msg-list" id="messagesList"></div>
      </div>
    </div>
  </section>
</main>

<div id="toast" class="toast"></div>

<script>
let state = { changes: [], tasks: [], decisions: [], recent_messages: [], db_path: "" };
let selectedChange = "";

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;" }[c]));
}
function shortHash(h) { return h ? h.slice(0, 7) : "-"; }
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.style.display = "block";
  clearTimeout(window._toastTimer);
  window._toastTimer = setTimeout(() => { el.style.display = "none"; }, 2200);
}

async function loadStatus() {
  const q = selectedChange ? `?change=${encodeURIComponent(selectedChange)}&limit=50` : "?limit=50";
  const res = await fetch("/api/status" + q);
  state = await res.json();
  document.getElementById("dbPath").textContent = state.db_path || "";
  renderChangeSelect();
  renderKPIs();
  renderControl();
  renderTasks();
  renderDecisions();
  renderMessages();
}

function renderChangeSelect() {
  const sel = document.getElementById("changeSelect");
  const allChanges = state.all_changes || state.changes || [];
  const opts = [`<option value="">全部 Changes (${allChanges.length})</option>`]
    .concat(allChanges.map(c => `<option value="${esc(c.change_id)}" ${c.change_id === selectedChange ? "selected" : ""}>${esc(c.change_id)} [${esc(c.control_flag)}]</option>`));
  sel.innerHTML = opts.join("");
  sel.onchange = () => { selectedChange = sel.value; loadStatus(); };
}

function renderKPIs() {
  const tasks = state.tasks || [];
  const cnt = (st) => tasks.filter(t => t.status === st).length;
  const items = [
    ["Changes", (state.all_changes || state.changes || []).length, "var(--text)"],
    ["进行中 / 待审", cnt("running") + cnt("reviewing"), "var(--accent)"],
    ["阻塞 (Blocked)", cnt("blocked"), cnt("blocked") > 0 ? "var(--red)" : "var(--muted)"],
    ["已完成 (Done)", cnt("done"), "var(--green)"],
    ["待执行 (Pending)", cnt("pending"), "var(--muted)"],
    ["已锁定决策", (state.decisions || []).length, "var(--purple)"],
  ];
  document.getElementById("kpis").innerHTML = items.map(([l, v, c]) =>
    `<div class="kpi"><div class="label">${l}</div><div class="val" style="color:${c}">${v}</div></div>`
  ).join("");
}

function renderControl() {
  const card = document.getElementById("controlCard");
  const current = (state.changes || []).find(c => c.change_id === selectedChange) || (state.changes && state.changes.length === 1 ? state.changes[0] : null);
  if (!current) { card.style.display = "none"; return; }
  card.style.display = "block";
  window._activeChange = current;
  document.getElementById("controlBadge").innerHTML = `<span class="pill st-${esc(current.control_flag)}">control: ${esc(current.control_flag)}</span>`;
  document.getElementById("changeMeta").innerHTML = `
    <span>Change: <strong style="color:var(--text)">${esc(current.change_id)}</strong></span>
    <span>Branch: <span class="mono">${esc(current.branch || "-")}</span></span>
    <span>Worktree: <span class="mono">${esc(current.worktree_path || current.project_root)}</span></span>
  `;
  const input = document.getElementById("userNoteInput");
  if (document.activeElement !== input) {
    input.value = current.user_note || "";
  }
}

function renderTasks() {
  const tasks = state.tasks || [];
  document.getElementById("taskCount").textContent = `${tasks.length} 个任务节点`;
  const body = document.getElementById("tasksBody");
  if (!tasks.length) {
    body.innerHTML = `<tr><td colspan="6" class="empty" style="padding:16px;">暂无任务节点。使用 orchestrate.py sync-tasks 同步 OpenSpec tasks.md 后将显示在这里。</td></tr>`;
    return;
  }
  body.innerHTML = tasks.map(t => {
    const breaker = t.block_count >= 2 ? ` <span class="pill st-blocked">熔断</span>` : "";
    return `<tr>
      <td><div class="mono" style="color:var(--muted)">${esc(t.change_id)} / <strong style="color:var(--text)">${esc(t.task_key)}</strong></div><div>${esc(t.title)}</div></td>
      <td><span class="pill st-${esc(t.status)}">${esc(t.status)}</span></td>
      <td class="mono">${t.codex_session_id ? esc(t.codex_session_id) : "<span style='color:var(--muted)'>未绑定</span>"}</td>
      <td class="mono">${shortHash(t.base_commit)} → ${shortHash(t.result_commit)}</td>
      <td>${t.block_count} / ${t.retry_count}${breaker}</td>
      <td style="max-width:300px;word-break:break-word;color:var(--muted);font-size:12px;">${esc(t.last_blocker || "-")}</td>
    </tr>`;
  }).join("");
}

function renderDecisions() {
  const list = state.decisions || [];
  const el = document.getElementById("decisionsList");
  if (!list.length) {
    el.innerHTML = `<div class="empty">暂无已锁定决策</div>`;
    return;
  }
  el.innerHTML = list.map(d => `
    <div class="decision-item">
      <div class="top">
        <span class="topic">${esc(d.topic)}</span>
        <span>${esc(d.decided_by)} · ${esc(d.change_id)}${d.task_key ? "/" + esc(d.task_key) : ""} · ${esc(d.created_at)}</span>
      </div>
      <div class="body">${esc(d.decision)}</div>
    </div>
  `).join("");
}

function renderMessages() {
  const filter = document.getElementById("senderFilter").value;
  const msgs = (state.recent_messages || []).filter(m => filter === "all" || m.sender === filter).slice().reverse();
  const el = document.getElementById("messagesList");
  if (!msgs.length) {
    el.innerHTML = `<div class="empty">暂无沟通日志</div>`;
    return;
  }
  el.innerHTML = msgs.map(m => `
    <div class="msg">
      <div class="msg-head">
        <div>
          <span class="sender-${esc(m.sender)}">${esc(m.sender)}</span>
          <span class="pill st-pending" style="margin-left:6px;">${esc(m.msg_type)}</span>
          <span class="mono" style="margin-left:6px;">${esc(m.change_id)}${m.task_key ? "/" + esc(m.task_key) : ""}</span>
        </div>
        <span>${esc(m.created_at)}</span>
      </div>
      <pre class="msg-body">${esc(m.content)}</pre>
    </div>
  `).join("");
}

async function setControl(flag) {
  const c = window._activeChange;
  if (!c) return;
  const note = document.getElementById("userNoteInput").value;
  await fetch("/api/control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ change_id: c.change_id, flag, note })
  });
  toast(`已设置 control_flag = ${flag}`);
  loadStatus();
}

async function saveNote() {
  const c = window._activeChange;
  if (!c) return;
  await setControl(c.control_flag || "none");
}

async function addDecision() {
  const c = window._activeChange || (state.changes && state.changes[0]);
  if (!c) { toast("请先同步至少一个 OpenSpec change"); return; }
  const topic = document.getElementById("decTopic").value.trim();
  const decision = document.getElementById("decBody").value.trim();
  if (!topic || !decision) { toast("请填写主题和决策内容"); return; }
  await fetch("/api/decide", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ change_id: c.change_id, topic, decision, by: "human" })
  });
  document.getElementById("decTopic").value = "";
  document.getElementById("decBody").value = "";
  toast("决策已写入 SQLite");
  loadStatus();
}

loadStatus();
setInterval(() => {
  if (document.getElementById("autoRefresh").checked) loadStatus();
}, 2000);
</script>
</body>
</html>
"""


def make_handler(db_path: Path):
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
                limit = int((qs.get("limit") or ["30"])[0])
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
