"""数据访问层：把 SQL 集中在这里，业务层只处理领域对象。"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, Iterable, Sequence

from .models import Batch, Bom, BomComponent, Material, RoutingOp, WorkCenter
from .util import to_date


# ---------------------------------------------------------------------------
# 物料 / BOM / 工艺
# ---------------------------------------------------------------------------
def _material(row: sqlite3.Row) -> Material:
    return Material(
        material_id=row["material_id"],
        material_name=row["material_name"],
        material_type=row["material_type"],
        base_unit=row["base_unit"],
        procurement_type=row["procurement_type"],
        standard_cost=float(row["standard_cost"]),
        list_price=float(row["list_price"]) if row["list_price"] is not None else None,
        safety_stock=float(row["safety_stock"]),
        min_order_qty=float(row["min_order_qty"]),
        lot_size=float(row["lot_size"]),
        lead_time_days=int(row["lead_time_days"]),
        shelf_life_days=int(row["shelf_life_days"]) if row["shelf_life_days"] is not None else None,
        default_warehouse_id=row["default_warehouse_id"],
        is_active=bool(row["is_active"]),
    )


def get_material(conn: sqlite3.Connection, material_id: str) -> Material | None:
    row = conn.execute("SELECT * FROM material WHERE material_id = ?", (material_id,)).fetchone()
    return _material(row) if row else None


def list_materials(
    conn: sqlite3.Connection,
    material_type: str | None = None,
    active_only: bool = True,
) -> list[Material]:
    sql = "SELECT * FROM material WHERE 1 = 1"
    args: list[Any] = []
    if material_type:
        sql += " AND material_type = ?"
        args.append(material_type)
    if active_only:
        sql += " AND is_active = 1"
    sql += " ORDER BY material_type, material_id"
    return [_material(row) for row in conn.execute(sql, args)]


def update_material_cost(conn: sqlite3.Connection, material_id: str, cost: float) -> None:
    conn.execute(
        "UPDATE material SET standard_cost = ? WHERE material_id = ?",
        (round(cost, 4), material_id),
    )


def get_bom(
    conn: sqlite3.Connection,
    product_id: str,
    on_date: str | date | None = None,
) -> Bom | None:
    """取产品在指定日期生效的 BOM（默认今天）。"""
    ref = (to_date(on_date) or date.today()).isoformat()
    header = conn.execute(
        """SELECT * FROM bom
           WHERE product_id = ? AND status = 'ACTIVE'
             AND (valid_from IS NULL OR valid_from <= ?)
             AND (valid_to   IS NULL OR valid_to   >= ?)
           ORDER BY version DESC
           LIMIT 1""",
        (product_id, ref, ref),
    ).fetchone()
    if header is None:
        return None
    rows = conn.execute(
        """SELECT i.* FROM bom_item i
           WHERE i.bom_id = ? ORDER BY i.line_no""",
        (header["bom_id"],),
    ).fetchall()
    components = tuple(
        BomComponent(
            bom_id=row["bom_id"],
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
        components=components,
    )


def list_bom_products(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT product_id FROM bom WHERE status = 'ACTIVE'").fetchall()
    return [row["product_id"] for row in rows]


def get_routing(conn: sqlite3.Connection, material_id: str) -> list[RoutingOp]:
    rows = conn.execute(
        "SELECT * FROM routing WHERE material_id = ? ORDER BY op_no", (material_id,)
    ).fetchall()
    return [
        RoutingOp(
            material_id=row["material_id"],
            op_no=int(row["op_no"]),
            operation_name=row["operation_name"],
            work_center_id=row["work_center_id"],
            setup_min=float(row["setup_min"]),
            run_min_per_unit=float(row["run_min_per_unit"]),
        )
        for row in rows
    ]


def get_work_center(conn: sqlite3.Connection, work_center_id: str) -> WorkCenter | None:
    row = conn.execute(
        "SELECT * FROM work_center WHERE work_center_id = ?", (work_center_id,)
    ).fetchone()
    if row is None:
        return None
    return WorkCenter(
        work_center_id=row["work_center_id"],
        work_center_name=row["work_center_name"],
        daily_capacity_min=float(row["daily_capacity_min"]),
        cost_rate_per_min=float(row["cost_rate_per_min"]),
    )


def list_work_centers(conn: sqlite3.Connection) -> list[WorkCenter]:
    rows = conn.execute("SELECT * FROM work_center ORDER BY work_center_id").fetchall()
    return [
        WorkCenter(
            work_center_id=row["work_center_id"],
            work_center_name=row["work_center_name"],
            daily_capacity_min=float(row["daily_capacity_min"]),
            cost_rate_per_min=float(row["cost_rate_per_min"]),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 库存
# ---------------------------------------------------------------------------
def _batch(row: sqlite3.Row) -> Batch:
    return Batch(
        batch_id=row["batch_id"],
        material_id=row["material_id"],
        warehouse_id=row["warehouse_id"],
        batch_no=row["batch_no"],
        qty_on_hand=float(row["qty_on_hand"]),
        qty_reserved=float(row["qty_reserved"]),
        production_date=row["production_date"],
        expiry_date=to_date(row["expiry_date"]),
        unit_cost=float(row["unit_cost"]),
        status=row["status"],
    )


def get_batches(
    conn: sqlite3.Connection,
    material_id: str,
    warehouse_id: str | None = None,
) -> list[Batch]:
    """按 FEFO（先到期先出）排序返回批次。"""
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
    """可用库存 = 未预留、未冻结、且效期覆盖需求日期的批次数量。"""
    need = to_date(on_date)
    return sum(batch.free_qty for batch in get_batches(conn, material_id) if batch.available_on(need))


def total_stock(conn: sqlite3.Connection, material_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(qty_on_hand), 0) AS qty FROM inventory_batch WHERE material_id = ?",
        (material_id,),
    ).fetchone()
    return float(row["qty"])


# ---------------------------------------------------------------------------
# 门店要货单
# ---------------------------------------------------------------------------
def get_requisition(conn: sqlite3.Connection, requisition_id: str) -> dict | None:
    row = conn.execute(
        """SELECT r.*, s.store_name
           FROM store_requisition r JOIN store s ON s.store_id = r.store_id
           WHERE r.requisition_id = ?""",
        (requisition_id,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["items"] = [
        dict(item)
        for item in conn.execute(
            """SELECT i.*, m.material_name, m.base_unit
               FROM store_requisition_item i JOIN material m ON m.material_id = i.product_id
               WHERE i.requisition_id = ? ORDER BY i.line_no""",
            (requisition_id,),
        )
    ]
    return result


def list_requisitions(
    conn: sqlite3.Connection,
    status: str | None = None,
    requisition_ids: Sequence[str] | None = None,
) -> list[dict]:
    sql = """SELECT r.*, s.store_name
             FROM store_requisition r JOIN store s ON s.store_id = r.store_id
             WHERE 1 = 1"""
    args: list[Any] = []
    if status:
        sql += " AND r.status = ?"
        args.append(status)
    if requisition_ids is not None:
        if not requisition_ids:
            return []
        placeholders = ",".join("?" for _ in requisition_ids)
        sql += f" AND r.requisition_id IN ({placeholders})"
        args.extend(requisition_ids)
    sql += " ORDER BY r.required_date, r.requisition_id"
    result = []
    for row in conn.execute(sql, args):
        item = dict(row)
        item["items"] = [
            dict(x)
            for x in conn.execute(
                """SELECT i.*, m.material_name, m.base_unit
                   FROM store_requisition_item i JOIN material m ON m.material_id = i.product_id
                   WHERE i.requisition_id = ? ORDER BY i.line_no""",
                (item["requisition_id"],),
            )
        ]
        result.append(item)
    return result


def insert_requisition(
    conn: sqlite3.Connection,
    requisition_id: str,
    store_id: str,
    request_date: str,
    required_date: str,
    items: Iterable[tuple[str, float]],
    remark: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO store_requisition
           (requisition_id, store_id, request_date, required_date, status, remark, created_at)
           VALUES (?, ?, ?, ?, 'SUBMITTED', ?, datetime('now', 'localtime'))""",
        (requisition_id, store_id, request_date, required_date, remark),
    )
    for line_no, (product_id, qty) in enumerate(items, start=10):
        conn.execute(
            """INSERT INTO store_requisition_item(requisition_id, line_no, product_id, qty)
               VALUES (?, ?, ?, ?)""",
            (requisition_id, line_no, product_id, qty),
        )


