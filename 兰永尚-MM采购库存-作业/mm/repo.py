"""MM 数据访问层。"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from .models import Batch, Bom, BomComponent, Material, Supplier
from .util import to_date


def _material(row: sqlite3.Row) -> Material:
    return Material(
        material_id=row["material_id"],
        material_name=row["material_name"],
        material_type=row["material_type"],
        base_unit=row["base_unit"],
        procurement_type=row["procurement_type"],
        standard_cost=float(row["standard_cost"]),
        safety_stock=float(row["safety_stock"]),
        min_order_qty=float(row["min_order_qty"]),
        lot_size=float(row["lot_size"]),
        lead_time_days=int(row["lead_time_days"]),
        shelf_life_days=int(row["shelf_life_days"]) if row["shelf_life_days"] is not None else None,
        default_warehouse_id=row["default_warehouse_id"],
        default_supplier_id=row["default_supplier_id"],
        is_active=bool(row["is_active"]),
    )


def get_material(conn: sqlite3.Connection, material_id: str) -> Material | None:
    row = conn.execute("SELECT * FROM material WHERE material_id = ?", (material_id,)).fetchone()
    return _material(row) if row else None


def list_materials(conn: sqlite3.Connection, material_type: str | None = None) -> list[Material]:
    sql = "SELECT * FROM material WHERE is_active = 1"
    args: list[Any] = []
    if material_type:
        sql += " AND material_type = ?"
        args.append(material_type)
    sql += " ORDER BY material_type, material_id"
    return [_material(row) for row in conn.execute(sql, args)]


def _supplier(row: sqlite3.Row) -> Supplier:
    return Supplier(
        supplier_id=row["supplier_id"],
        supplier_name=row["supplier_name"],
        category=row["category"],
        lead_time_days=int(row["lead_time_days"]),
        rating=float(row["rating"]),
        contact=row["contact"],
        status=row["status"],
    )


def list_suppliers(conn: sqlite3.Connection) -> list[Supplier]:
    return [_supplier(row) for row in conn.execute("SELECT * FROM supplier ORDER BY supplier_id")]


def get_supplier(conn: sqlite3.Connection, supplier_id: str) -> Supplier | None:
    row = conn.execute("SELECT * FROM supplier WHERE supplier_id = ?", (supplier_id,)).fetchone()
    return _supplier(row) if row else None


def get_bom(
    conn: sqlite3.Connection, product_id: str, on_date: str | date | None = None
) -> Bom | None:
    ref = (to_date(on_date) or date.today()).isoformat()
    header = conn.execute(
        """SELECT * FROM bom
           WHERE product_id = ? AND status = 'ACTIVE'
             AND (valid_from IS NULL OR valid_from <= ?)
             AND (valid_to IS NULL OR valid_to >= ?)
           ORDER BY version DESC LIMIT 1""",
        (product_id, ref, ref),
    ).fetchone()
    if header is None:
        return None
    rows = conn.execute(
        "SELECT * FROM bom_item WHERE bom_id = ? ORDER BY line_no", (header["bom_id"],)
    ).fetchall()
    components = tuple(
        BomComponent(
            bom_id=header["bom_id"],
            product_id=product_id,
            base_qty=float(header["base_qty"]),
            line_no=int(row["line_no"]),
            component_id=row["component_id"],
            qty_per_base=float(row["qty_per_base"]),
            scrap_rate=float(row["scrap_rate"]),
        )
        for row in rows
    )
    return Bom(
        bom_id=header["bom_id"],
        product_id=product_id,
        version=header["version"],
        base_qty=float(header["base_qty"]),
        status=header["status"],
        components=components,
    )


def list_bom_products(conn: sqlite3.Connection) -> list[str]:
    return [
        row["product_id"]
        for row in conn.execute("SELECT DISTINCT product_id FROM bom WHERE status = 'ACTIVE'")
    ]


def _batch(row: sqlite3.Row) -> Batch:
    return Batch(
        batch_id=row["batch_id"],
        material_id=row["material_id"],
        warehouse_id=row["warehouse_id"],
        batch_no=row["batch_no"],
        qty_on_hand=float(row["qty_on_hand"]),
        production_date=row["production_date"],
        expiry_date=to_date(row["expiry_date"]),
        unit_cost=float(row["unit_cost"]),
        status=row["status"],
    )


def get_batches(
    conn: sqlite3.Connection, material_id: str, warehouse_id: str | None = None
) -> list[Batch]:
    sql = "SELECT * FROM inventory_batch WHERE material_id = ? AND qty_on_hand > 0"
    args: list[Any] = [material_id]
    if warehouse_id:
        sql += " AND warehouse_id = ?"
        args.append(warehouse_id)
    sql += " ORDER BY CASE WHEN expiry_date IS NULL THEN 1 ELSE 0 END, expiry_date, batch_id"
    return [_batch(row) for row in conn.execute(sql, args)]


def get_batch(conn: sqlite3.Connection, batch_id: str) -> Batch | None:
    row = conn.execute("SELECT * FROM inventory_batch WHERE batch_id = ?", (batch_id,)).fetchone()
    return _batch(row) if row else None


def available_qty(conn: sqlite3.Connection, material_id: str, on_date: str | date) -> float:
    need = to_date(on_date)
    return sum(batch.qty_on_hand for batch in get_batches(conn, material_id) if batch.available_on(need))


def list_requisitions(
    conn: sqlite3.Connection,
    status: str | None = None,
    run_id: str | None = None,
) -> list[dict]:
    sql = """SELECT p.*, m.material_name, m.material_type
             FROM purchase_requisition p JOIN material m ON m.material_id = p.material_id
             WHERE 1 = 1"""
    args: list[Any] = []
    if status:
        sql += " AND p.status = ?"
        args.append(status)
    if run_id:
        sql += " AND p.mrp_run_id = ?"
        args.append(run_id)
    sql += " ORDER BY p.required_date, p.pr_id"
    return [dict(row) for row in conn.execute(sql, args)]


def get_requisition(conn: sqlite3.Connection, pr_id: str) -> dict | None:
    row = conn.execute(
        """SELECT p.*, m.material_name, m.base_unit
           FROM purchase_requisition p JOIN material m ON m.material_id = p.material_id
           WHERE p.pr_id = ?""",
        (pr_id,),
    ).fetchone()
    return dict(row) if row else None


def list_purchase_orders(
    conn: sqlite3.Connection,
    status: str | None = None,
    run_id: str | None = None,
) -> list[dict]:
    sql = """SELECT po.*, s.supplier_name, m.material_name, m.material_type
             FROM purchase_order po
             JOIN supplier s ON s.supplier_id = po.supplier_id
             JOIN material m ON m.material_id = po.material_id
             WHERE 1 = 1"""
    args: list[Any] = []
    if status:
        sql += " AND po.status = ?"
        args.append(status)
    if run_id:
        sql += " AND po.pr_id IN (SELECT pr_id FROM purchase_requisition WHERE mrp_run_id = ?)"
        args.append(run_id)
    sql += " ORDER BY po.po_id"
    return [dict(row) for row in conn.execute(sql, args)]


def list_receipts(conn: sqlite3.Connection, run_id: str | None = None) -> list[dict]:
    sql = """SELECT r.*, s.supplier_name, m.material_name, m.material_type
             FROM mm_receipt r
             JOIN purchase_order po ON po.po_id = r.po_id
             JOIN supplier s ON s.supplier_id = po.supplier_id
             JOIN material m ON m.material_id = po.material_id
             WHERE 1 = 1"""
    args: list[Any] = []
    if run_id:
        sql += " AND po.pr_id IN (SELECT pr_id FROM purchase_requisition WHERE mrp_run_id = ?)"
        args.append(run_id)
    sql += " ORDER BY r.receipt_id"
    return [dict(row) for row in conn.execute(sql, args)]


def list_issues(conn: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM mm_goods_issue ORDER BY issue_id")]


def list_events(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM integration_event WHERE 1 = 1"
    args: list[Any] = []
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY event_id"
    return [dict(row) for row in conn.execute(sql, args)]


def stock_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT b.*, m.material_name, m.material_type, m.base_unit, w.warehouse_name
           FROM inventory_batch b
           JOIN material m ON m.material_id = b.material_id
           JOIN warehouse w ON w.warehouse_id = b.warehouse_id
           WHERE b.qty_on_hand > 0
           ORDER BY m.material_type, b.material_id, b.expiry_date, b.batch_id"""
    )
    return [dict(row) for row in rows]


def stock_summary(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT b.material_id, m.material_name, m.material_type, m.base_unit,
                  SUM(b.qty_on_hand) AS qty, SUM(b.qty_on_hand * b.unit_cost) AS value,
                  MIN(b.expiry_date) AS earliest_expiry
           FROM inventory_batch b JOIN material m ON m.material_id = b.material_id
           WHERE b.qty_on_hand > 0
           GROUP BY b.material_id ORDER BY m.material_type, b.material_id"""
    )
    return [dict(row) for row in rows]


def inventory_value(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        """SELECT COALESCE(SUM(b.qty_on_hand * b.unit_cost), 0) AS value
           FROM inventory_batch b WHERE b.qty_on_hand > 0"""
    ).fetchone()
    return float(row["value"])


def supplier_scorecard(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT s.supplier_id, s.supplier_name, s.rating,
                  COUNT(po.po_id) AS po_count,
                  COALESCE(SUM(po.qty * po.unit_cost), 0) AS amount
           FROM supplier s LEFT JOIN purchase_order po ON po.supplier_id = s.supplier_id
           GROUP BY s.supplier_id ORDER BY amount DESC"""
    )
    return [dict(row) for row in rows]
