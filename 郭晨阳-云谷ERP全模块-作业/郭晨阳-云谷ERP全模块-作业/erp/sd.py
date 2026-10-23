"""SD 销售与分销：POS 零售、企业团餐订单、门店补货、工厂到门店配送。"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from typing import Sequence

from pp import repo, service
from pp.db import emit_event, next_number, transaction
from pp.util import QTY_EPS, now_iso, round_money, round_qty, to_date

from .inventory import (
    InventoryError,
    consume_fefo,
    store_inventory_add,
    store_inventory_consume,
    store_inventory_get,
    store_inventory_rows,
)

STORE_CORP = "ST-CORP"


class SdError(Exception):
    pass


# ---------------------------------------------------------------------------
# POS 零售
# ---------------------------------------------------------------------------
def record_pos_sale(
    conn: sqlite3.Connection,
    store_id: str,
    business_date: str | date,
    items: Sequence[tuple[str, float, float | None]],
    channel: str = "POS",
) -> dict:
    """items: [(product_id, qty, unit_price|None)]；价格为空时取成品售价。"""
    day = to_date(business_date) or date.today()
    if not items:
        raise SdError("POS 销售至少需要一行商品")
    store = conn.execute("SELECT * FROM store WHERE store_id = ?", (store_id,)).fetchone()
    if store is None or store["store_type"] != "STORE":
        raise SdError(f"门店不存在或不是直营门店: {store_id}")

    lines: list[dict] = []
    total_revenue = 0.0
    total_cost = 0.0
    for product_id, qty, price in items:
        qty = round_qty(float(qty))
        if qty <= 0:
            raise SdError(f"{product_id} 销售数量必须大于 0")
        material = repo.get_material(conn, product_id)
        if material is None or material.material_type != "FG":
            raise SdError(f"{product_id} 不是可售成品")
        stock = store_inventory_get(conn, store_id, product_id)
        if stock is None or float(stock["qty_on_hand"]) + QTY_EPS < qty:
            available = float(stock["qty_on_hand"]) if stock else 0.0
            raise SdError(f"{store_id} 的 {material.material_name} 门店库存不足：可用 {available}，需要 {qty}")
        unit_price = float(price) if price is not None else float(material.list_price or 0)
        unit_cost = float(stock["avg_cost"])
        revenue = round_money(qty * unit_price)
        cogs = round_money(qty * unit_cost)
        lines.append(
            {
                "product_id": product_id,
                "product_name": material.material_name,
                "unit": material.base_unit,
                "qty": qty,
                "unit_price": unit_price,
                "unit_cost": unit_cost,
                "revenue": revenue,
                "cogs": cogs,
            }
        )
        total_revenue += revenue
        total_cost += cogs

    with transaction(conn):
        sale_id = next_number(conn, f"POS-{day:%Y%m%d}")
        conn.execute(
            """INSERT INTO pos_sale(sale_id, store_id, business_date, channel,
                                    total_amount, total_cost, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'POSTED', ?)""",
            (sale_id, store_id, day.isoformat(), channel,
             round_money(total_revenue), round_money(total_cost), now_iso()),
        )
        for index, line in enumerate(lines, start=10):
            conn.execute(
                """INSERT INTO pos_sale_item
                   (sale_id, line_no, product_id, qty, unit_price, unit_cost, amount)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (sale_id, index, line["product_id"], line["qty"], line["unit_price"],
                 line["unit_cost"], line["revenue"]),
            )
            conn.execute(
                """INSERT INTO sales_fact
                   (sale_id, sale_date, channel, store_id, customer_id, product_id,
                    qty, unit_price, revenue, unit_cost, cogs)
                   VALUES (?, ?, 'POS', ?, NULL, ?, ?, ?, ?, ?, ?)""",
                (sale_id, day.isoformat(), store_id, line["product_id"], line["qty"],
                 line["unit_price"], line["revenue"], line["unit_cost"], line["cogs"]),
            )
            store_inventory_consume(conn, store_id, line["product_id"], line["qty"])

        emit_event(
            conn,
            "POS_SALE",
            sale_id,
            {
                "sale_id": sale_id,
                "store_id": store_id,
                "business_date": day.isoformat(),
                "posting_date": day.isoformat(),
                "amount": round_money(total_revenue),
                "cost_amount": round_money(total_cost),
                "lines": lines,
            },
        )
    return {
        "sale_id": sale_id,
        "store_id": store_id,
        "store_name": store["store_name"],
        "business_date": day.isoformat(),
        "lines": lines,
        "total_amount": round_money(total_revenue),
        "total_cost": round_money(total_cost),
        "gross_profit": round_money(total_revenue - total_cost),
    }