# ---------------------------------------------------------------------------
# 生产订单
# ---------------------------------------------------------------------------
def get_production_order(conn: sqlite3.Connection, order_id: str) -> dict | None:
    row = conn.execute(
        """SELECT o.*, m.material_name, m.base_unit, m.material_type
           FROM production_order o JOIN material m ON m.material_id = o.material_id
           WHERE o.order_id = ?""",
        (order_id,),
    ).fetchone()
    return dict(row) if row else None


def list_production_orders(
    conn: sqlite3.Connection,
    run_id: str | None = None,
    status: str | None = None,
    material_id: str | None = None,
) -> list[dict]:
    sql = """SELECT o.*, m.material_name, m.base_unit, m.material_type
             FROM production_order o JOIN material m ON m.material_id = o.material_id
             WHERE 1 = 1"""
    args: list[Any] = []
    if run_id:
        sql += " AND o.mrp_run_id = ?"
        args.append(run_id)
    if status:
        sql += " AND o.status = ?"
        args.append(status)
    if material_id:
        sql += " AND o.material_id = ?"
        args.append(material_id)
    sql += " ORDER BY o.required_date, o.order_id"
    return [dict(row) for row in conn.execute(sql, args)]


def get_order_components(conn: sqlite3.Connection, order_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT c.*, m.material_name, m.base_unit
           FROM production_order_component c JOIN material m ON m.material_id = c.material_id
           WHERE c.order_id = ? ORDER BY c.line_no""",
        (order_id,),
    )
    return [dict(row) for row in rows]


def get_order_operations(conn: sqlite3.Connection, order_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT op.*, w.work_center_name
           FROM production_order_operation op
           JOIN work_center w ON w.work_center_id = op.work_center_id
           WHERE op.order_id = ? ORDER BY op.op_no""",
        (order_id,),
    )
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# 采购申请 / MRP 运行
# ---------------------------------------------------------------------------
def list_purchase_requisitions(
    conn: sqlite3.Connection,
    run_id: str | None = None,
    status: str | None = None,
) -> list[dict]:
    sql = """SELECT p.*, m.material_name, m.material_type
             FROM purchase_requisition p JOIN material m ON m.material_id = p.material_id
             WHERE 1 = 1"""
    args: list[Any] = []
    if run_id:
        sql += " AND p.mrp_run_id = ?"
        args.append(run_id)
    if status:
        sql += " AND p.status = ?"
        args.append(status)
    sql += " ORDER BY p.required_date, p.pr_id"
    return [dict(row) for row in conn.execute(sql, args)]


