"""命令行入口：python -m pp.cli {demo|init|mrp|serve}"""
from __future__ import annotations

import argparse
import sys

from . import api, demo
from .db import DEFAULT_DB_PATH, connect
from .mrp import MrpError, run_mrp
from .seed import RUN_DATE, init_and_seed


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="云谷鲜米粉 PP 模块")
    sub = parser.add_subparsers(dest="command", required=True)

    p_demo = sub.add_parser("demo", help="完整演示（会重建演示数据库）")
    p_demo.add_argument("--db", default=str(DEFAULT_DB_PATH))

    p_init = sub.add_parser("init", help="初始化数据库与主数据")
    p_init.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p_init.add_argument("--reset", action="store_true")

    p_mrp = sub.add_parser("mrp", help="对现有数据库运行一次 MRP")
    p_mrp.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p_mrp.add_argument("--date", default=RUN_DATE)

    p_serve = sub.add_parser("serve", help="启动 REST API")
    p_serve.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--seed", action="store_true")

    args = parser.parse_args()
    if args.command == "demo":
        demo.run(args.db)
    elif args.command == "init":
        conn = init_and_seed(args.db, reset=args.reset)
        print(f"数据库已初始化：{args.db}")
        conn.close()
    elif args.command == "mrp":
        conn = connect(args.db)
        try:
            result = run_mrp(conn, run_date=args.date)
        except MrpError as exc:
            print(f"MRP 未运行：{exc}")
            return
        finally:
            conn.close()
        print(result["stats"])
    elif args.command == "serve":
        api.serve(args.db, args.host, args.port, args.seed)


if __name__ == "__main__":
    main()
