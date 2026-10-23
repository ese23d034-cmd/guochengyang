"""全模块 REST API（标准库，无第三方依赖）。

启动：
    python -m erp.api --db data/yungu_erp.db --port 8001 --seed

主要接口：
    GET  /api/sd/store-inventory
    POST /api/sd/sales
    POST /api/sd/reorder-check
    POST /api/sd/corporate-orders
    POST /api/pp/mrp
    POST /api/mm/purchase-orders
    POST /api/mm/receipts
    POST /api/fi/post
    GET  /api/fi/income-statement
    GET  /api/co/store-pnl
    GET  /api/hcm/coverage
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

from pp import repo, service
from pp.db import connect
from pp.mrp import run_mrp

from . import co, fi, hcm, mm, sd
from .db import FULL_DB_PATH, init_full_db


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
    return 200, {"status": "ok", "module": "ERP", "version": "1.0.0"}


def materials(conn, match, query, body):
    return 200, {"items": [asdict(m) for m in repo.list_materials(conn, _one(query, "type"))]}


def material_bom(conn, match, query, body):
    material_id = match.group(1)
    material = repo.get_material(conn, material_id)
    if material is None:
        raise ApiError(404, f"物料不存在: {material_id}")
    bom = repo.get_bom(conn, material_id)
    if bom is None:
        raise ApiError(404, f"{material_id} 没有生效 BOM")
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
            }
        )
    return 200, {
        "product_id": material_id,
        "product_name": material.material_name,
        "bom_id": bom.bom_id,
        "version": bom.version,
        "base_qty": bom.base_qty,
        "components": components,
    }


def store_inventory(conn, match, query, body):
    return 200, {"items": sd.store_inventory_rows(conn, _one(query, "store_id"))}


def pos_sales(conn, match, query, body):
    return 200, {"items": sd.recent_sales(conn, int(_one(query, "limit") or 20))}


def create_sale(conn, match, query, body):
    items = [(item["product_id"], float(item["qty"]), item.get("unit_price")) for item in body.get("items", [])]
    result = sd.record_pos_sale(
        conn, body["store_id"], body.get("business_date") or date.today().isoformat(), items
    )
    return 201, result


def reorder_check(conn, match, query, body):
    body = body or {}
    result = sd.check_reorder(
        conn,
        request_date=body.get("request_date"),
        required_date=body.get("required_date"),
        store_ids=body.get("store_ids"),
    )
    return 201, {"items": result}


def corporate_orders(conn, match, query, body):
    body = body or {}
    items = [(item["product_id"], float(item["qty"]), float(item["unit_price"])) for item in body.get("items", [])]
    result = sd.create_corporate_order(
        conn,
        body["customer_id"],
        items,
        body["required_date"],
        body.get("order_date"),
        body.get("remark"),
    )
    return 201, result


def requisitions(conn, match, query, body):
    return 200, {"items": repo.list_requisitions(conn, status=_one(query, "status"))}


def deliveries(conn, match, query, body):
    return 200, {"items": sd.deliveries(conn, _one(query, "type"))}


def deliver_store(conn, match, query, body):
    body = body or {}
    return 201, sd.deliver_store_requisition(
        conn, body["requisition_id"], body.get("delivery_date") or date.today().isoformat()
    )


def deliver_corporate(conn, match, query, body):
    body = body or {}
    return 201, sd.deliver_corporate_order(
        conn, body["order_id"], body.get("delivery_date") or date.today().isoformat()
    )


def mrp_run(conn, match, query, body):
    body = body or {}
    result = run_mrp(
        conn,
        run_date=body.get("run_date"),
        requisition_ids=body.get("requisition_ids"),
        horizon_days=int(body.get("horizon_days", 30)),
        run_by=body.get("run_by", "郭晨阳（PP）"),
        extra_demands=body.get("extra_demands"),
    )
    return 201, result


def production_orders(conn, match, query, body):
    return 200, {
        "items": repo.list_production_orders(
            conn, run_id=_one(query, "run_id"), status=_one(query, "status")
        )
    }


def order_detail(conn, match, query, body):
    return 200, service.get_order_detail(conn, match.group(1))


def release(conn, match, query, body):
    return 200, service.release_order(conn, match.group(1), (body or {}).get("on_date"))


def issue(conn, match, query, body):
    return 200, service.issue_components(conn, match.group(1), (body or {}).get("on_date"))


def confirm(conn, match, query, body):
    body = body or {}
    return 200, service.confirm_order(
        conn,
        match.group(1),
        qty_good=body.get("qty_good"),
        qty_scrap=float(body.get("qty_scrap", 0)),
        on_date=body.get("on_date"),
    )


def goods_receipt(conn, match, query, body):
    body = body or {}
    return 201, service.goods_receipt(
        conn,
        match.group(1),
        qty=body.get("qty"),
        production_date=body.get("production_date"),
        on_date=body.get("on_date"),
    )


def purchase_orders(conn, match, query, body):
    return 200, {
        "items": mm.purchase_orders(
            conn, run_id=_one(query, "run_id"), status=_one(query, "status")
        )
    }


def create_purchase_orders(conn, match, query, body):
    body = body or {}
    return 201, {"items": mm.create_purchase_orders(conn, body.get("run_id"), body.get("order_date"))}


def receive_purchase_orders(conn, match, query, body):
    body = body or {}
    return 201, {
        "items": mm.receive_purchase_orders(
            conn, run_id=body.get("run_id"), po_ids=body.get("po_ids"),
            receipt_date=body.get("receipt_date"),
        )
    }


def shelf_life(conn, match, query, body):
    return 200, {
        "items": mm.shelf_life_alerts(
            conn, _one(query, "as_of") or date.today().isoformat(),
            int(_one(query, "days") or 7),
        )
    }


def fi_post(conn, match, query, body):
    return 201, {"vouchers": fi.post_pending_events(conn)}


def fi_opening(conn, match, query, body):
    body = body or {}
    return 201, fi.post_opening_balances(conn, body.get("posting_date") or date.today().isoformat())


def fi_fixed(conn, match, query, body):
    body = body or {}
    return 201, {
        "vouchers": fi.post_store_fixed_costs(
            conn,
            body.get("period_start") or "2026-10-23",
            body.get("period_end") or "2026-10-26",
        )
    }


def fi_vouchers(conn, match, query, body):
    return 200, {"items": fi.vouchers(conn)}


def fi_trial(conn, match, query, body):
    return 200, {"items": fi.trial_balance(conn)}


def fi_income(conn, match, query, body):
    return 200, fi.income_statement(conn)


def co_store(conn, match, query, body):
    return 200, {
        "items": co.store_profitability(
            conn,
            _one(query, "start") or "2026-10-23",
            _one(query, "end") or "2026-10-26",
        )
    }


def co_product(conn, match, query, body):
    return 200, {
        "items": co.product_margin(
            conn,
            _one(query, "start") or "2026-10-23",
            _one(query, "end") or "2026-10-26",
        )
    }


def co_channel(conn, match, query, body):
    return 200, {
        "items": co.channel_mix(
            conn,
            _one(query, "start") or "2026-10-23",
            _one(query, "end") or "2026-10-26",
        )
    }


def hcm_employees(conn, match, query, body):
    return 200, {"items": hcm.employees(conn, _one(query, "department"))}


def hcm_shifts(conn, match, query, body):
    return 200, {
        "items": hcm.shift_roster(
            conn, _one(query, "date") or "2026-10-24", _one(query, "location")
        )
    }


def hcm_coverage(conn, match, query, body):
    return 200, {"items": hcm.coverage(conn, _one(query, "date") or "2026-10-24")}


def hcm_access(conn, match, query, body):
    user_id, module = match.group(1), match.group(2)
    edit = (_one(query, "edit") or "0") in ("1", "true", "yes")
    return 200, {
        "user_id": user_id,
        "module": module.upper(),
        "allowed": hcm.can_access(conn, user_id, module, edit=edit),
        "edit": edit,
    }


ROUTES: list[tuple[str, str, Callable]] = [
    ("GET", r"^/health$", health),
    ("GET", r"^/api/materials$", materials),
    ("GET", r"^/api/materials/([^/]+)/bom$", material_bom),
    ("GET", r"^/api/sd/store-inventory$", store_inventory),
    ("GET", r"^/api/sd/sales$", pos_sales),
    ("POST", r"^/api/sd/sales$", create_sale),
    ("POST", r"^/api/sd/reorder-check$", reorder_check),
    ("GET", r"^/api/sd/requisitions$", requisitions),
    ("POST", r"^/api/sd/corporate-orders$", corporate_orders),
    ("GET", r"^/api/sd/deliveries$", deliveries),
    ("POST", r"^/api/sd/deliveries/store$", deliver_store),
    ("POST", r"^/api/sd/deliveries/corporate$", deliver_corporate),
    ("POST", r"^/api/pp/mrp$", mrp_run),
    ("GET", r"^/api/pp/production-orders$", production_orders),
    ("GET", r"^/api/pp/production-orders/([^/]+)$", order_detail),
    ("POST", r"^/api/pp/production-orders/([^/]+)/release$", release),
    ("POST", r"^/api/pp/production-orders/([^/]+)/issue$", issue),
    ("POST", r"^/api/pp/production-orders/([^/]+)/confirm$", confirm),
    ("POST", r"^/api/pp/production-orders/([^/]+)/goods-receipt$", goods_receipt),
    ("GET", r"^/api/mm/purchase-orders$", purchase_orders),
    ("POST", r"^/api/mm/purchase-orders$", create_purchase_orders),
    ("POST", r"^/api/mm/receipts$", receive_purchase_orders),
    ("GET", r"^/api/mm/shelf-life$", shelf_life),
    ("POST", r"^/api/fi/post$", fi_post),
    ("POST", r"^/api/fi/opening$", fi_opening),
    ("POST", r"^/api/fi/fixed-costs$", fi_fixed),
    ("GET", r"^/api/fi/vouchers$", fi_vouchers),
    ("GET", r"^/api/fi/trial-balance$", fi_trial),
    ("GET", r"^/api/fi/income-statement$", fi_income),
    ("GET", r"^/api/co/store-pnl$", co_store),
    ("GET", r"^/api/co/product-margin$", co_product),
    ("GET", r"^/api/co/channel-mix$", co_channel),
    ("GET", r"^/api/hcm/employees$", hcm_employees),
    ("GET", r"^/api/hcm/shifts$", hcm_shifts),
    ("GET", r"^/api/hcm/coverage$", hcm_coverage),
    ("GET", r"^/api/hcm/access/([^/]+)/([^/]+)$", hcm_access),
]
COMPILED = [(method, re.compile(pattern), func) for method, pattern, func in ROUTES]


class Handler(BaseHTTPRequestHandler):
    db_path = str(FULL_DB_PATH)
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
        except service.NotFoundError as exc:
            self._send(404, {"error": str(exc)})
        except service.InsufficientStockError as exc:
            self._send(409, {"error": str(exc), "shortages": exc.shortages})
        except (service.ServiceError, sd.SdError, mm.MmError, fi.FiError) as exc:
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
        sys.stderr.write(f"[ERP-API] {self.address_string()} {fmt % args}\n")


def serve(db_path: str | Path, host: str = "127.0.0.1", port: int = 8001, seed: bool = False) -> None:
    path = Path(db_path)
    if seed or not path.exists():
        conn = init_full_db(path, reset=True)
        conn.close()
        print(f"已初始化全模块数据库: {path.resolve()}")
    Handler.db_path = str(path)
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    print(f"ERP API 已启动: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="云谷鲜米粉 ERP 全模块 API")
    parser.add_argument("--db", default=str(FULL_DB_PATH))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args()
    serve(args.db, args.host, args.port, args.seed)


if __name__ == "__main__":
    main()