# ---------------------------------------------------------------------------
# 企业团餐订单
# ---------------------------------------------------------------------------
def create_corporate_order(
    conn: sqlite3.Connection,
    customer_id: str,
    items: Sequence[tuple[str, float, float]],
    required_date: str | date,
    order_date: str | date | None = None,
    remark: str | None = None,
) -> dict:
    order_day = to_date(order_date) or date.today()
    need_day = to_date(required_date)
    if need_day is None or need_day < order_day:
        raise SdError("团餐订单需求日期不能早于下单日期")
    customer = conn.execute("SELECT * FROM customer WHERE customer_id = ?", (customer_id,)).fetchone()
    if customer is None:
        raise SdError(f"客户不存在: {customer_id}")
    if not items:
        raise SdError("团餐订单至少需要一行商品")

    normalized: list[dict] = []
    total = 0.0
    for product_id, qty, price in items:
        material = repo.get_material(conn, product_id)
        if material is None or material.material_type != "FG":
            raise SdError(f"{product_id} 不是可售成品")
        qty = round_qty(float(qty))
        price = float(price)
        if qty <= 0 or price < 0:
            raise SdError("团餐数量必须大于 0，价格不能为负")
        amount = round_money(qty * price)
        normalized.append({"product_id": product_id, "qty": qty, "unit_price": price, "amount": amount})
        total += amount

    with transaction(conn):
        order_id = next_number(conn, f"SO-{order_day:%Y%m%d}")
        conn.execute(
            """INSERT INTO sales_order
               (order_id, customer_id, order_date, required_date, status, total_amount, remark, created_at)
               VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?)""",
            (order_id, customer_id, order_day.isoformat(), need_day.isoformat(),
             round_money(total), remark, now_iso()),
        )
        for index, line in enumerate(normalized, start=10):
            conn.execute(
                """INSERT INTO sales_order_item(order_id, line_no, product_id, qty, unit_price)
                   VALUES (?, ?, ?, ?, ?)""",
                (order_id, index, line["product_id"], line["qty"], line["unit_price"]),
            )
        emit_event(
            conn,
            "CORPORATE_ORDER_CREATED",
            order_id,
            {
                "order_id": order_id,
                "customer_id": customer_id,
                "order_date": order_day.isoformat(),
                "required_date": need_day.isoformat(),
                "amount": round_money(total),
            },
        )
    return get_corporate_order(conn, order_id)


def get_corporate_order(conn: sqlite3.Connection, order_id: str) -> dict:
    row = conn.execute(
        """SELECT o.*, c.customer_name
           FROM sales_order o JOIN customer c ON c.customer_id = o.customer_id
           WHERE o.order_id = ?""",
        (order_id,),
    ).fetchone()
    if row is None:
        raise SdError(f"团餐订单不存在: {order_id}")
    result = dict(row)
    result["items"] = [
        dict(item)
        for item in conn.execute(
            """SELECT i.*, m.material_name, m.base_unit
               FROM sales_order_item i JOIN material m ON m.material_id = i.product_id
               WHERE i.order_id = ? ORDER BY i.line_no""",
            (order_id,),
        )
    ]
    return result


# ---------------------------------------------------------------------------
# 门店补货：库存低于补货点自动生成要货单
# ---------------------------------------------------------------------------
def check_reorder(
    conn: sqlite3.Connection,
    request_date: str | date | None = None,
    required_date: str | date | None = None,
    store_ids: Sequence[str] | None = None,
) -> list[dict]:
    request_day = to_date(request_date) or date.today()
    required_day = to_date(required_date) or (request_day + timedelta(days=3))
    rows = store_inventory_rows(conn)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["store_type"] == "CORP":
            continue
        if store_ids and row["store_id"] not in store_ids:
            continue
        if float(row["qty_on_hand"]) >= float(row["reorder_point"]):
            continue
        order_qty = round_qty(float(row["target_stock"]) - float(row["qty_on_hand"]))
        if order_qty <= QTY_EPS:
            continue
        grouped[row["store_id"]].append(
            {"product_id": row["product_id"], "qty": order_qty, "reason": "低于补货点"}
        )

    result: list[dict] = []
    for store_id, lines in grouped.items():
        # 同一门店同一需求日已有未完成要货单则不重复生成
        exists = conn.execute(
            """SELECT requisition_id FROM store_requisition
               WHERE store_id = ? AND required_date = ?
                 AND status IN ('SUBMITTED', 'PLANNED') LIMIT 1""",
            (store_id, required_day.isoformat()),
        ).fetchone()
        if exists:
            continue
        requisition_id = service.submit_requisition(
            conn,
            store_id=store_id,
            items=[(line["product_id"], line["qty"]) for line in lines],
            required_date=required_day,
            request_date=request_day,
            remark="门店库存低于补货点，自动生成",
        )
        result.append({"requisition_id": requisition_id, "store_id": store_id, "items": lines})
    return result