def get_mrp_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM mrp_run WHERE run_id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_mrp_runs(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM mrp_run ORDER BY run_at DESC, run_id DESC LIMIT ?", (limit,)
    )
    return [dict(row) for row in rows]


def list_explosion(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT e.*, m.material_name, m.base_unit
           FROM mrp_explosion e JOIN material m ON m.material_id = e.material_id
           WHERE e.run_id = ? ORDER BY e.level, e.material_id, e.required_date""",
        (run_id,),
    )
    return [dict(row) for row in rows]


def list_exceptions(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM mrp_exception WHERE run_id = ?
           ORDER BY CASE severity WHEN 'ERROR' THEN 1 WHEN 'WARNING' THEN 2 ELSE 3 END,
                    exception_id""",
        (run_id,),
    )
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# 库存清单 / 事件
# ---------------------------------------------------------------------------
def inventory_snapshot(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT b.*, m.material_name, m.material_type, m.base_unit
           FROM inventory_batch b JOIN material m ON m.material_id = b.material_id
           WHERE b.qty_on_hand > 0
           ORDER BY m.material_type, b.material_id, b.expiry_date"""
    )
    return [dict(row) for row in rows]


def list_events(
    conn: sqlite3.Connection,
    status: str | None = None,
    event_type: str | None = None,
) -> list[dict]:
    sql = "SELECT * FROM integration_event WHERE 1 = 1"
    args: list[Any] = []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if event_type:
        sql += " AND event_type = ?"
        args.append(event_type)
    sql += " ORDER BY event_id"
    return [dict(row) for row in conn.execute(sql, args)]
