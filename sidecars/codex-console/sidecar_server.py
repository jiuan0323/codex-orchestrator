"""Antigravity native Sidecar web server for Codex Orchestrator."""

from __future__ import annotations

import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

SIDECAR_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SIDECAR_DIR.parent.parent
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "codex-orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import console  # noqa: E402
import db  # noqa: E402


def _uri_to_path(uri: str) -> Optional[Path]:
    if not uri:
        return None
    if uri.startswith("file:///"):
        raw = unquote(uri[8:])
        if os.name == "nt":
            raw = raw.replace("/", "\\")
        else:
            raw = "/" + raw
        return Path(raw)
    return Path(uri)


def discover_projects(extra_workspace_uris: Optional[list[str]] = None) -> list[dict[str, Any]]:
    candidates: dict[str, Path] = {}

    def _add(p: Optional[Path]) -> None:
        if not p:
            return
        try:
            rp = p.expanduser().resolve()
        except Exception:
            return
        if not rp.is_dir():
            return
        db_file = rp / ".codex-orchestrator" / "state.db"
        if db_file.exists():
            candidates[str(rp).lower()] = rp

    for uri in extra_workspace_uris or []:
        _add(_uri_to_path(uri))

    scratch_root = Path.home() / ".gemini" / "antigravity" / "scratch"
    if scratch_root.is_dir():
        for child in scratch_root.iterdir():
            if child.is_dir():
                _add(child)

    for sess in console.find_project_codex_sessions([], max_scan=40):
        cwd = sess.get("cwd")
        if cwd:
            _add(Path(cwd))

    items: list[dict[str, Any]] = []
    for rp in candidates.values():
        db_file = rp / ".codex-orchestrator" / "state.db"
        try:
            mtime = db_file.stat().st_mtime
        except OSError:
            mtime = 0.0
        items.append({"name": rp.name, "path": str(rp), "db_path": str(db_file), "mtime": mtime})

    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def resolve_target_project(requested: Optional[str], workspace_uris: list[str]) -> Optional[Path]:
    if requested:
        p = Path(requested).expanduser().resolve()
        if (p / ".codex-orchestrator" / "state.db").exists():
            return p
    projects = discover_projects(workspace_uris)
    if projects:
        return Path(projects[0]["path"])
    for uri in workspace_uris:
        p = _uri_to_path(uri)
        if p and p.is_dir():
            return p
    return None


def make_sidecar_handler():
    ui_path = SIDECAR_DIR / "ui.html"

    class SidecarHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            return json.loads(raw.decode("utf-8"))

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)

            if parsed.path in ("/healthz", "/health"):
                self._send_json({"ok": True, "service": "codex-orchestrator-sidecar"})
                return

            if parsed.path in ("/", "/ui.html", "/index.html"):
                body = ui_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            ws_uris = qs.get("workspace") or []
            req_proj = (qs.get("project") or [None])[0] or None

            if parsed.path == "/api/projects":
                projects = discover_projects(ws_uris)
                self._send_json({"projects": projects})
                return

            if parsed.path == "/api/status":
                projects = discover_projects(ws_uris)
                target = resolve_target_project(req_proj, ws_uris)
                if not target:
                    self._send_json(
                        {
                            "projects": projects,
                            "active_project": "",
                            "changes": [],
                            "all_changes": [],
                            "tasks": [],
                            "decisions": [],
                            "recent_messages": [],
                        }
                    )
                    return

                db_path = db.resolve_db_path(target)
                if not db_path.exists():
                    self._send_json(
                        {
                            "projects": projects,
                            "active_project": str(target),
                            "changes": [],
                            "all_changes": [],
                            "tasks": [],
                            "decisions": [],
                            "recent_messages": [],
                        }
                    )
                    return

                db.init_db(db_path)
                change_id = (qs.get("change") or [None])[0] or None
                limit = int((qs.get("limit") or ["50"])[0])
                with db.connect(db_path) as conn:
                    data = db.fetch_status(conn, change_id=change_id, recent_messages=limit)
                    all_changes = [
                        dict(r)
                        for r in conn.execute("SELECT * FROM changes ORDER BY updated_at DESC").fetchall()
                    ]
                data["projects"] = projects
                data["active_project"] = str(target)
                data["all_changes"] = all_changes
                data["db_path"] = str(db_path)
                self._send_json(data)
                return

            if parsed.path == "/api/live":
                target = resolve_target_project(req_proj, ws_uris)
                wanted_sid = (qs.get("session") or [None])[0] or None
                limit = int((qs.get("limit") or ["80"])[0])
                roots = [str(target)] if target else []
                if target:
                    db_path = db.resolve_db_path(target)
                    if db_path.exists():
                        with db.connect(db_path) as conn:
                            for r in conn.execute("SELECT project_root, worktree_path FROM changes").fetchall():
                                if r["project_root"]:
                                    roots.append(r["project_root"])
                                if r["worktree_path"]:
                                    roots.append(r["worktree_path"])
                sessions = console.find_project_codex_sessions(roots) if roots else []
                sel = next((s for s in sessions if s["session_id"] == wanted_sid), None) if wanted_sid else None
                if not sel and sessions:
                    sel = sessions[0]
                events = console.parse_codex_session_events(Path(sel["path"]), limit=limit) if sel else []
                self._send_json(
                    {
                        "sessions": sessions,
                        "selected_session_id": sel["session_id"] if sel else "",
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

            project_str = body.get("project")
            target = resolve_target_project(project_str, [])
            if not target:
                self._send_json({"ok": False, "error": "No active project"}, status=HTTPStatus.BAD_REQUEST)
                return
            db_path = db.init_db(db.resolve_db_path(target))

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
                    self._send_json({"ok": False, "error": "Missing fields"}, status=HTTPStatus.BAD_REQUEST)
                    return
                with db.connect(db_path) as conn:
                    did = db.add_decision(conn, change_id, topic, decision, decided_by=decided_by)
                    db.add_message(conn, change_id, decided_by, "decision", f"{topic}: {decision}")
                self._send_json({"ok": True, "decision_id": did})
                return

            self._send_json({"ok": False, "error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    return SidecarHandler


def main() -> None:
    port = int(os.environ.get("ANTIGRAVITY_SIDECAR_WEB_PORT", "8766"))
    server = ThreadingHTTPServer(("127.0.0.1", port), make_sidecar_handler())
    print(f"Codex Orchestrator Sidecar listening on http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
