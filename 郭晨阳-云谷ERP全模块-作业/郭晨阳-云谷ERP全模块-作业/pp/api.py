"""PP 模块 REST API（仅使用 Python 标准库，无需安装框架）。

启动：
    python -m pp.api --db data/yungu_pp.db --port 8000

示例：
    curl http://127.0.0.1:8000/api/materials/FG-001/bom
    curl -X POST http://127.0.0.1:8000/api/mrp/run -H "Content-Type: application/json" \
         -d "{\"run_date\":\"2026-10-23\"}"
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

from . import costing, repo, service, stubs
from .db import DEFAULT_DB_PATH, connect
from .mrp import MrpError, run_mrp
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


def _material_dict(conn: sqlite3.Connection, material) -> dict:
    data = asdict(material)
    data["stock"] = round(repo.available_qty(conn, material.material_id, date.today()), 3)
    return data


# ---------------------------------------------------------------------------
# 路由处理函数
# ---------------------------------------------------------------------------
def health(conn, match, query, body):
    return 200, {"status": "ok", "module": "PP", "owner": "郭晨阳", "version": "1.0.0"}


def list_materials(conn, match, query, body):
    material_type = _one(query, "type")
    materials = repo.list_materials(conn, material_type=material_type)
    return 200, {"items": [_material_dict(conn, m) for m in materials]}


def get_material(conn, match, query, body):
    material = repo.get_material(conn, match.group(1))
    if material is None:
        raise ApiError(404, f"物料不存在: {match.group(1)}")
    data = _material_dict(conn, material)
    data["bom"] = bool(repo.get_bom(conn, material.material_id))
    data["routing"] = [asdict(op) for op in repo.get_routing(conn, material.material_id)]
    return 200, data


def get_material_bom(conn, match, query, body):
    product_id = match.group(1)
    material = repo.get_material(conn, product_id)
    if material is None:
        raise ApiError(404, f"物料不存在: {product_id}")
    on_date = _one(query, "on_date")
    bom = repo.get_bom(conn, product_id, on_date)
    if bom is None:
        raise ApiError(404, f"{product_id} 没有生效 BOM")
    components = []
    for item in bom.components:
        child = repo.get_material(conn, item.component_id)
        components.append(
            {
                "line_no": item.line_no,
                "component_id": item.component_id,
                "component_name": child.material_name if child else None,
                "unit": child.base_unit if child else None,
                "qty_per_base": item.qty_per_base,
                "scrap_rate": item.scrap_rate,
                "qty_per_unit": round(item.qty_per_base / bom.base_qty * (1 + item.scrap_rate), 6),
            }
        )
    return 200, {
        "product_id": product_id,
        "product_name": material.material_name,
        "bom_id": bom.bom_id,
        "version": bom.version,
        "base_qty": bom.base_qty,
        "components": components,
    }


def get_material_cost(conn, match, query, body):
    material = repo.get_material(conn, match.group(1))
    if material is None:
        raise ApiError(404, f"物料不存在: {match.group(1)}")
    return 200, {
        "material_id": material.material_id,
        "material_name": material.material_name,
        "standard_cost": round(material.standard_cost, 4),
        "rolled_up_cost": round(costing.rollup_cost(conn, material.material_id), 4),
    }


def list_requisitions(conn, match, query, body):
    rows = repo.list_requisitions(conn, status=_one(query, "status"))
    return 200, {"items": rows}


def create_requisition(conn, match, query, body):
    store_id = body.get("store_id")
    required_date = body.get("required_date")
    raw_items = body.get("items") or []
    if not store_id or not required_date:
        raise ApiError(400, "store_id 与 required_date 为必填")
    items = []
    for item in raw_items:
        try:
            items.append((item["product_id"], float(item["qty"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiError(400, f"items 格式错误: {exc}") from exc
    requisition_id = service.submit_requisition(
        conn,
        store_id=store_id,
        items=items,
        required_date=required_date,
        request_date=body.get("request_date"),
        remark=body.get("remark"),
    )
    return 201, repo.get_requisition(conn, requisition_id)


def run_mrp_endpoint(conn, match, query, body):
    body = body or {}
    result = run_mrp(
        conn,
        run_date=body.get("run_date"),
        requisition_ids=body.get("requisition_ids"),
        horizon_days=int(body.get("horizon_days", 30)),
        run_by=body.get("run_by", "郭晨阳（PP）"),
    )
    return 201, result


def list_mrp_runs(conn, match, query, body):
    return 200, {"items": repo.list_mrp_runs(conn)}


def get_mrp_run(conn, match, query, body):
    run_id = match.group(1)
    run = repo.get_mrp_run(conn, run_id)
    if run is None:
        raise ApiError(404, f"MRP 运行不存在: {run_id}")
    run["explosion"] = repo.list_explosion(conn, run_id)
    run["exceptions"] = repo.list_exceptions(conn, run_id)
    run["production_orders"] = [
        service.get_order_detail(conn, order["order_id"])
        for order in repo.list_production_orders(conn, run_id=run_id)
    ]
    run["purchase_requisitions"] = repo.list_purchase_requisitions(conn, run_id=run_id)
    return 200, run


def list_orders(conn, match, query, body):
    orders = repo.list_production_orders(
        conn, run_id=_one(query, "run_id"), status=_one(query, "status")
    )
    return 200, {"items": orders}


def get_order(conn, match, query, body):
    return 200, service.get_order_detail(conn, match.group(1))


def release_order(conn, match, query, body):
    detail = service.release_order(conn, match.group(1), (body or {}).get("on_date"))
    return 200, detail


def issue_order(conn, match, query, body):
    detail = service.issue_components(conn, match.group(1), (body or {}).get("on_date"))
    return 200, detail


def confirm_order(conn, match, query, body):
    body = body or {}
    detail = service.confirm_order(
        conn,
        match.group(1),
        qty_good=body.get("qty_good"),
        qty_scrap=float(body.get("qty_scrap", 0)),
        confirmed_by=body.get("confirmed_by", "郭晨阳"),
        on_date=body.get("on_date"),
    )
    return 200, detail


def goods_receipt(conn, match, query, body):
    body = body or {}
    result = service.goods_receipt(
        conn,
        match.group(1),
        qty=body.get("qty"),
        warehouse_id=body.get("warehouse_id"),
        production_date=body.get("production_date"),
        on_date=body.get("on_date"),
    )
    return 201, result


def list_purchase_requisitions(conn, match, query, body):
    rows = repo.list_purchase_requisitions(
        conn, run_id=_one(query, "run_id"), status=_one(query, "status")
    )
    return 200, {"items": rows}


def inventory(conn, match, query, body):
    return 200, {"items": repo.inventory_snapshot(conn)}


def events(conn, match, query, body):
    return 200, {
        "items": repo.list_events(conn, status=_one(query, "status"), event_type=_one(query, "type"))
    }


def fi_post(conn, match, query, body):
    vouchers = stubs.post_pending_events(conn)
    return 201, {"posted": len(vouchers), "vouchers": vouchers}


def fi_vouchers(conn, match, query, body):
    return 200, {"items": stubs.list_vouchers(conn), "trial_balance": stubs.trial_balance(conn)}


def mm_receive(conn, match, query, body):
    body = body or {}
    received = stubs.receive_purchase_requisitions(
        conn, run_id=body.get("run_id"), on_date=body.get("on_date")
    )
    return 201, {"received": len(received), "items": received}


def list_stores(conn, match, query, body):
    rows = conn.execute("SELECT * FROM store ORDER BY store_id").fetchall()
    return 200, {"items": [dict(row) for row in rows]}


def list_work_centers(conn, match, query, body):
    return 200, {"items": [asdict(wc) for wc in repo.list_work_centers(conn)]}


ROUTES: list[tuple[str, str, Callable]] = [
    ("GET", r"^/health$", health),
    ("GET", r"^/api/materials$", list_materials),
    ("GET", r"^/api/materials/([^/]+)/bom$", get_material_bom),
    ("GET", r"^/api/materials/([^/]+)/cost$", get_material_cost),
    ("GET", r"^/api/materials/([^/]+)$", get_material),
    ("GET", r"^/api/stores$", list_stores),
    ("GET", r"^/api/work-centers$", list_work_centers),
    ("GET", r"^/api/requisitions$", list_requisitions),
    ("POST", r"^/api/requisitions$", create_requisition),
    ("POST", r"^/api/mrp/run$", run_mrp_endpoint),
    ("GET", r"^/api/mrp/runs$", list_mrp_runs),
    ("GET", r"^/api/mrp/runs/([^/]+)$", get_mrp_run),
    ("GET", r"^/api/production-orders$", list_orders),
    ("GET", r"^/api/production-orders/([^/]+)$", get_order),
    ("POST", r"^/api/production-orders/([^/]+)/release$", release_order),
    ("POST", r"^/api/production-orders/([^/]+)/issue$", issue_order),
    ("POST", r"^/api/production-orders/([^/]+)/confirm$", confirm_order),
    ("POST", r"^/api/production-orders/([^/]+)/goods-receipt$", goods_receipt),
    ("GET", r"^/api/purchase-requisitions$", list_purchase_requisitions),
    ("GET", r"^/api/inventory$", inventory),
    ("GET", r"^/api/events$", events),
    ("POST", r"^/api/fi/post$", fi_post),
    ("GET", r"^/api/fi/vouchers$", fi_vouchers),
    ("POST", r"^/api/demo/mm-receive$", mm_receive),
]
COMPILED_ROUTES = [(method, re.compile(pattern), func) for method, pattern, func in ROUTES]


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
            for route_method, pattern, func in COMPILED_ROUTES:
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
        except service.NotFoundError as exc:
            self._send(404, {"error": str(exc)})
        except service.InsufficientStockError as exc:
            self._send(409, {"error": str(exc), "shortages": exc.shortages})
        except (service.ServiceError, MrpError) as exc:
            self._send(400, {"error": str(exc)})
        except sqlite3.IntegrityError as exc:
            self._send(400, {"error": f"数据校验失败: {exc}"})
        except (ValueError, TypeError) as exc:
            self._send(400, {"error": f"参数错误: {exc}"})
        except Exception as exc:  # pragma: no cover - 兜底，避免进程崩溃
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
        if not isinstance(data, dict):
            raise ApiError(400, "请求体必须是 JSON 对象")
        return data

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
        sys.stderr.write(f"[PP-API] {self.address_string()} {fmt % args}\n")


def serve(db_path: str | Path, host: str = "127.0.0.1", port: int = 8000, seed: bool = False) -> None:
    path = Path(db_path)
    if seed or not path.exists():
        conn = init_and_seed(path, reset=True)
        conn.close()
        print(f"已初始化数据库并写入演示数据: {path.resolve()}")
    Handler.db_path = str(path)
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    print(f"PP API 已启动: http://{host}:{port}")
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="云谷鲜米粉 PP 模块 REST API")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--seed", action="store_true", help="数据库不存在时写入演示数据")
    args = parser.parse_args()
    serve(args.db, args.host, args.port, args.seed)


if __name__ == "__main__":
    main()
