"""Antigravity -> Codex CLI orchestrator with OpenSpec + SQLite WAL middleware."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402

TASK_LINE_RE = re.compile(r"^(\s*-\s*\[)([ xX])(\]\s*)(?:([A-Za-z0-9_.-]+)\s+)?(.+?)\s*$")
BLOCKED_RE = re.compile(r"^\s*BLOCKED\s*:\s*(.+)$", re.MULTILINE)


def run_git(project_root: Path, *args: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except OSError:
        return None
    return None


def find_tasks_md(project_root: Path, change_id: str) -> Path:
    return project_root / "openspec" / "changes" / change_id / "tasks.md"


def parse_tasks_md(tasks_md: Path) -> list[tuple[str, str, bool]]:
    """Return list of (task_key, title, is_done) from OpenSpec tasks.md."""
    results: list[tuple[str, str, bool]] = []
    auto_idx = 0
    for line in tasks_md.read_text(encoding="utf-8").splitlines():
        m = TASK_LINE_RE.match(line)
        if not m:
            continue
        checked = m.group(2).lower() == "x"
        raw_key = m.group(4)
        title = m.group(5).strip()
        if raw_key and re.match(r"^\d+(\.\d+)*\.?$", raw_key):
            key = raw_key.rstrip(".")
        else:
            auto_idx += 1
            if raw_key:
                title = f"{raw_key} {title}".strip()
            key = f"T{auto_idx}"
        results.append((key, title, checked))
    return results


def check_off_task_in_spec(tasks_md: Path, task_key: str) -> bool:
    """Mark the matching task checkbox as [x] in OpenSpec tasks.md (Antigravity-only write)."""
    if not tasks_md.exists():
        return False
    lines = tasks_md.read_text(encoding="utf-8").splitlines()
    updated = False
    auto_idx = 0
    new_lines: list[str] = []
    for line in lines:
        m = TASK_LINE_RE.match(line)
        if m:
            raw_key = m.group(4)
            if raw_key and re.match(r"^\d+(\.\d+)*\.?$", raw_key):
                key = raw_key.rstrip(".")
            else:
                auto_idx += 1
                key = f"T{auto_idx}"
            if key == task_key:
                line = f"{m.group(1)}x{m.group(3)}{((raw_key + ' ') if raw_key else '')}{m.group(5)}"
                updated = True
        new_lines.append(line)
    if updated:
        tasks_md.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return updated


def build_codex_prompt(
    change_id: str,
    task_key: str,
    task_title: str,
    instructions: str,
    decisions: list[dict[str, Any]],
    user_note: Optional[str] = None,
) -> str:
    decision_block = (
        "\n".join(f"- [{d['decided_by']}] {d['topic']}: {d['decision']}" for d in decisions)
        if decisions
        else "- (none yet)"
    )
    note_block = f"\n## User Note\n{user_note}\n" if user_note else ""
    return f"""You are executing a single scoped task dispatched by Antigravity (Master Orchestrator).

## Scope
- OpenSpec Change: `{change_id}`
- Task `{task_key}`: {task_title}

## Execution Instructions
{instructions}

## Locked Decisions (Do NOT revisit or ask about these)
{decision_block}
{note_block}
## Strict Execution Rules
1. Implement ONLY this task cleanly and directly. Do NOT edit `openspec/changes/{change_id}/tasks.md` checkboxes (Antigravity owns OpenSpec status).
2. Avoid defensive bloat: trust internal types and contracts; do NOT add speculative try/catch wrappers, redundant null checks for guaranteed internal values, or unused abstractions.
3. Make routine local decisions (naming, private helpers, small refactors) autonomously following existing project style.
4. ONLY if you hit a genuine ambiguity or breaking architectural decision that cannot be inferred from the codebase, stop immediately and end your response with a single line:
   `BLOCKED: <concise question> | Options: <A / B>`
