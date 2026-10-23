"""库存底层操作：批次入库、FEFO 出库、门店成品库存加权平均。"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from pp import repo
from pp.util import QTY_EPS, now_iso, round_money, round_qty, to_date


class InventoryError(Exception):
    pass


def add_batch(
    conn: sqlite3.Connection,
    material_id: str,
    qty: float,
    unit_cost: float,
    warehouse_id: str,
    batch_no: str,
    batch_id: str | None = None,
    production_date: str | None = None,
    expiry_date: str | None = None,
    movement_type: str = "PURCHASE_RECEIPT",
    reference_no: str | None = None,
    posting_date: str | date | None = None,
) -> dict:
    batch_id = batch_id or f"B{batch_no}"
    posting = (to_date(posting_date) or date.today()).isoformat()
    conn.execute(
        """INSERT INTO inventory_batch
           (batch_id, material_id, warehouse_id, batch_no, qty_on_hand, qty_reserved,
            production_date, expiry_date, unit_cost, status)
           VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, 'UNRESTRICTED')""",
        (batch_id, material_id, warehouse_id, batch_no, round_qty(qty),
         production_date, expiry_date, round(unit_cost, 6)),
    )
    amount = round_money(qty * unit_cost)
    conn.execute(
        """INSERT INTO inventory_movement
           (movement_type, material_id, batch_id, qty, unit_cost, amount, reference_no,
            posting_date, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (movement_type, material_id, batch_id, qty, unit_cost, amount, reference_no, posting, now_iso()),
    )
    return {
        "batch_id": batch_id,
        "material_id": material_id,
        "qty": round_qty(qty),
        "unit_cost": unit_cost,
        "amount": amount,
        "expiry_date": expiry_date,
    }


