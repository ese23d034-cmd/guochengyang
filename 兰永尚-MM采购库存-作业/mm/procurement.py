"""MM 采购：采购申请转采购订单、供应商收货、批次建档。"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import repo
from .db import emit_event, next_number, transaction
from .inventory import add_batch
from .util import now_iso, round_money, to_date

class ProcurementError(Exception):
    pass


def create_purchase_orders(
    conn: sqlite3.Connection,
    run_id: str | None = None,
    order_date: str | date | None = None,
) -> list[dict]:
    day = to_date(order_date) or date.today()
    prs = repo.list_requisitions(conn, status="OPEN", run_id=run_id)
    created: list[dict] = []
    with transaction(conn):
        for pr in prs:
            material = repo.get_material(conn, pr["material_id"])
            if material is None:
                raise ProcurementError(f"物料不存在: {pr['material_id']}")
            supplier_id = material.default_supplier_id
            if supplier_id is None:
                raise ProcurementError(f"{material.material_id} 未维护默认供应商")
            supplier = repo.get_supplier(conn, supplier_id)
            if supplier is None:
                raise ProcurementError(f"供应商不存在: {supplier_id}")
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
                    "supplier_name": supplier.supplier_name,
                    "material_id": material.material_id,
                    "material_name": material.material_name,
                    "qty": float(pr["qty"]),
                    "unit": material.base_unit,
                    "unit_cost": unit_cost,
                    "expected_date": pr["required_date"],
                    "amount": round_money(float(pr["qty"]) * unit_cost),
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
                raise ProcurementError(f"物料不存在: {order['material_id']}")
            warehouse = order["warehouse_id"] or material.default_warehouse_id
            if warehouse is None:
                raise ProcurementError(f"{material.material_id} 未维护默认仓库")
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
            conn.execute(
                """INSERT INTO mm_receipt
                   (receipt_id, po_id, receipt_date, warehouse_id, total_amount, status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'POSTED', ?)""",
                (receipt_id, order["po_id"], day.isoformat(), warehouse, batch["amount"], now_iso()),
            )
            conn.execute(
                """INSERT INTO mm_receipt_item
                   (receipt_id, line_no, material_id, qty, batch_id, unit_cost, amount)
                   VALUES (?, 10, ?, ?, ?, ?, ?)""",
                (receipt_id, material.material_id, order["qty"], batch["batch_id"],
                 order["unit_cost"], batch["amount"]),
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
                    "amount": batch["amount"],
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
                    "amount": batch["amount"],
                    "batch_id": batch["batch_id"],
                    "expiry_date": expiry,
                    "warehouse_id": warehouse,
                }
            )
    return receipts


def purchase_order_detail(conn: sqlite3.Connection, po_id: str) -> dict:
    orders = repo.list_purchase_orders(conn)
    for order in orders:
        if order["po_id"] == po_id:
            order["receipts"] = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM mm_receipt WHERE po_id = ? ORDER BY receipt_id", (po_id,)
                )
            ]
            return order
    raise ProcurementError(f"采购订单不存在: {po_id}")