5. Otherwise finish the implementation, run relevant targeted verification, and summarize files changed and test results concisely.
"""


def parse_codex_jsonl(stdout: str) -> tuple[Optional[str], str]:
    """Extract (session_id, final_text) from `codex exec --json` output."""
    session_id: Optional[str] = None
    messages: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(evt, dict):
            continue
        if not session_id:
            session_id = evt.get("thread_id") or evt.get("session_id")
        evt_type = evt.get("type", "")
        if evt_type == "item.completed":
            item = evt.get("item") or {}
            if item.get("type") in ("agent_message", "message"):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    messages.append(text.strip())
                content = item.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            messages.append(part["text"].strip())
        elif evt_type == "message" and isinstance(evt.get("text"), str):
            messages.append(evt["text"].strip())
    final_text = messages[-1] if messages else stdout.strip()
    return session_id, final_text


def invoke_codex(
    work_dir: Path,
    prompt: str,
    session_id: Optional[str] = None,
    extra_args: Optional[list[str]] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    codex_bin = shutil.which("codex")
    if dry_run:
        return {
            "returncode": 0,
            "session_id": session_id or "dry-run-session-001",
            "output": "[DRY RUN] Codex invocation skipped.",
            "stderr": "",
            "cmd": ["codex", "exec", "..."],
        }
    if not codex_bin:
        raise RuntimeError("`codex` CLI not found on PATH. Install with: npm install -g @openai/codex")

    with tempfile.NamedTemporaryFile(prefix="codex_last_", suffix=".txt", delete=False) as tf:
        last_msg_path = Path(tf.name)

    try:
        if session_id:
            cmd = [
                codex_bin,
                "exec",
                "resume",
                "--json",
                "--skip-git-repo-check",
                "-o",
                str(last_msg_path),
                session_id,
                "-",
            ]
        else:
            cmd = [
                codex_bin,
                "exec",
                "--json",
                "--sandbox",
                "workspace-write",
                "--approve-for-me",
                "--skip-git-repo-check",
                "-C",
                str(work_dir),
                "-o",
                str(last_msg_path),
                "-",
            ]
        if extra_args:
            cmd = cmd[:-1] + extra_args + [cmd[-1]]

        proc = subprocess.run(
            cmd,
            input=prompt,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        parsed_sid, jsonl_text = parse_codex_jsonl(proc.stdout)
        file_text = last_msg_path.read_text(encoding="utf-8", errors="replace").strip() if last_msg_path.exists() else ""
        final_text = file_text or jsonl_text
    finally:
        try:
            last_msg_path.unlink(missing_ok=True)
        except OSError:
            pass

    return {
        "returncode": proc.returncode,
        "session_id": parsed_sid or session_id,
        "output": final_text,
        "stderr": proc.stderr.strip(),
        "cmd": cmd,
    }


def cmd_init(args: argparse.Namespace) -> None:
    project_root = Path(args.project).expanduser().resolve()
    db_path = db.init_db(db.resolve_db_path(project_root, args.db))
    print(json.dumps({"ok": True, "project_root": str(project_root), "db_path": str(db_path)}, ensure_ascii=False, indent=2))


def cmd_sync_tasks(args: argparse.Namespace) -> None:
    project_root = Path(args.project).expanduser().resolve()
    db_path = db.init_db(db.resolve_db_path(project_root, args.db))
    tasks_md = find_tasks_md(project_root, args.change)
    if not tasks_md.exists():
        print(json.dumps({"ok": False, "error": f"tasks.md not found: {tasks_md}"}, ensure_ascii=False))
        sys.exit(1)

    parsed = parse_tasks_md(tasks_md)
    branch = args.branch or run_git(project_root, "rev-parse", "--abbrev-ref", "HEAD")
    worktree = str(Path(args.worktree).expanduser().resolve()) if args.worktree else str(project_root)

    with db.connect(db_path) as conn:
        db.upsert_change(conn, args.change, str(project_root), worktree_path=worktree, branch=branch)
        for key, title, is_done in parsed:
            db.upsert_task(conn, args.change, key, title, done_in_spec=is_done)

    print(
        json.dumps(
            {
                "ok": True,
                "change_id": args.change,
                "synced_tasks": len(parsed),
                "tasks": [{"task_key": k, "title": t, "done": d} for k, t, d in parsed],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_dispatch(args: argparse.Namespace) -> None:
    project_root = Path(args.project).expanduser().resolve()
    db_path = db.init_db(db.resolve_db_path(project_root, args.db))

    with db.connect(db_path) as conn:
        change = db.get_change(conn, args.change)
        if not change:
            branch = run_git(project_root, "rev-parse", "--abbrev-ref", "HEAD")
            db.upsert_change(conn, args.change, str(project_root), worktree_path=str(project_root), branch=branch)
            change = db.get_change(conn, args.change)

        assert change is not None
        if change["control_flag"] in ("pause", "abort") and not args.ignore_control:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "halted_by_control": change["control_flag"],
                        "user_note": change.get("user_note"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            sys.exit(2)

        task = db.get_task(conn, args.change, args.task)
        if not task:
            db.upsert_task(conn, args.change, args.task, args.title or f"Task {args.task}")
            task = db.get_task(conn, args.change, args.task)
        assert task is not None

        work_dir = Path(change["worktree_path"] or project_root)
        base_commit = task["base_commit"] or run_git(work_dir, "rev-parse", "HEAD")
        decisions = db.list_decisions(conn, args.change)
        prompt = build_codex_prompt(
            change_id=args.change,
            task_key=args.task,
            task_title=task["title"],
            instructions=args.instructions,
            decisions=decisions,
            user_note=change.get("user_note"),
        )

        db.update_task_fields(
            conn,
            args.change,
            args.task,
            status="running",
            base_commit=base_commit,
            retry_count=task["retry_count"] + 1,
        )
        db.add_message(conn, args.change, "antigravity", "dispatch", prompt, task_key=args.task)

    result = invoke_codex(work_dir, prompt, session_id=None, dry_run=args.dry_run)
    blocker_match = BLOCKED_RE.search(result["output"])

    with db.connect(db_path) as conn:
        task_now = db.get_task(conn, args.change, args.task)
        assert task_now is not None
        sid = result["session_id"]
        if blocker_match:
            blocker_text = blocker_match.group(1).strip()
            new_blocks = task_now["block_count"] + 1
            db.update_task_fields(
                conn,
                args.change,
                args.task,
                codex_session_id=sid,
                status="blocked",
                block_count=new_blocks,
                last_blocker=blocker_text,
            )
            db.add_message(
                conn, args.change, "codex", "blocked", result["output"], task_key=args.task, codex_session_id=sid
            )
            status_out = "blocked"
            circuit_breaker = new_blocks >= 2
        elif result["returncode"] != 0:
            db.update_task_fields(
                conn,
                args.change,
                args.task,
                codex_session_id=sid,
                status="failed",
                last_blocker=result["stderr"] or result["output"][:500],
            )
            db.add_message(
                conn,
                args.change,
                "codex",
                "output",
                result["output"] or result["stderr"],
                task_key=args.task,
                codex_session_id=sid,
            )
            status_out = "failed"
            circuit_breaker = False
        else:
            db.update_task_fields(
                conn,
                args.change,
                args.task,
                codex_session_id=sid,
                status="reviewing",
            )
            db.add_message(
                conn, args.change, "codex", "output", result["output"], task_key=args.task, codex_session_id=sid
            )
            status_out = "reviewing"
            circuit_breaker = False

    print(
        json.dumps(
            {
                "ok": result["returncode"] == 0,
                "status": status_out,
                "circuit_breaker_triggered": circuit_breaker,
                "codex_session_id": result["session_id"],
                "base_commit": base_commit,
                "output": result["output"],
                "stderr": result["stderr"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_resume(args: argparse.Namespace) -> None:
    project_root = Path(args.project).expanduser().resolve()
    db_path = db.init_db(db.resolve_db_path(project_root, args.db))

    with db.connect(db_path) as conn:
        change = db.get_change(conn, args.change)
        task = db.get_task(conn, args.change, args.task)
        if not change or not task or not task.get("codex_session_id"):
            print(json.dumps({"ok": False, "error": "No existing codex_session_id bound to this task."}, ensure_ascii=False))
            sys.exit(1)

        sid = task["codex_session_id"]
        work_dir = Path(change["worktree_path"] or project_root)
        if args.topic:
            db.add_decision(conn, args.change, args.topic, args.message, task_key=args.task, decided_by=args.decided_by)
        db.add_message(conn, args.change, args.decided_by, "resume", args.message, task_key=args.task, codex_session_id=sid)
        db.update_task_fields(conn, args.change, args.task, status="running")

    resume_prompt = (
        f"Decision from Antigravity: {args.message}\n\n"
        "Proceed directly with the implementation without further confirmation unless a new breaking blocker arises "
        "(use `BLOCKED: <question>` only if strictly necessary)."
    )
    result = invoke_codex(work_dir, resume_prompt, session_id=sid, dry_run=args.dry_run)
    blocker_match = BLOCKED_RE.search(result["output"])

    with db.connect(db_path) as conn:
        task_now = db.get_task(conn, args.change, args.task)
        assert task_now is not None
        if blocker_match:
            new_blocks = task_now["block_count"] + 1
            db.update_task_fields(
                conn,
                args.change,
                args.task,
                status="blocked",
                block_count=new_blocks,
                last_blocker=blocker_match.group(1).strip(),
            )
            db.add_message(conn, args.change, "codex", "blocked", result["output"], task_key=args.task, codex_session_id=sid)
            status_out = "blocked"
            circuit_breaker = new_blocks >= 2
        elif result["returncode"] != 0:
            db.update_task_fields(conn, args.change, args.task, status="failed")
            db.add_message(
                conn, args.change, "codex", "output", result["output"] or result["stderr"], task_key=args.task, codex_session_id=sid
            )
            status_out = "failed"
            circuit_breaker = False
        else:
            db.update_task_fields(conn, args.change, args.task, status="reviewing")
            db.add_message(conn, args.change, "codex", "output", result["output"], task_key=args.task, codex_session_id=sid)
            status_out = "reviewing"
            circuit_breaker = False

    print(
        json.dumps(
            {
                "ok": result["returncode"] == 0,
                "status": status_out,
                "circuit_breaker_triggered": circuit_breaker,
                "codex_session_id": sid,
                "output": result["output"],
                "stderr": result["stderr"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_gate(args: argparse.Namespace) -> None:
    project_root = Path(args.project).expanduser().resolve()
    db_path = db.init_db(db.resolve_db_path(project_root, args.db))

    with db.connect(db_path) as conn:
        change = db.get_change(conn, args.change)
        work_dir = Path(change["worktree_path"]) if change and change.get("worktree_path") else project_root

    proc = subprocess.run(
        args.command,
        shell=True,
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    passed = proc.returncode == 0
    combined = (proc.stdout + "\n" + proc.stderr).strip()

    with db.connect(db_path) as conn:
        db.add_message(
            conn,
            args.change,
            "gate",
            "gate_pass" if passed else "gate_fail",
            f"$ {args.command}\nexit={proc.returncode}\n{combined[-4000:]}",
            task_key=args.task,
        )

    print(
        json.dumps(
            {"ok": passed, "returncode": proc.returncode, "output": combined[-4000:]},
            ensure_ascii=False,
            indent=2,
        )
    )
    if not passed:
        sys.exit(proc.returncode or 1)


def cmd_complete(args: argparse.Namespace) -> None:
    project_root = Path(args.project).expanduser().resolve()
    db_path = db.init_db(db.resolve_db_path(project_root, args.db))

    with db.connect(db_path) as conn:
        change = db.get_change(conn, args.change)
        work_dir = Path(change["worktree_path"]) if change and change.get("worktree_path") else project_root
        result_commit = run_git(work_dir, "rev-parse", "HEAD")
        db.update_task_fields(conn, args.change, args.task, status="done", result_commit=result_commit)
        db.add_message(
            conn,
            args.change,
            "antigravity",
            "review",
            args.summary or f"Accepted task {args.task} at commit {result_commit}",
            task_key=args.task,
        )

    spec_updated = check_off_task_in_spec(find_tasks_md(project_root, args.change), args.task)
    print(
        json.dumps(
            {"ok": True, "task_key": args.task, "result_commit": result_commit, "spec_checked_off": spec_updated},
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_rollback(args: argparse.Namespace) -> None:
    project_root = Path(args.project).expanduser().resolve()
    db_path = db.init_db(db.resolve_db_path(project_root, args.db))

    with db.connect(db_path) as conn:
        change = db.get_change(conn, args.change)
        task = db.get_task(conn, args.change, args.task)
        if not task or not task.get("base_commit"):
            print(json.dumps({"ok": False, "error": "No base_commit recorded for this task."}, ensure_ascii=False))
            sys.exit(1)
        work_dir = Path(change["worktree_path"]) if change and change.get("worktree_path") else project_root
        base_commit = task["base_commit"]

    if not args.force:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"Refusing destructive git reset without --force. Target base_commit={base_commit}",
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    res = run_git(work_dir, "reset", "--hard", base_commit)
    if res is None:
        print(json.dumps({"ok": False, "error": f"git reset --hard {base_commit} failed"}, ensure_ascii=False))
        sys.exit(1)

    with db.connect(db_path) as conn:
        db.update_task_fields(conn, args.change, args.task, status="rolled_back")
        db.add_message(
            conn,
            args.change,
            "antigravity",
            "rollback",
            f"Rolled back to {base_commit}. Reason: {args.reason or 'unspecified'}",
            task_key=args.task,
        )

    print(json.dumps({"ok": True, "rolled_back_to": base_commit}, ensure_ascii=False, indent=2))


def cmd_decide(args: argparse.Namespace) -> None:
    project_root = Path(args.project).expanduser().resolve()
    db_path = db.init_db(db.resolve_db_path(project_root, args.db))
    with db.connect(db_path) as conn:
        did = db.add_decision(conn, args.change, args.topic, args.decision, task_key=args.task, decided_by=args.by)
        db.add_message(
            conn, args.change, args.by, "decision", f"{args.topic}: {args.decision}", task_key=args.task
        )
    print(json.dumps({"ok": True, "decision_id": did}, ensure_ascii=False, indent=2))


def cmd_control(args: argparse.Namespace) -> None:
    project_root = Path(args.project).expanduser().resolve()
    db_path = db.init_db(db.resolve_db_path(project_root, args.db))
    with db.connect(db_path) as conn:
        db.set_control(conn, args.change, args.flag, args.note)
        db.add_message(conn, args.change, "human", "note", f"control={args.flag} note={args.note or ''}")
    print(json.dumps({"ok": True, "change_id": args.change, "control_flag": args.flag, "user_note": args.note}, ensure_ascii=False, indent=2))


def cmd_status(args: argparse.Namespace) -> None:
    project_root = Path(args.project).expanduser().resolve()
    db_path = db.init_db(db.resolve_db_path(project_root, args.db))
    with db.connect(db_path) as conn:
        data = db.fetch_status(conn, change_id=args.change, recent_messages=args.limit)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_console(args: argparse.Namespace) -> None:
    import console  # noqa: E402

    project_root = Path(args.project).expanduser().resolve()
    db_path = db.resolve_db_path(project_root, args.db)
    console.serve_console(db_path, host=args.host, port=args.port, open_browser=args.open)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Antigravity -> Codex CLI Orchestrator")
    p.add_argument("--project", default=".", help="Target project root directory")
    p.add_argument("--db", default=None, help="Optional override path to SQLite DB")
    sub = p.add_subparsers(dest="cmd", required=False)

    sp = sub.add_parser("init", help="Initialize SQLite WAL middleware DB")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("sync-tasks", help="Sync OpenSpec changes/<change>/tasks.md into SQLite")
    sp.add_argument("--change", required=True)
    sp.add_argument("--worktree", default=None)
    sp.add_argument("--branch", default=None)
    sp.set_defaults(func=cmd_sync_tasks)

    sp = sub.add_parser("dispatch", help="Dispatch a scoped task to Codex CLI")
    sp.add_argument("--change", required=True)
    sp.add_argument("--task", required=True)
    sp.add_argument("--title", default=None)
    sp.add_argument("--instructions", required=True)
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--ignore-control", action="store_true")
    sp.set_defaults(func=cmd_dispatch)

    sp = sub.add_parser("resume", help="Resume a blocked or active Codex session with a decision")
    sp.add_argument("--change", required=True)
    sp.add_argument("--task", required=True)
    sp.add_argument("--message", required=True)
    sp.add_argument("--topic", default=None)
    sp.add_argument("--decided-by", default="antigravity", choices=["antigravity", "human"])
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_resume)

    sp = sub.add_parser("gate", help="Run hard verification gate (tests/lint/build) and log to SQLite")
    sp.add_argument("--change", required=True)
    sp.add_argument("--task", default=None)
    sp.add_argument("--command", required=True)
    sp.set_defaults(func=cmd_gate)

    sp = sub.add_parser("complete", help="Accept task, record result_commit, and check off OpenSpec tasks.md")
    sp.add_argument("--change", required=True)
    sp.add_argument("--task", required=True)
    sp.add_argument("--summary", default=None)
    sp.set_defaults(func=cmd_complete)

    sp = sub.add_parser("rollback", help="Reset worktree to task base_commit")
    sp.add_argument("--change", required=True)
    sp.add_argument("--task", required=True)
    sp.add_argument("--reason", default=None)
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_rollback)

    sp = sub.add_parser("decide", help="Record a locked architectural/product decision")
    sp.add_argument("--change", required=True)
    sp.add_argument("--task", default=None)
    sp.add_argument("--topic", required=True)
    sp.add_argument("--decision", required=True)
    sp.add_argument("--by", default="antigravity", choices=["antigravity", "human"])
    sp.set_defaults(func=cmd_decide)

    sp = sub.add_parser("control", help="Set pause/abort control flag or user_note")
    sp.add_argument("--change", required=True)
    sp.add_argument("--flag", required=True, choices=["none", "pause", "abort"])
    sp.add_argument("--note", default=None)
    sp.set_defaults(func=cmd_control)

    sp = sub.add_parser("status", help="Inspect changes, task bindings, decisions, and recent messages")
    sp.add_argument("--change", default=None)
    sp.add_argument("--limit", type=int, default=10)
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("console", aliases=["ui"], help="Launch local web console dashboard backed by SQLite WAL")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--open", action="store_true", help="Open browser automatically")
    sp.set_defaults(func=cmd_console)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.cmd:
        args.host = "127.0.0.1"
        args.port = 8765
        args.open = True
        cmd_console(args)
        return
    args.func(args)


if __name__ == "__main__":
    main()
