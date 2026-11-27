"""Standalone draggable/minimizable/maximizable desktop mini-window for Antigravity x Codex."""

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

SCRIPTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPTS_DIR.parent.parent.parent
MCP_DIR = PLUGIN_ROOT / "mcp_server"

sys.path.insert(0, str(SCRIPTS_DIR))
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
                events.append({"time": ts, "kind": "tool_call", "label": f"$ {name}", "text": raw_in.strip()[:1200]})
            elif ptype in ("custom_tool_call_output", "function_call_output"):
                out = payload.get("output")
                text_out = ""
                if isinstance(out, str):
                    text_out = out
                elif isinstance(out, list):
                    text_out = "".join(
                        item["text"] for item in out if isinstance(item, dict) and isinstance(item.get("text"), str)
                    )
                text_out = text_out.strip()
                if text_out:
                    events.append({"time": ts, "kind": "tool_output", "label": "输出", "text": text_out[:1200]})
    return events[-limit:]


def open_mini_window(url: str, width: int = 920, height: int = 660) -> None:
    """Open URL as a native draggable, minimizable, maximizable desktop app window."""
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


def _build_standalone_html() -> str:
    """Reuse the precompiled v0.4.0 MCP App HTML and inject a tiny local HTTP bridge for standalone desktop mode."""
    app_html = MCP_DIR / "app.html"
    raw = app_html.read_text(encoding="utf-8")
    bridge = """<script>
// Standalone desktop window bridge: intercepts MCP postMessage RPCs and routes them to local HTTP endpoints.
(function() {
  const origPost = window.parent.postMessage.bind(window.parent);
  if (window.parent === window) {
    window.addEventListener("message", async (ev) => {
      const msg = ev.data;
      if (!msg || msg.jsonrpc !== "2.0" || !msg.method) return;
      const respond = (result) => window.postMessage({ jsonrpc: "2.0", id: msg.id, result }, "*");
      if (msg.method === "ui/initialize") {
        respond({
          protocolVersion: "2026-01-26",
          hostInfo: { name: "codex-desktop-window", version: "0.4.0" },
          hostCapabilities: { serverTools: {} },
          hostContext: { theme: window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light", displayMode: "inline" }
        });
        return;
      }
      if (msg.method === "tools/call") {
        const { name, arguments: args } = msg.params || {};
        try {
          const r = await fetch("/api/tool", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, arguments: args || {} })
          });
          const data = await r.json();
          respond(data);
        } catch (e) {
          respond({ isError: true, content: [{ type: "text", text: String(e) }] });
        }
      }
    });
  }
})();
</script>"""
    return raw.replace("<script>", bridge + "\n<script>", 1)


def make_handler(db_path: Path):
    if str(MCP_DIR) not in sys.path:
        sys.path.insert(0, str(MCP_DIR))
    import server as mcp_srv  # noqa: E402

    default_project = str(db_path.parent.parent)

    class ConsoleHandler(BaseHTTPRequestHandler):
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

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                body = _build_standalone_html().encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._send_json({"ok": False, "error": "Not found"}, status=HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/tool":
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length > 0 else b"{}"
                body = json.loads(raw.decode("utf-8"))
                name = body.get("name")
                args = body.get("arguments") or {}
                if not args.get("project") and Path(default_project).is_dir():
                    args["project"] = default_project
                resp = mcp_srv.handle_request(
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
                )
                self._send_json(resp["result"] if resp and "result" in resp else {"isError": True})
                return
            self._send_json({"ok": False, "error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    return ConsoleHandler


def serve_console(db_path: Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    db.init_db(db_path)

    if open_browser:
        try:
            import threading
            import webview  # type: ignore

            server = ThreadingHTTPServer((host, 0), make_handler(db_path))
            actual_port = server.server_address[1]
            url = f"http://{host}:{actual_port}/"
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            print("Launching native desktop window: Antigravity × Codex", flush=True)
            webview.create_window(
                "Antigravity × Codex",
                url=url,
                width=960,
                height=680,
                min_size=(420, 520),
                resizable=True,
            )
            webview.start()
            server.shutdown()
            server.server_close()
            return
        except ImportError:
            pass

    server = ThreadingHTTPServer((host, port), make_handler(db_path))
    url = f"http://{host}:{port}/"
    print(f"Antigravity x Codex desktop window running at {url}", flush=True)
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

    parser = argparse.ArgumentParser(description="Antigravity x Codex Desktop Mini Window")
    parser.add_argument("--project", default=".", help="Target project root directory")
    parser.add_argument("--db", default=None, help="Optional override path to SQLite DB")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    ns = parser.parse_args()
    target_db = db.resolve_db_path(Path(ns.project).expanduser().resolve(), ns.db)
    serve_console(target_db, host=ns.host, port=ns.port, open_browser=ns.open)