# ---------------------------------------------------------------------------
# 配送：工厂成品仓 -> 门店 / 企业客户
# ---------------------------------------------------------------------------
def deliver_store_requisition(
    conn: sqlite3.Connection,
    requisition_id: str,
    delivery_date: str | date,
) -> dict:
    day = to_date(delivery_date) or date.today()
    req = repo.get_requisition(conn, requisition_id)
    if req is None:
        raise SdError(f"要货单不存在: {requisition_id}")
    if req["status"] != "PLANNED":
        raise SdError(f"要货单 {requisition_id} 状态为 {req['status']}，只有 PLANNED 可以配送")

    with transaction(conn):
        delivery_id = next_number(conn, f"DL-{day:%Y%m%d}")
        total_qty = 0.0
        total_cost = 0.0
        lines: list[dict] = []
        for item in req["items"]:
            material = repo.get_material(conn, item["product_id"])
            if material is None:
                raise SdError(f"成品不存在: {item['product_id']}")
            consumed = consume_fefo(
                conn,
                item["product_id"],
                float(item["qty"]),
                material.default_warehouse_id or "WH-FG",
                day,
                "STORE_DELIVERY",
                delivery_id,
            )
            unit_cost = consumed["amount"] / consumed["qty"] if consumed["qty"] else 0.0
            store_inventory_add(conn, req["store_id"], item["product_id"], consumed["qty"], unit_cost)
            total_qty += consumed["qty"]
            total_cost += consumed["amount"]
            lines.append(
                {
                    "product_id": item["product_id"],
                    "product_name": item["material_name"],
                    "qty": consumed["qty"],
                    "unit": item["base_unit"],
                    "unit_cost": unit_cost,
                    "amount": consumed["amount"],
                }
            )
        conn.execute(
            """INSERT INTO outbound_delivery
               (delivery_id, delivery_type, store_id, customer_id, delivery_date,
                status, total_qty, total_cost, reference_id, created_at)
               VALUES (?, 'STORE', ?, NULL, ?, 'POSTED', ?, ?, ?, ?)""",
            (delivery_id, req["store_id"], day.isoformat(), round_qty(total_qty),
             round_money(total_cost), requisition_id, now_iso()),
        )
        for index, line in enumerate(lines, start=10):
            conn.execute(
                """INSERT INTO outbound_delivery_item
                   (delivery_id, line_no, product_id, qty, batch_id, unit_cost, amount)
                   VALUES (?, ?, ?, ?, NULL, ?, ?)""",
                (delivery_id, index, line["product_id"], line["qty"],
                 line["unit_cost"], line["amount"]),
            )
        conn.execute(
            "UPDATE store_requisition SET status = 'CLOSED' WHERE requisition_id = ?",
            (requisition_id,),
        )
        emit_event(
            conn,
            "STORE_DELIVERY",
            delivery_id,
            {
                "delivery_id": delivery_id,
                "store_id": req["store_id"],
                "delivery_date": day.isoformat(),
                "posting_date": day.isoformat(),
                "amount": round_money(total_cost),
                "lines": lines,
            },
        )
    return {
        "delivery_id": delivery_id,
        "store_id": req["store_id"],
        "store_name": req["store_name"],
        "total_qty": round_qty(total_qty),
        "total_cost": round_money(total_cost),
        "lines": lines,
    }


