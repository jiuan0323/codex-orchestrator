"""Zero-dependency Python stdio MCP App server for Codex Orchestrator (SEP-1865)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
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


def parse_session_telemetry(path: Path, limit: int = 80) -> dict[str, Any]:
    """Read live events + token usage + cache hit rate from a Codex CLI rollout JSONL."""
    events = console.parse_codex_session_events(path, limit=limit)
    total_input = 0
    total_cached = 0
    total_output = 0
    total_reasoning = 0
    model_name = ""
    effort = ""
    turns = 0
    first_ts = ""
    last_ts = ""

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

    cache_hit_pct = round((total_cached / total_input) * 100.0, 1) if total_input > 0 else 0.0
    return {
        "events": events,
        "telemetry": {
            "model": model_name,
            "effort": effort,
            "turns": turns,
            "input_tokens": total_input,
            "cached_input_tokens": total_cached,
            "cache_hit_pct": cache_hit_pct,
            "output_tokens": total_output,
            "reasoning_output_tokens": total_reasoning,
            "first_ts": first_ts,
            "last_ts": last_ts,
        },
    }


def collect_git_changes(project_root: Path) -> list[dict[str, str]]:
    raw = _run_git(project_root, "status", "--porcelain")
    files: list[dict[str, str]] = []
    for line in raw.splitlines():
        if len(line) < 4:
            continue
        code = line[:2].strip() or "M"
        fpath = line[3:].strip().strip('"')
        if fpath.startswith(".codex-orchestrator/"):
            continue
        files.append({"status": code, "path": fpath})
    return files[:80]


def build_state_snapshot(
    project_arg: Optional[str] = None,
    session_arg: Optional[str] = None,
    limit: int = 60,
) -> dict[str, Any]:
    projects = discover_projects()
    target = resolve_project(project_arg)
    if not target:
        return {
            "projects": projects,
            "active_project": "",
            "changes": [],
            "tasks": [],
            "decisions": [],
            "recent_messages": [],
            "sessions": [],
            "selected_session_id": "",
            "events": [],
            "telemetry": {},
            "git_files": [],
        }

    db_path = db.resolve_db_path(target)
    status_data: dict[str, Any] = {"changes": [], "tasks": [], "decisions": [], "recent_messages": []}
    roots = [str(target)]

    if db_path.exists():
        db.init_db(db_path)
        with db.connect(db_path) as conn:
            status_data = db.fetch_status(conn, change_id=None, recent_messages=40)
            for r in conn.execute("SELECT project_root, worktree_path FROM changes").fetchall():
                if r["project_root"]:
                    roots.append(r["project_root"])
                if r["worktree_path"]:
                    roots.append(r["worktree_path"])
            for r in conn.execute("SELECT worktree_path FROM tasks WHERE worktree_path IS NOT NULL").fetchall():
                if r["worktree_path"]:
                    roots.append(r["worktree_path"])

    sessions = console.find_project_codex_sessions(roots)
    sel = next((s for s in sessions if s["session_id"] == session_arg), None) if session_arg else None
    if not sel and sessions:
        sel = sessions[0]

    live_data = parse_session_telemetry(Path(sel["path"]), limit=limit) if sel else {"events": [], "telemetry": {}}
    git_files = collect_git_changes(target)

    return {
        "projects": projects,
        "active_project": str(target),
        "changes": status_data.get("changes", []),
        "tasks": status_data.get("tasks", []),
        "decisions": status_data.get("decisions", []),
        "recent_messages": status_data.get("recent_messages", []),
        "sessions": sessions,
        "selected_session_id": sel["session_id"] if sel else "",
        "events": live_data["events"],
        "telemetry": live_data["telemetry"],
        "git_files": git_files,
    }


def get_file_diff(project_arg: Optional[str], file_path: Optional[str] = None) -> dict[str, Any]:
    target = resolve_project(project_arg)
    if not target:
        return {"ok": False, "diff": "", "error": "No project found"}

    if file_path:
        diff_text = _run_git(target, "diff", "--", file_path)
        if not diff_text:
            diff_text = _run_git(target, "diff", "--cached", "--", file_path)
        if not diff_text:
            candidate = (target / file_path).resolve()
            try:
                candidate.relative_to(target.resolve())
                if candidate.is_file() and candidate.stat().st_size < 100_000:
                    content = candidate.read_text(encoding="utf-8", errors="replace")
                    diff_text = f"--- /dev/null\n+++ b/{file_path}\n" + "\n".join(
                        f"+{ln}" for ln in content.splitlines()[:250]
                    )
            except Exception:
                pass
    else:
        diff_text = _run_git(target, "diff", "--stat") + "\n\n" + _run_git(target, "diff")

    if len(diff_text) > 40000:
        diff_text = diff_text[:40000] + "\n... [diff truncated]"
    return {"ok": True, "file": file_path or "(all)", "diff": diff_text.strip() or "(当前工作区无未提交改动)"}


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
        "title": "Codex 实时观察台",
        "description": "Open the native Antigravity Codex Orchestrator live monitor (tasks, live Codex CLI stream, token/cache telemetry, and real-time git diff).",
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
                    {"type": "aux_pane", "title": "Codex 实时观察台"},
                    {"type": "global", "title": "Codex 实时观察台"},
                ]
            },
        },
    },
    {
        "name": "app_poll_state",
        "title": "Poll local Codex orchestrator state (app-only)",
        "description": "Read-only local poll of SQLite state, Codex CLI rollout telemetry, token/cache stats, and git status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "session": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "app_get_diff",
        "title": "Read local git diff for modified file (app-only)",
        "description": "Read live working-tree git diff for a specific file or summary.",
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
                "serverInfo": {"name": "codex-orchestrator-mcp-app", "version": "0.2.0"},
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
                        "name": "Codex 实时观察台",
                        "description": "Zero-telemetry local MCP App UI for Codex Orchestrator",
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
            done_n = sum(1 for t in snap["tasks"] if t.get("status") == "done")
            summary = f"Opened Codex Monitor for {snap['active_project'] or '(none)'} ({done_n}/{len(snap['tasks'])} tasks done)."
            return ok({"content": [{"type": "text", "text": summary}], "structuredContent": snap})

        if name == "app_poll_state":
            snap = build_state_snapshot(
                project_arg=args.get("project"),
                session_arg=args.get("session"),
                limit=int(args.get("limit") or 60),
            )
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
    tools_res = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    res_read = handle_request({"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": APP_URI}})
    poll_res = handle_request({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "app_poll_state", "arguments": {}}})
    snap = poll_res["result"]["structuredContent"]
    print(
        json.dumps(
            {
                "ok": True,
                "server": init_res["result"]["serverInfo"],
                "tools": [t["name"] for t in tools_res["result"]["tools"]],
                "html_bytes": len(res_read["result"]["contents"][0]["text"]),
                "projects": [p["name"] for p in snap["projects"]],
                "tasks": len(snap["tasks"]),
                "telemetry": snap["telemetry"],
                "git_files": len(snap["git_files"]),
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
