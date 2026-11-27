"""Zero-dependency Python stdio MCP App server for Codex Orchestrator (SEP-1865)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

SERVER_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SERVER_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "codex-orchestrator" / "scripts"
APP_HTML_PATH = SERVER_DIR / "app.html"
APP_URI = "ui://codex-orchestrator/app.html"

sys.path.insert(0, str(SCRIPTS_DIR))
import console  # noqa: E402
import db  # noqa: E402

# Escaped newlines before patch lines are separators; backslashes in paths are literal.
PATCH_FILE_RE = re.compile(
    r"\*\*\*[ \t]+(Add|Update|Delete)[ \t]+File:[ \t]+(.+?)"
    r'(?=\r?$|\\(?:r\\)?n(?=[ +@*"-]|\\(?:r\\)?n|$))',
    re.MULTILINE,
)
WALL_TIME_RE = re.compile(r"Wall time\s+([0-9.]+)\s*seconds?", re.IGNORECASE)
EXIT_CODE_RE = re.compile(r'"exit_code"\s*:\s*(-?\d+)|exit\s*code[:\s]+(-?\d+)', re.IGNORECASE)
SIDEBAR_HEX_ID_RE = re.compile(
    r"\b(?:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9a-f]{8,})\b",
    re.IGNORECASE | re.ASCII,
)


def _run_git(project_root: Path, *args: str, timeout: float = 5.0) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return proc.stdout if proc.returncode == 0 else ""
    except Exception:
        return ""


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _short_time(ts: str) -> str:
    dt = _parse_iso(ts)
    if not dt:
        return ts[:16]
    local_dt = dt.astimezone()
    return local_dt.strftime("%m-%d %H:%M")


def discover_projects() -> list[dict[str, Any]]:
    candidates: dict[str, Path] = {}

    def _add(p: Optional[Path]) -> None:
        if not p:
            return
        try:
            rp = p.expanduser().resolve()
        except Exception:
            return
        if rp.is_dir() and (rp / ".codex-orchestrator" / "state.db").exists():
            candidates[str(rp).lower()] = rp

    scratch_root = Path.home() / ".gemini" / "antigravity" / "scratch"
    if scratch_root.is_dir():
        for child in scratch_root.iterdir():
            if child.is_dir():
                _add(child)

    for sess in console.find_project_codex_sessions([], max_scan=40):
        if sess.get("cwd"):
            _add(Path(sess["cwd"]))

    items: list[dict[str, Any]] = []
    for rp in candidates.values():
        db_file = rp / ".codex-orchestrator" / "state.db"
        try:
            mtime = db_file.stat().st_mtime
        except OSError:
            mtime = 0.0
        items.append({"name": rp.name, "path": str(rp), "mtime": mtime})

    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def resolve_project(requested: Optional[str]) -> Optional[Path]:
    if requested:
        p = Path(requested).expanduser().resolve()
        if p.is_dir():
            return p
    projects = discover_projects()
    if projects:
        return Path(projects[0]["path"])
    return None


def _extract_cmd_preview(name: str, raw_in: str) -> str:
    if name == "exec_command":
        try:
            args = json.loads(raw_in)
        except json.JSONDecodeError:
            args = None
        if isinstance(args, dict) and isinstance(args.get("cmd"), str):
            return args["cmd"]
    elif name == "exec":
        match = re.search(
            r"""(?:\bcmd|"cmd"|'cmd')\s*:\s*(["'`])((?:\\[\s\S]|(?!\1)[^\\])*)\1""",
            raw_in,
        )
        if match:
            quote, command = match.groups()
            return re.sub(r"\\([" + quote + r"\\])", r"\1", command)
    return raw_in.splitlines()[0][:140] if raw_in else name


def parse_session_detail(path: Path) -> dict[str, Any]:
    """Parse a Codex CLI rollout JSONL into structured multi-round execution details."""
    total_input = 0
    total_cached = 0
    total_output = 0
    total_reasoning = 0
    model_name = ""
    effort = ""
    turns = 0
    first_ts = ""
    last_ts = ""

    raw_rounds: list[dict[str, Any]] = []

    def new_round(started_ts: str = "") -> dict[str, Any]:
        return {
            "prompt": "",
            "commentaries": [],
            "final_summary": "",
            "commands": [],
            "files_changed": [],
            "_seen_files": set(),
            "_call_index": {},
            "is_complete": False,
            "first_ts": started_ts,
            "last_ts": started_ts,
        }

    cur: Optional[dict[str, Any]] = None

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []

    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = obj.get("timestamp") or ""
        if ts:
            if not first_ts:
                first_ts = ts
            last_ts = ts
        etype = obj.get("type")
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue

        if etype == "turn_context":
            if payload.get("model"):
                model_name = str(payload["model"])
            if payload.get("effort"):
                effort = str(payload["effort"])
        elif etype == "token_usage_record":
            usage = payload.get("usage")
            if isinstance(usage, dict):
                turns += 1
                total_input += int(usage.get("input_tokens") or 0)
                total_cached += int(usage.get("cached_input_tokens") or 0)
                total_output += int(usage.get("output_tokens") or 0)
                total_reasoning += int(usage.get("reasoning_output_tokens") or 0)
        elif etype == "event_msg" and payload.get("type") == "task_started":
            cur = new_round(ts)
            raw_rounds.append(cur)
        elif etype == "event_msg" and payload.get("type") == "task_complete":
            if cur is not None:
                cur["is_complete"] = True
                if ts:
                    cur["last_ts"] = ts
        elif etype == "response_item":
            if cur is None:
                cur = new_round(ts)
                raw_rounds.append(cur)
            if ts:
                if not cur["first_ts"]:
                    cur["first_ts"] = ts
                cur["last_ts"] = ts
            ptype = payload.get("type")
            if ptype == "message":
                role = payload.get("role")
                parts = payload.get("content") or []
                texts = [
                    p.get("text", "").strip()
                    for p in parts
                    if isinstance(p, dict) and p.get("type") in ("output_text", "input_text", "text") and p.get("text")
                ]
                body = "\n".join(texts).strip()
                if not body:
                    continue
                if role == "user" and not cur["prompt"] and "AGENTS.md instructions" not in body and "<environment_context>" not in body:
                    cur["prompt"] = body
                elif role == "assistant":
                    phase = payload.get("phase") or ""
                    if phase == "commentary":
                        cur["commentaries"].append(body)
                    else:
                        cur["final_summary"] = body
            elif ptype in ("custom_tool_call", "function_call"):
                name = str(payload.get("name") or "tool")
                call_id = str(payload.get("call_id") or "")
                raw_in = payload.get("input") or payload.get("arguments") or ""
                if not isinstance(raw_in, str):
                    raw_in = json.dumps(raw_in, ensure_ascii=False)
                for action, fpath in PATCH_FILE_RE.findall(raw_in):
                    fpath = fpath.strip()
                    if fpath not in cur["_seen_files"]:
                        cur["_seen_files"].add(fpath)
                        action_cn = {"Add": "新增", "Update": "修改", "Delete": "删除"}.get(action, "修改")
                        cur["files_changed"].append({"action": action_cn, "path": fpath})
                cmd_item = {
                    "call_id": call_id,
                    "tool": name,
                    "cmd": _extract_cmd_preview(name, raw_in),
                    "status": "running",
                    "duration": "",
                    "output": "",
                }
                cur["commands"].append(cmd_item)
                if call_id:
                    cur["_call_index"][call_id] = cmd_item
            elif ptype in ("custom_tool_call_output", "function_call_output"):
                call_id = str(payload.get("call_id") or "")
                out = payload.get("output")
                text_out = ""
                if isinstance(out, str):
                    text_out = out
                elif isinstance(out, list):
                    text_out = "".join(
                        item["text"] for item in out if isinstance(item, dict) and isinstance(item.get("text"), str)
                    )
                if call_id in cur["_call_index"]:
                    item = cur["_call_index"][call_id]
                    item["output"] = text_out.strip()[:2000]
                    wt = WALL_TIME_RE.search(text_out)
                    if wt:
                        item["duration"] = f"{float(wt.group(1)):.1f}s"
                    ec = EXIT_CODE_RE.search(text_out)
                    code = int(ec.group(1) or ec.group(2)) if ec else 0
                    item["status"] = "pass" if code == 0 else "fail"

    formatted_rounds: list[dict[str, Any]] = []
    for idx, rr in enumerate(raw_rounds, start=1):
        if not rr["prompt"] and not rr["commands"] and not rr["final_summary"] and not rr["commentaries"]:
            continue
        t0, t1 = _parse_iso(rr["first_ts"]), _parse_iso(rr["last_ts"])
        dur = max(0, int((t1 - t0).total_seconds())) if (t0 and t1) else 0
        summary = rr["final_summary"] or (rr["commentaries"][-1] if rr["commentaries"] else "")
        formatted_rounds.append(
            {
                "round_no": idx,
                "prompt": rr["prompt"],
                "commentary": rr["commentaries"][-1] if rr["commentaries"] else "",
                "final_summary": summary,
                "commands": rr["commands"][-12:],
                "files_changed": rr["files_changed"][:20],
                "is_complete": rr["is_complete"],
                "duration_sec": dur,
                "started_label": _short_time(rr["first_ts"]),
            }
        )

    cache_hit_pct = round((total_cached / total_input) * 100.0, 1) if total_input > 0 else 0.0
    return {
        "rounds": formatted_rounds,
        "started_label": _short_time(first_ts),
        "telemetry": {
            "model": model_name or "gpt-6.2-sol",
            "effort": effort or "high",
            "turns": max(1, turns),
            "input_tokens": total_input,
            "cached_input_tokens": total_cached,
            "cache_hit_pct": cache_hit_pct,
            "output_tokens": total_output,
            "reasoning_output_tokens": total_reasoning,
        },
    }


def _extract_task_brief(prompt: str) -> str:
    """Pull out the human-readable Execution Instructions from Antigravity's dispatch template."""
    m = re.search(r"## Execution Instructions\s*\n(.+?)(?:\n## |\Z)", prompt, re.DOTALL)
    if m:
        return m.group(1).strip()
    return prompt.strip()


def _sidebar_title(text: str) -> str:
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:#{1,6}|[-*+])\s+", "", line)
        line = SIDEBAR_HEX_ID_RE.sub("", line.replace("`", "").replace("**", ""))
        title = " ".join(line.split()).strip(" :：·-")
        if title:
            return title
    return ""


def _sidebar_session_title(path: Path) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as rollout:
            for line in rollout:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "response_item":
                    continue
                payload = entry["payload"]
                if payload.get("type") != "message" or payload.get("role") != "user":
                    continue
                for part in payload["content"]:
                    prompt = part.get("text", "")
                    if "AGENTS.md instructions" in prompt or "<environment_context>" in prompt:
                        continue
                    title = _sidebar_title(_extract_task_brief(prompt))
                    if title:
                        return title
    except OSError:
        pass
    return "本地会话"


def build_state_snapshot(
    project_arg: Optional[str] = None,
    session_arg: Optional[str] = None,
) -> dict[str, Any]:
    projects = discover_projects()
    sidebar_sessions: dict[str, tuple[tuple[bool, int], Path, dict[str, Any], Optional[dict[str, Any]]]] = {}

    for proj in projects:
        p_path = Path(proj["path"])
        p_sessions = console.find_project_codex_sessions([str(p_path)], max_scan=30)
        db_path = db.resolve_db_path(p_path)
        task_by_sid: dict[str, dict[str, Any]] = {}
        if db_path.exists():
            db.init_db(db_path)
            with db.connect(db_path) as conn:
                for r in conn.execute("SELECT * FROM tasks").fetchall():
                    if r["codex_session_id"]:
                        task_by_sid[r["codex_session_id"]] = dict(r)
        for s in p_sessions:
            sid = s["session_id"]
            t_row = task_by_sid.get(sid)
            priority = (t_row is not None, len(p_path.parts))
            if sid not in sidebar_sessions or priority > sidebar_sessions[sid][0]:
                sidebar_sessions[sid] = (priority, p_path, s, t_row)

    items_by_project: dict[str, list[dict[str, Any]]] = {proj["path"]: [] for proj in projects}
    for _, p_path, s, t_row in sorted(sidebar_sessions.values(), key=lambda item: item[2]["mtime"], reverse=True):
        items = items_by_project[str(p_path)]
        if len(items) == 8:
            continue
        title = _sidebar_title(t_row["title"]) if t_row else ""
        items.append(
            {
                "session_id": s["session_id"],
                "title": title or _sidebar_session_title(Path(s["path"])),
                "task_key": t_row["task_key"] if t_row else "",
                "status": t_row["status"] if t_row else ("running" if s["is_active"] else "done"),
                "is_active": s["is_active"],
                "started_label": _short_time(s["started_at"]),
                "project_path": str(p_path),
            }
        )
    sidebar_projects = [
        {
            "name": proj["name"],
            "path": proj["path"],
            "count": len(items_by_project[proj["path"]]),
            "sessions": items_by_project[proj["path"]],
        }
        for proj in projects
    ]

    target = resolve_project(project_arg)
    if not target:
        return {
            "sidebar_projects": sidebar_projects,
            "active_project": "",
            "project_name": "",
            "change": None,
            "selected_session_id": "",
            "rounds": [],
            "telemetry": {},
            "tasks": [],
        }

    db_path = db.resolve_db_path(target)
    tasks: list[dict[str, Any]] = []
    change_row: Optional[dict[str, Any]] = None
    messages: list[dict[str, Any]] = []
    roots = [str(target)]

    if db_path.exists():
        db.init_db(db_path)
        with db.connect(db_path) as conn:
            st = db.fetch_status(conn, change_id=None, recent_messages=60)
            tasks = st.get("tasks", [])
            changes = st.get("changes", [])
            change_row = changes[0] if changes else None
            messages = st.get("recent_messages", [])
            for c in changes:
                if c.get("worktree_path"):
                    roots.append(c["worktree_path"])
            for t in tasks:
                if t.get("worktree_path"):
                    roots.append(t["worktree_path"])

    sessions = console.find_project_codex_sessions(roots, max_scan=40)
    sel = next((s for s in sessions if s["session_id"] == session_arg), None) if session_arg else None
    if not sel and sessions:
        sel = sessions[0]

    rounds: list[dict[str, Any]] = []
    top_telemetry: dict[str, Any] = {}
    if sel:
        detail = parse_session_detail(Path(sel["path"]))
        top_telemetry = detail["telemetry"]
        matched_task = next((t for t in tasks if t.get("codex_session_id") == sel["session_id"]), None)
        dispatch_msg = None
        if matched_task:
            dispatch_msg = next(
                (m for m in reversed(messages) if m["msg_type"] == "dispatch" and m.get("task_key") == matched_task["task_key"]),
                None,
            )
        parsed_rounds = detail["rounds"] or [
            {
                "round_no": 1,
                "prompt": "",
                "commentary": "",
                "final_summary": "",
                "commands": [],
                "files_changed": [],
                "is_complete": not sel["is_active"],
                "duration_sec": 0,
                "started_label": detail["started_label"],
            }
        ]
        for idx, rr in enumerate(parsed_rounds):
            raw_prompt = rr["prompt"] or (dispatch_msg["content"] if idx == 0 and dispatch_msg else "")
            is_last = idx == len(parsed_rounds) - 1
            rounds.append(
                {
                    "round_no": idx + 1,
                    "task_key": matched_task["task_key"] if matched_task else "",
                    "task_title": matched_task["title"] if matched_task else "",
                    "dispatch_time": rr["started_label"] or (_short_time(dispatch_msg["created_at"]) if dispatch_msg else ""),
                    "dispatch_brief": _extract_task_brief(raw_prompt) if raw_prompt else "（本地 Codex CLI 直接执行的会话）",
                    "dispatch_full": raw_prompt,
                    "is_active": sel["is_active"] and is_last and not rr["is_complete"],
                    "is_complete": rr["is_complete"] or not (sel["is_active"] and is_last),
                    "duration_sec": rr["duration_sec"],
                    "commentary": rr["commentary"],
                    "commands": rr["commands"],
                    "files_changed": rr["files_changed"],
                    "final_summary": rr["final_summary"],
                    "model": detail["telemetry"]["model"],
                    "effort": detail["telemetry"]["effort"],
                }
            )

    return {
        "sidebar_projects": sidebar_projects,
        "active_project": str(target),
        "project_name": target.name,
        "change": change_row,
        "selected_session_id": sel["session_id"] if sel else "",
        "rounds": rounds,
        "telemetry": top_telemetry,
        "tasks": tasks,
    }


def get_file_diff(project_arg: Optional[str], file_path: Optional[str] = None) -> dict[str, Any]:
    target = resolve_project(project_arg)
    if not target:
        return {"ok": False, "diff": "", "error": "No project found"}

    diff_text = ""
    if file_path:
        diff_text = _run_git(target, "diff", "--", file_path)
        if not diff_text:
            diff_text = _run_git(target, "diff", "HEAD~1..HEAD", "--", file_path)
        if not diff_text:
            candidate = (target / file_path).resolve()
            try:
                candidate.relative_to(target.resolve())
                if candidate.is_file() and candidate.stat().st_size < 100_000:
                    content = candidate.read_text(encoding="utf-8", errors="replace")
                    diff_text = f"--- /dev/null\n+++ b/{file_path}\n" + "\n".join(
                        f"+{ln}" for ln in content.splitlines()[:200]
                    )
            except Exception:
                pass
    else:
        diff_text = _run_git(target, "diff") or _run_git(target, "diff", "HEAD~1..HEAD")

    if len(diff_text) > 36000:
        diff_text = diff_text[:36000] + "\n... [diff truncated]"
    return {"ok": True, "file": file_path or "(all)", "diff": diff_text.strip() or "(暂无可展示的 Diff)"}


def set_control_state(
    project_arg: Optional[str],
    change_id: str,
    flag: str = "none",
    note: Optional[str] = None,
) -> dict[str, Any]:
    target = resolve_project(project_arg)
    if not target or not change_id or flag not in ("none", "pause", "abort"):
        return {"ok": False, "error": "Invalid project, change_id, or flag"}
    db_path = db.init_db(db.resolve_db_path(target))
    with db.connect(db_path) as conn:
        db.set_control(conn, change_id, flag, note)
        db.add_message(conn, change_id, "human", "note", f"control={flag} note={note or ''}")
    return {"ok": True, "change_id": change_id, "flag": flag, "note": note}


TOOLS_LIST = [
    {
        "name": "open_codex_monitor",
        "title": "Antigravity × Codex",
        "description": "Open the native Antigravity × Codex monitor UI (one-way real-time supervision, token/cache telemetry, and inline git diff).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Optional project root directory path"},
            },
            "additionalProperties": False,
        },
        "_meta": {
            "ui": {"resourceUri": APP_URI},
            "com.google.antigravity/ui": {
                "entrypoints": [
                    {"type": "aux_pane", "title": "Antigravity × Codex"},
                    {"type": "global", "title": "Antigravity × Codex"},
                ]
            },
        },
    },
    {
        "name": "app_poll_state",
        "title": "Poll local Codex orchestrator state (app-only)",
        "description": "Read-only local poll of SQLite state and Codex CLI rollout telemetry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "session": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "app_get_diff",
        "title": "Read local git diff for modified file (app-only)",
        "description": "Read working-tree or last-commit git diff for a specific file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "file": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "app_set_control",
        "title": "Update change pause/note control flag (app-only)",
        "description": "Write control_flag (none/pause/abort) and user_note into local SQLite WAL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "change_id": {"type": "string"},
                "flag": {"type": "string", "enum": ["none", "pause", "abort"]},
                "note": {"type": "string"},
            },
            "required": ["change_id", "flag"],
            "additionalProperties": False,
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
]