def deliver_corporate_order(
    conn: sqlite3.Connection,
    order_id: str,
    delivery_date: str | date,
) -> dict:
    day = to_date(delivery_date) or date.today()
    order = get_corporate_order(conn, order_id)
    if order["status"] not in ("OPEN", "PLANNED"):
        raise SdError(f"团餐订单 {order_id} 状态为 {order['status']}，不能配送")

    with transaction(conn):
        delivery_id = next_number(conn, f"DL-{day:%Y%m%d}")
        total_qty = 0.0
        total_cost = 0.0
        revenue = 0.0
        lines: list[dict] = []
        for item in order["items"]:
            material = repo.get_material(conn, item["product_id"])
            if material is None:
                raise SdError(f"成品不存在: {item['product_id']}")
            consumed = consume_fefo(
                conn,
                item["product_id"],
                float(item["qty"]),
                material.default_warehouse_id or "WH-FG",
                day,
                "SALES_DELIVERY",
                delivery_id,
            )
            unit_cost = consumed["amount"] / consumed["qty"] if consumed["qty"] else 0.0
            line_revenue = round_money(float(item["qty"]) * float(item["unit_price"]))
            total_qty += consumed["qty"]
            total_cost += consumed["amount"]
            revenue += line_revenue
            lines.append(
                {
                    "product_id": item["product_id"],
                    "product_name": item["material_name"],
                    "qty": consumed["qty"],
                    "unit": item["base_unit"],
                    "unit_price": float(item["unit_price"]),
                    "unit_cost": unit_cost,
                    "revenue": line_revenue,
                    "cogs": consumed["amount"],
                }
            )
            conn.execute(
                """INSERT INTO sales_fact
                   (sale_id, sale_date, channel, store_id, customer_id, product_id,
                    qty, unit_price, revenue, unit_cost, cogs)
                   VALUES (?, ?, 'GROUP', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (delivery_id, day.isoformat(), STORE_CORP, order["customer_id"],
                 item["product_id"], consumed["qty"], item["unit_price"], line_revenue,
                 unit_cost, consumed["amount"]),
            )
        conn.execute(
            """INSERT INTO outbound_delivery
               (delivery_id, delivery_type, store_id, customer_id, delivery_date,
                status, total_qty, total_cost, reference_id, created_at)
               VALUES (?, 'CORPORATE', NULL, ?, ?, 'POSTED', ?, ?, ?, ?)""",
            (delivery_id, order["customer_id"], day.isoformat(), round_qty(total_qty),
             round_money(total_cost), order_id, now_iso()),
        )
        for index, line in enumerate(lines, start=10):
            conn.execute(
                """INSERT INTO outbound_delivery_item
                   (delivery_id, line_no, product_id, qty, batch_id, unit_cost, amount)
                   VALUES (?, ?, ?, ?, NULL, ?, ?)""",
                (delivery_id, index, line["product_id"], line["qty"],
                 line["unit_cost"], line["cogs"]),
            )
        conn.execute("UPDATE sales_order SET status = 'DELIVERED' WHERE order_id = ?", (order_id,))
        emit_event(
            conn,
            "SALES_DELIVERY",
            delivery_id,
            {
                "delivery_id": delivery_id,
                "order_id": order_id,
                "customer_id": order["customer_id"],
                "delivery_date": day.isoformat(),
                "posting_date": day.isoformat(),
                "amount": round_money(revenue),
                "cost_amount": round_money(total_cost),
                "lines": lines,
            },
        )
    return {
        "delivery_id": delivery_id,
        "order_id": order_id,
        "customer_id": order["customer_id"],
        "customer_name": order["customer_name"],
        "total_qty": round_qty(total_qty),
        "revenue": round_money(revenue),
        "total_cost": round_money(total_cost),
    }


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def reorder_alerts(conn: sqlite3.Connection) -> list[dict]:
    return [
        row for row in store_inventory_rows(conn)
        if row["store_type"] != "CORP" and float(row["qty_on_hand"]) < float(row["reorder_point"])
    ]


def recent_sales(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        """SELECT p.*, s.store_name
           FROM pos_sale p JOIN store s ON s.store_id = p.store_id
           ORDER BY p.sale_id DESC LIMIT ?""",
        (limit,),
    )
    return [dict(row) for row in rows]


def deliveries(conn: sqlite3.Connection, delivery_type: str | None = None) -> list[dict]:
    sql = """SELECT d.*, s.store_name, c.customer_name
             FROM outbound_delivery d
             LEFT JOIN store s ON s.store_id = d.store_id
             LEFT JOIN customer c ON c.customer_id = d.customer_id
             WHERE 1 = 1"""
    args: list = []
    if delivery_type:
        sql += " AND d.delivery_type = ?"
        args.append(delivery_type)
    sql += " ORDER BY d.delivery_id"
    return [dict(row) for row in conn.execute(sql, args)]
