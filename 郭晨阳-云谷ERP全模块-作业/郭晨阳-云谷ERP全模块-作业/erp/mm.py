"""MM 采购与库存：采购申请转采购订单、供应商收货、冷链批次、效期预警。"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from pp import repo
from pp.db import emit_event, next_number, transaction
from pp.util import now_iso, round_money, round_qty, to_date

from .inventory import add_batch, expiring_batches

SUPPLIER_BY_MATERIAL = {
    "RM-001": "SUP-GRAIN",
    "RM-002": "SUP-GRAIN",
    "RM-003": "SUP-GRAIN",
    "RM-006": "SUP-GRAIN",
    "RM-007": "SUP-GRAIN",
    "RM-004": "SUP-MEAT",
    "RM-008": "SUP-MEAT",
    "RM-005": "SUP-VEG",
    "RM-009": "SUP-VEG",
    "RM-010": "SUP-VEG",
    "RM-013": "SUP-VEG",
    "RM-011": "SUP-PACK",
    "RM-012": "SUP-PACK",
    "PK-001": "SUP-PACK",
    "PK-002": "SUP-PACK",
}


class MmError(Exception):
    pass


def supplier_for(material_id: str) -> str:
    return SUPPLIER_BY_MATERIAL.get(material_id, "SUP-GRAIN")


def create_purchase_orders(
    conn: sqlite3.Connection,
    run_id: str | None = None,
    order_date: str | date | None = None,
) -> list[dict]:
    day = to_date(order_date) or date.today()
    prs = repo.list_purchase_requisitions(conn, run_id=run_id, status="OPEN")
    created: list[dict] = []
    with transaction(conn):
        for pr in prs:
            material = repo.get_material(conn, pr["material_id"])
            if material is None:
                continue
            supplier_id = supplier_for(material.material_id)
            supplier = conn.execute(
                "SELECT * FROM supplier WHERE supplier_id = ?", (supplier_id,)
            ).fetchone()
            if supplier is None:
                raise MmError(f"供应商不存在: {supplier_id}")
            po_id = next_number(conn, f"PUR-{day:%Y%m%d}")
            unit_cost = float(pr["unit_cost"]) or material.standard_cost
            conn.execute(
                """INSERT INTO purchase_order
                   (po_id, pr_id, supplier_id, material_id, qty, unit, unit_cost,
                    order_date, expected_date, status, warehouse_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)""",
                (
                    po_id, pr["pr_id"], supplier_id, material.material_id, pr["qty"],
                    material.base_unit, unit_cost, day.isoformat(), pr["required_date"],
                    material.default_warehouse_id, now_iso(),
                ),
            )
            emit_event(
                conn,
                "PURCHASE_ORDER_CREATED",
                po_id,
                {
                    "po_id": po_id,
                    "pr_id": pr["pr_id"],
                    "supplier_id": supplier_id,
                    "material_id": material.material_id,
                    "qty": pr["qty"],
                    "amount": round_money(float(pr["qty"]) * unit_cost),
                    "expected_date": pr["required_date"],
                },
            )
            created.append(
                {
                    "po_id": po_id,
                    "pr_id": pr["pr_id"],
                    "supplier_id": supplier_id,
                    "supplier_name": supplier["supplier_name"],
                    "material_id": material.material_id,
                    "material_name": material.material_name,
                    "qty": float(pr["qty"]),
                    "unit": material.base_unit,
                    "unit_cost": unit_cost,
                    "expected_date": pr["required_date"],
                }
            )
    return created


def receive_purchase_orders(
    conn: sqlite3.Connection,
    run_id: str | None = None,
    po_ids: list[str] | None = None,
    receipt_date: str | date | None = None,
) -> list[dict]:
    day = to_date(receipt_date) or date.today()
    sql = """SELECT po.*, s.supplier_name
             FROM purchase_order po JOIN supplier s ON s.supplier_id = po.supplier_id
             WHERE po.status = 'OPEN'"""
    args: list = []
    if po_ids is not None:
        if not po_ids:
            return []
        sql += f" AND po.po_id IN ({','.join('?' for _ in po_ids)})"
        args.extend(po_ids)
    elif run_id:
        sql += " AND po.pr_id IN (SELECT pr_id FROM purchase_requisition WHERE mrp_run_id = ?)"
        args.append(run_id)
    sql += " ORDER BY po.po_id"
    orders = [dict(row) for row in conn.execute(sql, args)]
    if not orders:
        return []

    receipts: list[dict] = []
    with transaction(conn):
        for order in orders:
            material = repo.get_material(conn, order["material_id"])
            if material is None:
                raise MmError(f"物料不存在: {order['material_id']}")
            warehouse = order["warehouse_id"] or material.default_warehouse_id
            if warehouse is None:
                raise MmError(f"{material.material_id} 未维护默认仓库")
            expiry = None
            if material.shelf_life_days:
                expiry = (day + timedelta(days=material.shelf_life_days)).isoformat()
            receipt_id = next_number(conn, f"GR-{day:%Y%m%d}")
            batch = add_batch(
                conn,
                material.material_id,
                float(order["qty"]),
                float(order["unit_cost"]),
                warehouse,
                batch_no=f"PO{order['po_id']}",
                batch_id=f"B{order['po_id']}",
                expiry_date=expiry,
                movement_type="PURCHASE_RECEIPT",
                reference_no=order["po_id"],
                posting_date=day,
            )
            amount = batch["amount"]
            conn.execute(
                """INSERT INTO mm_receipt
                   (receipt_id, po_id, receipt_date, warehouse_id, total_amount, status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'POSTED', ?)""",
                (receipt_id, order["po_id"], day.isoformat(), warehouse, amount, now_iso()),
            )
            conn.execute(
                """INSERT INTO mm_receipt_item
                   (receipt_id, line_no, material_id, qty, batch_id, unit_cost, amount)
                   VALUES (?, 10, ?, ?, ?, ?, ?)""",
                (receipt_id, material.material_id, order["qty"], batch["batch_id"],
                 order["unit_cost"], amount),
            )
            conn.execute("UPDATE purchase_order SET status = 'RECEIVED' WHERE po_id = ?", (order["po_id"],))
            if order["pr_id"]:
                conn.execute(
                    "UPDATE purchase_requisition SET status = 'RECEIVED' WHERE pr_id = ?",
                    (order["pr_id"],),
                )
            emit_event(
                conn,
                "PURCHASE_RECEIPT",
                receipt_id,
                {
                    "receipt_id": receipt_id,
                    "po_id": order["po_id"],
                    "supplier_id": order["supplier_id"],
                    "material_id": material.material_id,
                    "qty": float(order["qty"]),
                    "unit": material.base_unit,
                    "unit_cost": float(order["unit_cost"]),
                    "amount": amount,
                    "batch_id": batch["batch_id"],
                    "expiry_date": expiry,
                    "posting_date": day.isoformat(),
                },
            )
            receipts.append(
                {
                    "receipt_id": receipt_id,
                    "po_id": order["po_id"],
                    "supplier_name": order["supplier_name"],
                    "material_id": material.material_id,
                    "material_name": material.material_name,
                    "qty": float(order["qty"]),
                    "unit": material.base_unit,
                    "amount": amount,
                    "expiry_date": expiry,
                }
            )
    return receipts


def purchase_orders(
    conn: sqlite3.Connection,
    run_id: str | None = None,
    status: str | None = None,
) -> list[dict]:
    sql = """SELECT po.*, s.supplier_name, m.material_name
             FROM purchase_order po
             JOIN supplier s ON s.supplier_id = po.supplier_id
             JOIN material m ON m.material_id = po.material_id
             WHERE 1 = 1"""
    args: list = []
    if status:
        sql += " AND po.status = ?"
        args.append(status)
    if run_id:
        sql += " AND po.pr_id IN (SELECT pr_id FROM purchase_requisition WHERE mrp_run_id = ?)"
        args.append(run_id)
    sql += " ORDER BY po.po_id"
    return [dict(row) for row in conn.execute(sql, args)]


def shelf_life_alerts(
    conn: sqlite3.Connection, as_of: str | date, within_days: int = 7
) -> list[dict]:
    return expiring_batches(conn, as_of, within_days)


def supplier_scorecard(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT s.supplier_id, s.supplier_name, s.rating,
                  COUNT(po.po_id) AS po_count,
                  COALESCE(SUM(po.qty * po.unit_cost), 0) AS amount
           FROM supplier s LEFT JOIN purchase_order po ON po.supplier_id = s.supplier_id
           GROUP BY s.supplier_id ORDER BY amount DESC"""
    )
    return [dict(row) for row in rows]
