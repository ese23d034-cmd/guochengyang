"""MM 库存：批次入库、FEFO 出库、生产发料、效期预警与报损。"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Sequence

from . import repo
from .db import emit_event, next_number, transaction
from .util import QTY_EPS, now_iso, round_money, round_qty, to_date


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
           (batch_id, material_id, warehouse_id, batch_no, qty_on_hand,
            production_date, expiry_date, unit_cost, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'UNRESTRICTED')""",
        (batch_id, material_id, warehouse_id, batch_no, round_qty(qty),
         production_date, expiry_date, round(unit_cost, 6)),
    )
    amount = round_money(qty * unit_cost)
    conn.execute(
        """INSERT INTO inventory_movement
           (movement_type, material_id, batch_id, qty, unit_cost, amount,
            reference_no, posting_date, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (movement_type, material_id, batch_id, qty, unit_cost, amount,
         reference_no, posting, now_iso()),
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
    reference_no: str,
) -> dict:
    posting = to_date(posting_date) or date.today()
    remaining = round_qty(qty)
    lines: list[dict] = []
    total = 0.0
    for batch in repo.get_batches(conn, material_id, warehouse_id):
        if remaining <= QTY_EPS:
            break
        if not batch.available_on(posting):
            continue
        take = round_qty(min(batch.qty_on_hand, remaining))
        if take <= QTY_EPS:
            continue
        conn.execute(
            "UPDATE inventory_batch SET qty_on_hand = qty_on_hand - ? WHERE batch_id = ?",
            (take, batch.batch_id),
        )
        amount = round_money(take * batch.unit_cost)
        conn.execute(
            """INSERT INTO inventory_movement
               (movement_type, material_id, batch_id, qty, unit_cost, amount,
                reference_no, posting_date, created_at)
               VALUES ('GOODS_ISSUE', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (material_id, batch.batch_id, -take, batch.unit_cost, amount,
             reference_no, posting.isoformat(), now_iso()),
        )
        lines.append(
            {
                "batch_id": batch.batch_id,
                "batch_no": batch.batch_no,
                "qty": take,
                "unit_cost": batch.unit_cost,
                "amount": amount,
                "expiry_date": batch.expiry_date.isoformat() if batch.expiry_date else None,
            }
        )
        remaining -= take
        total += amount
    if remaining > QTY_EPS:
        raise InventoryError(
            f"{material_id} 在 {posting} 可用库存不足，缺口 {round_qty(remaining)}（需求 {qty}）"
        )
    return {"qty": round_qty(qty), "amount": round_money(total), "lines": lines}


def issue_to_production(
    conn: sqlite3.Connection,
    order_id: str,
    lines: Sequence[tuple[str, float]],
    issue_date: str | date,
) -> dict:
    """按生产订单发料；每个批次一行，便于追溯。"""
    day = to_date(issue_date) or date.today()
    if not lines:
        raise InventoryError("发料明细不能为空")
    consumed_lines: list[dict] = []
    total = 0.0
    with transaction(conn):
        issue_id = next_number(conn, f"GI-{day:%Y%m%d}")
        for material_id, qty in lines:
            material = repo.get_material(conn, material_id)
            if material is None:
                raise InventoryError(f"物料不存在: {material_id}")
            consumed = consume_fefo(
                conn,
                material_id,
                float(qty),
                material.default_warehouse_id,
                day,
                issue_id,
            )
            total += consumed["amount"]
            for batch_line in consumed["lines"]:
                consumed_lines.append(
                    {
                        "issue_id": issue_id,
                        "order_id": order_id,
                        "issue_date": day.isoformat(),
                        "material_id": material_id,
                        "material_name": material.material_name,
                        "unit": material.base_unit,
                        **batch_line,
                    }
                )
        conn.execute(
            """INSERT INTO mm_goods_issue
               (issue_id, order_id, issue_date, total_amount, status, created_at)
               VALUES (?, ?, ?, ?, 'POSTED', ?)""",
            (issue_id, order_id, day.isoformat(), round_money(total), now_iso()),
        )
        for line_no, line in enumerate(consumed_lines, start=10):
            conn.execute(
                """INSERT INTO mm_goods_issue_item
                   (issue_id, line_no, material_id, batch_id, qty, unit_cost, amount)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (issue_id, line_no, line["material_id"], line["batch_id"],
                 line["qty"], line["unit_cost"], line["amount"]),
            )
        emit_event(
            conn,
            "GOODS_ISSUE",
            issue_id,
            {
                "issue_id": issue_id,
                "order_id": order_id,
                "issue_date": day.isoformat(),
                "posting_date": day.isoformat(),
                "amount": round_money(total),
                "lines": consumed_lines,
            },
        )
    return {
        "issue_id": issue_id,
        "order_id": order_id,
        "issue_date": day.isoformat(),
        "total_amount": round_money(total),
        "lines": consumed_lines,
    }


def shelf_life_alerts(
    conn: sqlite3.Connection, as_of: str | date, within_days: int = 7
) -> list[dict]:
    start = to_date(as_of) or date.today()
    end = start + timedelta(days=within_days)
    rows = conn.execute(
        """SELECT b.*, m.material_name, m.base_unit, w.warehouse_name
           FROM inventory_batch b
           JOIN material m ON m.material_id = b.material_id
           JOIN warehouse w ON w.warehouse_id = b.warehouse_id
           WHERE b.qty_on_hand > 0 AND b.expiry_date IS NOT NULL
             AND b.expiry_date >= ? AND b.expiry_date <= ?
           ORDER BY b.expiry_date, b.material_id""",
        (start.isoformat(), end.isoformat()),
    )
    result = []
    for row in rows:
        expiry = to_date(row["expiry_date"])
        result.append(
            {
                "batch_id": row["batch_id"],
                "material_id": row["material_id"],
                "material_name": row["material_name"],
                "batch_no": row["batch_no"],
                "warehouse_name": row["warehouse_name"],
                "qty": float(row["qty_on_hand"]),
                "unit": row["base_unit"],
                "expiry_date": row["expiry_date"],
                "days_to_expiry": (expiry - start).days if expiry else None,
                "value": round_money(float(row["qty_on_hand"]) * float(row["unit_cost"])),
            }
        )
    return result


def expired_stock(conn: sqlite3.Connection, as_of: str | date) -> list[dict]:
    day = to_date(as_of) or date.today()
    rows = conn.execute(
        """SELECT b.*, m.material_name, m.base_unit, w.warehouse_name
           FROM inventory_batch b
           JOIN material m ON m.material_id = b.material_id
           JOIN warehouse w ON w.warehouse_id = b.warehouse_id
           WHERE b.qty_on_hand > 0 AND b.expiry_date IS NOT NULL AND b.expiry_date < ?
           ORDER BY b.expiry_date, b.material_id""",
        (day.isoformat(),),
    )
    return [
        {
            "batch_id": row["batch_id"],
            "material_id": row["material_id"],
            "material_name": row["material_name"],
            "batch_no": row["batch_no"],
            "warehouse_name": row["warehouse_name"],
            "qty": float(row["qty_on_hand"]),
            "unit": row["base_unit"],
            "expiry_date": row["expiry_date"],
            "value": round_money(float(row["qty_on_hand"]) * float(row["unit_cost"])),
        }
        for row in rows
    ]


def scrap_expired(conn: sqlite3.Connection, as_of: str | date) -> list[dict]:
    """过期批次报损：数量清零、写库存流水与 EXPIRED_SCRAP 事件。"""
    day = to_date(as_of) or date.today()
    expired = expired_stock(conn, day)
    if not expired:
        return []
    with transaction(conn):
        for row in expired:
            conn.execute(
                "UPDATE inventory_batch SET qty_on_hand = 0, status = 'EXPIRED' WHERE batch_id = ?",
                (row["batch_id"],),
            )
            conn.execute(
                """INSERT INTO inventory_movement
                   (movement_type, material_id, batch_id, qty, unit_cost, amount,
                    reference_no, posting_date, created_at)
                   VALUES ('EXPIRED_SCRAP', ?, ?, ?, ?, ?, 'SCRAP', ?, ?)""",
                (row["material_id"], row["batch_id"], -row["qty"],
                 row["value"] / row["qty"] if row["qty"] else 0, -row["value"],
                 day.isoformat(), now_iso()),
            )
        emit_event(
            conn,
            "EXPIRED_SCRAP",
            f"SCRAP-{day:%Y%m%d}",
            {"posting_date": day.isoformat(), "lines": expired,
             "amount": round_money(sum(row["value"] for row in expired))},
        )
    return expired


def inventory_snapshot(conn: sqlite3.Connection) -> list[dict]:
    return repo.stock_rows(conn)


def stock_summary(conn: sqlite3.Connection) -> list[dict]:
    return repo.stock_summary(conn)


def warehouse_stock(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT w.warehouse_id, w.warehouse_name, w.warehouse_type,
                  COUNT(DISTINCT b.material_id) AS material_count,
                  SUM(b.qty_on_hand * b.unit_cost) AS value
           FROM warehouse w
           LEFT JOIN inventory_batch b ON b.warehouse_id = w.warehouse_id AND b.qty_on_hand > 0
           GROUP BY w.warehouse_id ORDER BY w.warehouse_id"""
    )
    return [dict(row) for row in rows]