def consume_fefo(
    conn: sqlite3.Connection,
    material_id: str,
    qty: float,
    warehouse_id: str | None,
    posting_date: str | date,
    movement_type: str,
    reference_no: str,
    order_id: str | None = None,
) -> dict:
    """按先到期先出消耗批次；返回 {qty, amount, lines}。库存不足抛异常，由外层事务回滚。"""
    posting = to_date(posting_date) or date.today()
    remaining = round_qty(qty)
    lines: list[dict] = []
    total = 0.0
    for batch in repo.get_batches(conn, material_id, warehouse_id):
        if remaining <= QTY_EPS:
            break
        if not batch.available_on(posting):
            continue
        take = round_qty(min(batch.free_qty, remaining))
        if take <= QTY_EPS:
            continue
        conn.execute(
            "UPDATE inventory_batch SET qty_on_hand = qty_on_hand - ? WHERE batch_id = ?",
            (take, batch.batch_id),
        )
        amount = round_money(take * batch.unit_cost)
        conn.execute(
            """INSERT INTO inventory_movement
               (movement_type, material_id, batch_id, qty, unit_cost, amount, order_id,
                reference_no, posting_date, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                movement_type,
                material_id,
                batch.batch_id,
                -take,
                batch.unit_cost,
                amount,
                order_id,
                reference_no,
                posting.isoformat(),
                now_iso(),
            ),
        )
        lines.append(
            {
                "batch_id": batch.batch_id,
                "qty": take,
                "unit_cost": batch.unit_cost,
                "amount": amount,
                "expiry_date": batch.expiry_date,
            }
        )
        remaining -= take
        total += amount
    if remaining > QTY_EPS:
        raise InventoryError(
            f"{material_id} 库存不足，缺口 {round_qty(remaining)}（需求 {qty}）"
        )
    return {"qty": round_qty(qty), "amount": round_money(total), "lines": lines}


def store_inventory_add(
    conn: sqlite3.Connection,
    store_id: str,
    product_id: str,
    qty: float,
    unit_cost: float,
) -> dict:
    row = conn.execute(
        "SELECT * FROM store_inventory WHERE store_id = ? AND product_id = ?",
        (store_id, product_id),
    ).fetchone()
    qty = round_qty(qty)
    if row is None:
        conn.execute(
            """INSERT INTO store_inventory
               (store_id, product_id, qty_on_hand, avg_cost, reorder_point, target_stock)
               VALUES (?, ?, ?, ?, 0, 0)""",
            (store_id, product_id, qty, round(unit_cost, 6)),
        )
    else:
        old_qty = float(row["qty_on_hand"])
        new_qty = old_qty + qty
        new_cost = (
            (old_qty * float(row["avg_cost"]) + qty * unit_cost) / new_qty
            if new_qty > 0 else unit_cost
        )
        conn.execute(
            """UPDATE store_inventory
               SET qty_on_hand = ?, avg_cost = ?
               WHERE store_id = ? AND product_id = ?""",
            (round_qty(new_qty), round(new_cost, 6), store_id, product_id),
        )
    return store_inventory_get(conn, store_id, product_id)


def store_inventory_get(
    conn: sqlite3.Connection, store_id: str, product_id: str
) -> dict | None:
    row = conn.execute(
        """SELECT si.*, m.material_name, m.base_unit, s.store_name
           FROM store_inventory si
           JOIN material m ON m.material_id = si.product_id
           JOIN store s ON s.store_id = si.store_id
           WHERE si.store_id = ? AND si.product_id = ?""",
        (store_id, product_id),
    ).fetchone()
    return dict(row) if row else None


def store_inventory_consume(
    conn: sqlite3.Connection, store_id: str, product_id: str, qty: float
) -> float:
    row = store_inventory_get(conn, store_id, product_id)
    if row is None or float(row["qty_on_hand"]) + QTY_EPS < qty:
        available = float(row["qty_on_hand"]) if row else 0.0
        raise InventoryError(
            f"{store_id} 的 {product_id} 门店库存不足：可用 {available}，需要 {qty}"
        )
    conn.execute(
        "UPDATE store_inventory SET qty_on_hand = qty_on_hand - ? WHERE store_id = ? AND product_id = ?",
        (round_qty(qty), store_id, product_id),
    )
    return float(row["avg_cost"])


def store_inventory_rows(conn: sqlite3.Connection, store_id: str | None = None) -> list[dict]:
    sql = """SELECT si.*, m.material_name, m.base_unit, s.store_name, s.store_type
             FROM store_inventory si
             JOIN material m ON m.material_id = si.product_id
             JOIN store s ON s.store_id = si.store_id
             WHERE 1 = 1"""
    args: list = []
    if store_id:
        sql += " AND si.store_id = ?"
        args.append(store_id)
    sql += " ORDER BY si.store_id, si.product_id"
    return [dict(row) for row in conn.execute(sql, args)]


def expiring_batches(
    conn: sqlite3.Connection,
    as_of: str | date,
    within_days: int = 7,
    warehouse_id: str | None = None,
) -> list[dict]:
    start = to_date(as_of) or date.today()
    end = start + timedelta(days=within_days)
    sql = """SELECT b.*, m.material_name, m.base_unit
             FROM inventory_batch b JOIN material m ON m.material_id = b.material_id
             WHERE b.qty_on_hand > 0 AND b.expiry_date IS NOT NULL
               AND b.expiry_date >= ? AND b.expiry_date <= ?"""
    args: list = [start.isoformat(), end.isoformat()]
    if warehouse_id:
        sql += " AND b.warehouse_id = ?"
        args.append(warehouse_id)
    sql += " ORDER BY b.expiry_date, b.material_id"
    return [dict(row) for row in conn.execute(sql, args)]


def inventory_value(conn: sqlite3.Connection, material_type: str | None = None) -> float:
    sql = """SELECT COALESCE(SUM(b.qty_on_hand * b.unit_cost), 0) AS value
             FROM inventory_batch b JOIN material m ON m.material_id = b.material_id
             WHERE 1 = 1"""
    args: list = []
    if material_type:
        sql += " AND m.material_type = ?"
        args.append(material_type)
    return float(conn.execute(sql, args).fetchone()["value"])


def store_inventory_value(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(qty_on_hand * avg_cost), 0) AS value FROM store_inventory"
    ).fetchone()
    return float(row["value"])
