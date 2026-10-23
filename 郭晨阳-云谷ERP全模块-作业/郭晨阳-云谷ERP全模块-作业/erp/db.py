"""全模块数据库初始化：PP 基础表 + SD/MM/FI/CO/HCM 扩展表 + 全量演示数据。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from pp.db import DEFAULT_DB_PATH, connect, init_db, reset_db
from pp.seed import seed_all

EXTRA_SCHEMA_PATH = Path(__file__).with_name("schema_extra.sql")
FULL_DB_PATH = DEFAULT_DB_PATH.with_name("yungu_erp.db")


def ensure_extra_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(EXTRA_SCHEMA_PATH.read_text(encoding="utf-8"))
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(store)")}
    if "store_type" not in columns:
        conn.execute("ALTER TABLE store ADD COLUMN store_type TEXT NOT NULL DEFAULT 'STORE'")
    conn.commit()


def init_full_db(db_path: str | Path = FULL_DB_PATH, reset: bool = True) -> sqlite3.Connection:
    path = Path(db_path)
    if reset:
        reset_db(path)
    conn = connect(path)
    init_db(conn)
    ensure_extra_schema(conn)
    seed_all(conn)          # PP 主数据 + 期初批次（会清空 PP 基础表）
    from .seed_full import seed_extra

    seed_extra(conn)        # 全模块主数据 + 门店库存 + HCM 等
    return conn