def handle_request(req: dict[str, Any]) -> Optional[dict[str, Any]]:
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params") or {}

    if method == "notifications/initialized" or req_id is None:
        return None

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def err(code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        proto = params.get("protocolVersion") or "2024-11-05"
        return ok(
            {
                "protocolVersion": proto,
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "codex-orchestrator-mcp-app", "version": "0.3.0"},
            }
        )

    if method == "ping":
        return ok({})

    if method == "tools/list":
        return ok({"tools": TOOLS_LIST})

    if method == "resources/list":
        return ok(
            {
                "resources": [
                    {
                        "uri": APP_URI,
                        "name": "Antigravity × Codex",
                        "description": "Warm minimalist local MCP App monitor for Antigravity -> Codex CLI",
                        "mimeType": "text/html;profile=mcp-app",
                        "_meta": {"ui": {"prefersBorder": False}},
                    }
                ]
            }
        )

    if method == "resources/read":
        uri = params.get("uri")
        if uri != APP_URI:
            return err(-32602, f"Unknown resource URI: {uri}")
        html = APP_HTML_PATH.read_text(encoding="utf-8") if APP_HTML_PATH.exists() else "<h1>app.html missing</h1>"
        return ok(
            {
                "contents": [
                    {
                        "uri": APP_URI,
                        "mimeType": "text/html;profile=mcp-app",
                        "text": html,
                        "_meta": {"ui": {"prefersBorder": False}},
                    }
                ]
            }
        )

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "open_codex_monitor":
            snap = build_state_snapshot(project_arg=args.get("project"))
            summary = f"Opened Antigravity × Codex monitor for {snap['project_name'] or '(none)'}."
            return ok({"content": [{"type": "text", "text": summary}], "structuredContent": snap})

        if name == "app_poll_state":
            snap = build_state_snapshot(project_arg=args.get("project"), session_arg=args.get("session"))
            return ok({"content": [{"type": "text", "text": "ok"}], "structuredContent": snap})

        if name == "app_get_diff":
            res = get_file_diff(args.get("project"), args.get("file"))
            return ok({"content": [{"type": "text", "text": "ok"}], "structuredContent": res})

        if name == "app_set_control":
            res = set_control_state(args.get("project"), args.get("change_id", ""), args.get("flag", "none"), args.get("note"))
            return ok({"content": [{"type": "text", "text": "ok"}], "structuredContent": res})

        return err(-32601, f"Unknown tool: {name}")

    return err(-32601, f"Method not found: {method}")


def run_stdio_loop() -> None:
    if sys.platform == "win32":
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)

    for raw_line in sys.stdin.buffer:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_request(req)
        if resp is not None:
            out = (json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8")
            sys.stdout.buffer.write(out)
            sys.stdout.buffer.flush()


def run_self_test() -> None:
    init_res = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    res_read = handle_request({"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": APP_URI}})
    poll_res = handle_request({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "app_poll_state", "arguments": {}}})
    snap = poll_res["result"]["structuredContent"]
    print(
        json.dumps(
            {
                "ok": True,
                "server": init_res["result"]["serverInfo"],
                "html_bytes": len(res_read["result"]["contents"][0]["text"]),
                "projects": [p["name"] for p in snap["sidebar_projects"]],
                "rounds": len(snap["rounds"]),
                "telemetry": snap["telemetry"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        run_self_test()
    else:
        run_stdio_loop()
