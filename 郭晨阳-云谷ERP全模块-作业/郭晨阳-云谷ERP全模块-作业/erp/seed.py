"""初始化全模块演示数据库。

    python -m erp.seed
    python -m erp.seed --db data/yungu_erp.db
"""
from __future__ import annotations

import argparse
import sys

from .db import FULL_DB_PATH, init_full_db


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="初始化云谷鲜米粉全模块 ERP 数据库")
    parser.add_argument("--db", default=str(FULL_DB_PATH))
    args = parser.parse_args()
    conn = init_full_db(args.db, reset=True)
    print(f"数据库已初始化: {args.db}")
    for table in ("store", "supplier", "employee", "store_inventory", "shift"):
        count = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        print(f"  {table}: {count}")
    conn.close()


if __name__ == "__main__":
    main()
