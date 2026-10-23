"""MM 模块 REST API（标准库，无第三方依赖）。

启动：
    python -m mm.api --db data/yungu_mm.db --port 8100 --seed

关键接口：
    GET  /api/materials/FG-001/bom       按产品 ID 查 BOM
    GET  /api/purchase-requisitions      PP 给 MM 的采购申请
    POST /api/purchase-orders            采购申请转采购订单
    POST /api/receipts                   采购收货/批次建档
    GET  /api/inventory                  批次库存
    GET  /api/shelf-life                 效期预警
    POST /api/goods-issues               生产发料（FEFO）
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import asdict
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import inventory, master_data, procurement, repo
from .db import DEFAULT_DB_PATH, connect
from .seed import init_and_seed


class ApiError(Exception):
    def __init__(self, status: int, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail


def _one(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def health(conn, match, query, body):
    return 200, {"status": "ok", "module": "MM", "owner": "兰永尚", "version": "1.0.0"}


def materials(conn, match, query, body):
    material_type = _one(query, "type")
    return 200, {"items": [asdict(m) for m in repo.list_materials(conn, material_type)]}


def material(conn, match, query, body):
    return 200, master_data.material_card(conn, match.group(1))


def bom(conn, match, query, body):
    return 200, master_data.query_bom(conn, match.group(1), _one(query, "on_date"))


def where_used(conn, match, query, body):
    return 200, {"items": master_data.where_used(conn, match.group(1))}


def master_summary(conn, match, query, body):
    return 200, master_data.master_summary(conn)


def suppliers(conn, match, query, body):
    return 200, {"items": [asdict(s) for s in repo.list_suppliers(conn)]}


def warehouses(conn, match, query, body):
    rows = conn.execute("SELECT * FROM warehouse ORDER BY warehouse_id").fetchall()
    return 200, {"items": [dict(row) for row in rows]}


def requisitions(conn, match, query, body):
    return 200, {
        "items": repo.list_requisitions(
            conn, status=_one(query, "status"), run_id=_one(query, "run_id")
        )
    }


def purchase_orders(conn, match, query, body):
    return 200, {
        "items": repo.list_purchase_orders(
            conn, status=_one(query, "status"), run_id=_one(query, "run_id")
        )
    }


def create_purchase_orders(conn, match, query, body):
    body = body or {}
    created = procurement.create_purchase_orders(
        conn, body.get("run_id"), body.get("order_date")
    )
    return 201, {"items": created}


def purchase_order_detail(conn, match, query, body):
    return 200, procurement.purchase_order_detail(conn, match.group(1))


def receipts(conn, match, query, body):
    return 200, {"items": repo.list_receipts(conn, _one(query, "run_id"))}


def receive(conn, match, query, body):
    body = body or {}
    result = procurement.receive_purchase_orders(
        conn,
        run_id=body.get("run_id"),
        po_ids=body.get("po_ids"),
        receipt_date=body.get("receipt_date"),
    )
    return 201, {"items": result}


def stock(conn, match, query, body):
    return 200, {"items": inventory.inventory_snapshot(conn)}


def stock_summary(conn, match, query, body):
    return 200, {
        "items": inventory.stock_summary(conn),
        "value": sum(row["value"] for row in inventory.stock_summary(conn)),
    }


def warehouses_stock(conn, match, query, body):
    return 200, {"items": inventory.warehouse_stock(conn)}


def shelf_life(conn, match, query, body):
    return 200, {
        "items": inventory.shelf_life_alerts(
            conn, _one(query, "as_of") or date.today().isoformat(),
            int(_one(query, "days") or 7),
        )
    }


def expired(conn, match, query, body):
    return 200, {
        "items": inventory.expired_stock(
            conn, _one(query, "as_of") or date.today().isoformat()
        )
    }


def goods_issues(conn, match, query, body):
    return 200, {"items": repo.list_issues(conn)}


def goods_issue(conn, match, query, body):
    body = body or {}
    lines = [
        (line["material_id"], float(line["qty"]))
        for line in body.get("lines", [])
    ]
    result = inventory.issue_to_production(
        conn,
        body["order_id"],
        lines,
        body.get("issue_date") or date.today().isoformat(),
    )
    return 201, result


def events(conn, match, query, body):
    return 200, {"items": repo.list_events(conn, status=_one(query, "status"))}


def scorecard(conn, match, query, body):
    return 200, {"items": repo.supplier_scorecard(conn)}


ROUTES: list[tuple[str, str, Callable]] = [
    ("GET", r"^/health$", health),
    ("GET", r"^/api/master-summary$", master_summary),
    ("GET", r"^/api/materials$", materials),
    ("GET", r"^/api/materials/([^/]+)/bom$", bom),
    ("GET", r"^/api/materials/([^/]+)$", material),
    ("GET", r"^/api/where-used/([^/]+)$", where_used),
    ("GET", r"^/api/suppliers$", suppliers),
    ("GET", r"^/api/suppliers/scorecard$", scorecard),
    ("GET", r"^/api/warehouses$", warehouses),
    ("GET", r"^/api/purchase-requisitions$", requisitions),
    ("GET", r"^/api/purchase-orders$", purchase_orders),
    ("POST", r"^/api/purchase-orders$", create_purchase_orders),
    ("GET", r"^/api/purchase-orders/([^/]+)$", purchase_order_detail),
    ("GET", r"^/api/receipts$", receipts),
    ("POST", r"^/api/receipts$", receive),
    ("GET", r"^/api/inventory$", stock),
    ("GET", r"^/api/inventory/summary$", stock_summary),
    ("GET", r"^/api/warehouse-stock$", warehouses_stock),
    ("GET", r"^/api/shelf-life$", shelf_life),
    ("GET", r"^/api/expired$", expired),
    ("GET", r"^/api/goods-issues$", goods_issues),
    ("POST", r"^/api/goods-issues$", goods_issue),
    ("GET", r"^/api/events$", events),
]
COMPILED = [(method, re.compile(pattern), func) for method, pattern, func in ROUTES]


class Handler(BaseHTTPRequestHandler):
    db_path = str(DEFAULT_DB_PATH)
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle(self, method: str) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)
            body = self._read_json()
            for route_method, pattern, func in COMPILED:
                match = pattern.match(path)
                if route_method != method or not match:
                    continue
                conn = connect(self.db_path)
                try:
                    status, payload = func(conn, match, query, body)
                finally:
                    conn.close()
                self._send(status, payload)
                return
            raise ApiError(404, f"接口不存在: {method} {path}")
        except ApiError as exc:
            self._send(exc.status, {"error": exc.message, "detail": exc.detail})
        except (master_data.MasterDataError, procurement.ProcurementError, inventory.InventoryError) as exc:
            self._send(400, {"error": str(exc)})
        except sqlite3.IntegrityError as exc:
            self._send(400, {"error": f"数据校验失败: {exc}"})
        except (ValueError, TypeError, KeyError) as exc:
            self._send(400, {"error": f"参数错误: {exc}"})
        except Exception as exc:  # pragma: no cover
            self._send(500, {"error": f"服务器内部错误: {exc}"})

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, f"请求体不是合法 JSON: {exc}") from exc
        return data if isinstance(data, dict) else {"value": data}

    def _send(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"[MM-API] {self.address_string()} {fmt % args}\n")


def serve(db_path: str | Path, host: str = "127.0.0.1", port: int = 8100, seed: bool = False) -> None:
    path = Path(db_path)
    if seed or not path.exists():
        conn = init_and_seed(path, reset=True)
        conn.close()
        print(f"已初始化 MM 数据库: {path.resolve()}")
    Handler.db_path = str(path)
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    print(f"MM API 已启动: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="云谷鲜米粉 MM 模块 API")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args()
    serve(args.db, args.host, args.port, args.seed)


if __name__ == "__main__":
    main()
