"""物料主数据与 BOM：查询/维护物料、BOM 配方。"""
from __future__ import annotations

import sqlite3
from typing import Sequence

from . import repo
from .db import transaction
from .models import Material
from .util import QTY_EPS, round_money


class MasterDataError(Exception):
    pass


def material_card(conn: sqlite3.Connection, material_id: str) -> dict:
    material = repo.get_material(conn, material_id)
    if material is None:
        raise MasterDataError(f"物料不存在: {material_id}")
    stock = sum(batch.qty_on_hand for batch in repo.get_batches(conn, material_id))
    return {
        "material_id": material.material_id,
        "material_name": material.material_name,
        "material_type": material.material_type,
        "base_unit": material.base_unit,
        "procurement_type": material.procurement_type,
        "standard_cost": material.standard_cost,
        "safety_stock": material.safety_stock,
        "min_order_qty": material.min_order_qty,
        "lot_size": material.lot_size,
        "lead_time_days": material.lead_time_days,
        "shelf_life_days": material.shelf_life_days,
        "default_warehouse_id": material.default_warehouse_id,
        "default_supplier_id": material.default_supplier_id,
        "stock_on_hand": round(stock, 3),
    }


def query_bom(conn: sqlite3.Connection, product_id: str, on_date: str | None = None) -> dict:
    """按产品 ID 查 BOM 配方（供 PP/成本/门店配方查询）。"""
    product = repo.get_material(conn, product_id)
    if product is None:
        raise MasterDataError(f"物料不存在: {product_id}")
    bom = repo.get_bom(conn, product_id, on_date)
    if bom is None:
        raise MasterDataError(f"{product_id} 没有生效 BOM")
    components = []
    for item in bom.components:
        child = repo.get_material(conn, item.component_id)
        components.append(
            {
                "line_no": item.line_no,
                "component_id": item.component_id,
                "component_name": child.material_name if child else None,
                "unit": child.base_unit if child else None,
                "material_type": child.material_type if child else None,
                "qty_per_base": item.qty_per_base,
                "scrap_rate": item.scrap_rate,
                "qty_per_unit": round(item.qty_per_base / bom.base_qty * (1 + item.scrap_rate), 6),
                "standard_cost": child.standard_cost if child else 0.0,
            }
        )
    estimated = round_money(
        sum(c["qty_per_unit"] * c["standard_cost"] for c in components)
    )
    return {
        "product_id": product_id,
        "product_name": product.material_name,
        "bom_id": bom.bom_id,
        "version": bom.version,
        "base_qty": bom.base_qty,
        "components": components,
        "estimated_material_cost": estimated,
    }


def where_used(conn: sqlite3.Connection, component_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT b.product_id, m.material_name, i.qty_per_base, i.scrap_rate
           FROM bom_item i
           JOIN bom b ON b.bom_id = i.bom_id
           JOIN material m ON m.material_id = b.product_id
           WHERE i.component_id = ? AND b.status = 'ACTIVE'
           ORDER BY b.product_id""",
        (component_id,),
    )
    return [dict(row) for row in rows]


def upsert_material(conn: sqlite3.Connection, material: Material) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT INTO material
               (material_id, material_name, material_type, base_unit, procurement_type,
                standard_cost, safety_stock, min_order_qty, lot_size, lead_time_days,
                shelf_life_days, default_warehouse_id, default_supplier_id, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(material_id) DO UPDATE SET
                 material_name = excluded.material_name,
                 material_type = excluded.material_type,
                 base_unit = excluded.base_unit,
                 procurement_type = excluded.procurement_type,
                 standard_cost = excluded.standard_cost,
                 safety_stock = excluded.safety_stock,
                 min_order_qty = excluded.min_order_qty,
                 lot_size = excluded.lot_size,
                 lead_time_days = excluded.lead_time_days,
                 shelf_life_days = excluded.shelf_life_days,
                 default_warehouse_id = excluded.default_warehouse_id,
                 default_supplier_id = excluded.default_supplier_id,
                 is_active = excluded.is_active""",
            (
                material.material_id, material.material_name, material.material_type,
                material.base_unit, material.procurement_type, material.standard_cost,
                material.safety_stock, material.min_order_qty, material.lot_size,
                material.lead_time_days, material.shelf_life_days,
                material.default_warehouse_id, material.default_supplier_id,
                1 if material.is_active else 0,
            ),
        )


def upsert_bom(
    conn: sqlite3.Connection,
    product_id: str,
    components: Sequence[tuple[str, float, float]],
    base_qty: float = 1.0,
    version: str = "V1",
) -> str:
    """components: [(component_id, qty_per_base, scrap_rate)]；返回 bom_id。"""
    if not components:
        raise MasterDataError("BOM 至少需要一个组件")
    product = repo.get_material(conn, product_id)
    if product is None:
        raise MasterDataError(f"产品不存在: {product_id}")
    bom_id = f"BOM-{product_id}-{version}"
    with transaction(conn):
        conn.execute(
            """INSERT INTO bom(bom_id, product_id, version, base_qty, status, valid_from)
               VALUES (?, ?, ?, ?, 'ACTIVE', '2026-01-01')
               ON CONFLICT(product_id, version) DO UPDATE SET
                 base_qty = excluded.base_qty, status = 'ACTIVE'""",
            (bom_id, product_id, version, base_qty),
        )
        row = conn.execute(
            "SELECT bom_id FROM bom WHERE product_id = ? AND version = ?", (product_id, version)
        ).fetchone()
        actual_id = row["bom_id"]
        conn.execute("DELETE FROM bom_item WHERE bom_id = ?", (actual_id,))
        for line_no, (component_id, qty, scrap) in enumerate(components, start=10):
            if qty <= QTY_EPS:
                raise MasterDataError(f"组件 {component_id} 用量必须大于 0")
            conn.execute(
                """INSERT INTO bom_item(bom_id, line_no, component_id, qty_per_base, scrap_rate)
                   VALUES (?, ?, ?, ?, ?)""",
                (actual_id, line_no, component_id, qty, scrap),
            )
    return bom_id


def master_summary(conn: sqlite3.Connection) -> dict:
    materials = repo.list_materials(conn)
    counts = {"FG": 0, "SF": 0, "RM": 0, "PK": 0}
    for material in materials:
        counts[material.material_type] += 1
    return {
        "materials": len(materials),
        "counts": counts,
        "boms": len(repo.list_bom_products(conn)),
        "suppliers": len(repo.list_suppliers(conn)),
        "warehouses": conn.execute("SELECT COUNT(*) AS n FROM warehouse").fetchone()["n"],
    }
