"""SQLite WAL middleware for Antigravity <-> Codex CLI orchestration."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

DEFAULT_DB_RELPATH = Path(".codex-orchestrator") / "state.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS changes (
    change_id TEXT PRIMARY KEY,
    project_root TEXT NOT NULL,
    worktree_path TEXT,
    branch TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    control_flag TEXT NOT NULL DEFAULT 'none',
    user_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id TEXT NOT NULL,
    task_key TEXT NOT NULL,
    title TEXT NOT NULL,
    task_kind TEXT NOT NULL DEFAULT 'dev_entity',
    worktree_path TEXT,
    branch TEXT,
    owned_paths TEXT,
    codex_session_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    base_commit TEXT,
    result_commit TEXT,
    block_count INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_blocker TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(change_id, task_key),
    FOREIGN KEY(change_id) REFERENCES changes(change_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id TEXT NOT NULL,
    task_key TEXT,
    codex_session_id TEXT,
    sender TEXT NOT NULL,
    msg_type TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id TEXT NOT NULL,
    task_key TEXT,
    topic TEXT NOT NULL,
    decision TEXT NOT NULL,
    decided_by TEXT NOT NULL DEFAULT 'antigravity',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_change ON tasks(change_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(codex_session_id);
CREATE INDEX IF NOT EXISTS idx_messages_change ON messages(change_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_change ON decisions(change_id, id DESC);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_db_path(project_root: Path, db_override: Optional[str] = None) -> Path:
    if db_override:
        return Path(db_override).expanduser().resolve()
    return (project_root / DEFAULT_DB_RELPATH).resolve()


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_task_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    migrations = [
        ("task_kind", "TEXT NOT NULL DEFAULT 'dev_entity'"),
        ("worktree_path", "TEXT"),
        ("branch", "TEXT"),
        ("owned_paths", "TEXT"),
    ]
    for col, col_def in migrations:
        if col not in existing:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {col_def}")


def init_db(db_path: Path) -> Path:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _ensure_task_columns(conn)
    return db_path


def upsert_change(
    conn: sqlite3.Connection,
    change_id: str,
    project_root: str,
    worktree_path: Optional[str] = None,
    branch: Optional[str] = None,
    status: str = "active",
) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO changes (change_id, project_root, worktree_path, branch, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(change_id) DO UPDATE SET
            project_root = excluded.project_root,
            worktree_path = COALESCE(excluded.worktree_path, changes.worktree_path),
            branch = COALESCE(excluded.branch, changes.branch),
            updated_at = excluded.updated_at
        """,
        (change_id, project_root, worktree_path, branch, status, now, now),
    )


def upsert_task(
    conn: sqlite3.Connection,
    change_id: str,
    task_key: str,
    title: str,
    done_in_spec: bool = False,
    task_kind: str = "dev_entity",
    worktree_path: Optional[str] = None,
    branch: Optional[str] = None,
    owned_paths: Optional[str] = None,
) -> None:
    now = utc_now()
    initial_status = "done" if done_in_spec else "pending"
    conn.execute(
        """
        INSERT INTO tasks (change_id, task_key, title, task_kind, worktree_path, branch, owned_paths, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(change_id, task_key) DO UPDATE SET
            title = excluded.title,
            task_kind = COALESCE(excluded.task_kind, tasks.task_kind),
            worktree_path = COALESCE(excluded.worktree_path, tasks.worktree_path),
            branch = COALESCE(excluded.branch, tasks.branch),
            owned_paths = COALESCE(excluded.owned_paths, tasks.owned_paths),
            status = CASE
                WHEN ? = 1 THEN 'done'
                ELSE tasks.status
            END,
            updated_at = excluded.updated_at
        """,
        (
            change_id,
            task_key,
            title,
            task_kind,
            worktree_path,
            branch,
            owned_paths,
            initial_status,
            now,
            now,
            1 if done_in_spec else 0,
        ),
    )


def get_change(conn: sqlite3.Connection, change_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute("SELECT * FROM changes WHERE change_id = ?", (change_id,)).fetchone()
    return dict(row) if row else None


def get_task(conn: sqlite3.Connection, change_id: str, task_key: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM tasks WHERE change_id = ? AND task_key = ?",
        (change_id, task_key),
    ).fetchone()
    return dict(row) if row else None


def update_task_fields(
    conn: sqlite3.Connection,
    change_id: str,
    task_key: str,
    **fields: Any,
) -> None:
    if not fields:
        return
    fields["updated_at"] = utc_now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [change_id, task_key]
    conn.execute(f"UPDATE tasks SET {cols} WHERE change_id = ? AND task_key = ?", vals)


def add_message(
    conn: sqlite3.Connection,
    change_id: str,
    sender: str,
    msg_type: str,
    content: str,
    task_key: Optional[str] = None,
    codex_session_id: Optional[str] = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO messages (change_id, task_key, codex_session_id, sender, msg_type, content, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (change_id, task_key, codex_session_id, sender, msg_type, content, utc_now()),
    )
    return int(cur.lastrowid)


def add_decision(
    conn: sqlite3.Connection,
    change_id: str,
    topic: str,
    decision: str,
    task_key: Optional[str] = None,
    decided_by: str = "antigravity",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO decisions (change_id, task_key, topic, decision, decided_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (change_id, task_key, topic, decision, decided_by, utc_now()),
    )
    return int(cur.lastrowid)


def list_decisions(conn: sqlite3.Connection, change_id: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM decisions
        WHERE change_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (change_id, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def set_control(
    conn: sqlite3.Connection,
    change_id: str,
    control_flag: str,
    user_note: Optional[str] = None,
) -> None:
    now = utc_now()
    if user_note is None:
        conn.execute(
            "UPDATE changes SET control_flag = ?, updated_at = ? WHERE change_id = ?",
            (control_flag, now, change_id),
        )
    else:
        conn.execute(
            "UPDATE changes SET control_flag = ?, user_note = ?, updated_at = ? WHERE change_id = ?",
            (control_flag, user_note, now, change_id),
        )


def fetch_status(
    conn: sqlite3.Connection,
    change_id: Optional[str] = None,
    recent_messages: int = 10,
) -> dict[str, Any]:
    if change_id:
        changes = conn.execute("SELECT * FROM changes WHERE change_id = ?", (change_id,)).fetchall()
        tasks = conn.execute(
            "SELECT * FROM tasks WHERE change_id = ? ORDER BY id ASC", (change_id,)
        ).fetchall()
        messages = conn.execute(
            "SELECT * FROM messages WHERE change_id = ? ORDER BY id DESC LIMIT ?",
            (change_id, recent_messages),
        ).fetchall()
        decisions = conn.execute(
            "SELECT * FROM decisions WHERE change_id = ? ORDER BY id DESC LIMIT 10",
            (change_id,),
        ).fetchall()
    else:
        changes = conn.execute("SELECT * FROM changes ORDER BY updated_at DESC").fetchall()
        tasks = conn.execute("SELECT * FROM tasks ORDER BY change_id, id ASC").fetchall()
        messages = conn.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (recent_messages,)
        ).fetchall()
        decisions = conn.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT 20"
        ).fetchall()

    return {
        "changes": [dict(r) for r in changes],
        "tasks": [dict(r) for r in tasks],
        "decisions": [dict(r) for r in reversed(decisions)],
        "recent_messages": [dict(r) for r in reversed(messages)],
    }
