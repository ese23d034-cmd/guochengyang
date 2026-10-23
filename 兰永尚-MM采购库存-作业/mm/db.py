"""数据库连接、建表、事务、单号与事件。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .util import json_dumps, now_iso

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
DEFAULT_DB_PATH = PROJECT_DIR / "data" / "yungu_mm.db"
SCHEMA_PATH = PACKAGE_DIR / "schema.sql"


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def reset_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    path = Path(db_path)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def next_number(conn: sqlite3.Connection, scope: str) -> str:
    row = conn.execute("SELECT next_no FROM number_range WHERE scope = ?", (scope,)).fetchone()
    if row is None:
        seq = 1
        conn.execute("INSERT INTO number_range(scope, next_no) VALUES (?, 2)", (scope,))
    else:
        seq = int(row["next_no"])
        conn.execute("UPDATE number_range SET next_no = ? WHERE scope = ?", (seq + 1, scope))
    return f"{scope}-{seq:04d}"


def emit_event(conn: sqlite3.Connection, event_type: str, aggregate_id: str, payload: dict) -> int:
    cur = conn.execute(
        """INSERT INTO integration_event(event_type, aggregate_id, payload, status, created_at)
           VALUES (?, ?, ?, 'PENDING', ?)""",
        (event_type, aggregate_id, json_dumps(payload), now_iso()),
    )
    return int(cur.lastrowid)
