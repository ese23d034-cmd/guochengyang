"""标准成本卷算与生产订单成本估算。

成本口径（简化）：
  外购物料：物料主数据 standard_cost（最近采购价/标准价）
  自制物料：直接材料成本（按 BOM 逐层卷算） + 直接人工（工艺路线运行工时 × 工作中心费率）
  设置工时（setup）在订单成本中按整单计入，不摊入单位标准成本。
"""
from __future__ import annotations

import sqlite3
from datetime import date

from . import repo
from .models import Material


class CostingError(Exception):
    pass


def unit_labor_cost(conn: sqlite3.Connection, material_id: str) -> float:
    total = 0.0
    for op in repo.get_routing(conn, material_id):
        wc = repo.get_work_center(conn, op.work_center_id)
        if wc:
            total += op.run_min_per_unit * wc.cost_rate_per_min
    return total


def rollup_cost(
    conn: sqlite3.Connection,
    material_id: str,
    on_date: str | date | None = None,
    _stack: tuple[str, ...] = (),
) -> float:
    """递归卷算单位标准成本（元 / base_unit）。"""
    material = repo.get_material(conn, material_id)
    if material is None:
        raise CostingError(f"物料不存在: {material_id}")
    if not material.is_make:
        return material.standard_cost
    if material_id in _stack:
        raise CostingError(f"BOM 存在循环: {' -> '.join(_stack + (material_id,))}")

    bom = repo.get_bom(conn, material_id, on_date)
    if bom is None:
        return material.standard_cost  # 没有 BOM 时退回主数据价格

    material_cost = 0.0
    for item in bom.components:
        child_cost = rollup_cost(conn, item.component_id, on_date, _stack + (material_id,))
        material_cost += item.qty_per_base / bom.base_qty * (1 + item.scrap_rate) * child_cost
    return material_cost + unit_labor_cost(conn, material_id)


def rollup_all(conn: sqlite3.Connection, on_date: str | date | None = None) -> dict[str, float]:
    """重算所有自制物料的标准成本并写回主数据。"""
    result: dict[str, float] = {}
    for material in repo.list_materials(conn):
        if material.is_make:
            cost = rollup_cost(conn, material.material_id, on_date)
            repo.update_material_cost(conn, material.material_id, cost)
            result[material.material_id] = cost
    return result


def order_cost_estimate(
    conn: sqlite3.Connection,
    material: Material,
    qty: float,
    on_date: str | date | None = None,
) -> tuple[float, float]:
    """返回 (预计材料成本, 预计人工成本)。"""
    material_cost = 0.0
    bom = repo.get_bom(conn, material.material_id, on_date)
    if bom:
        for item in bom.components:
            child = repo.get_material(conn, item.component_id)
            unit_cost = child.standard_cost if child else 0.0
            required = qty * item.qty_per_base / bom.base_qty * (1 + item.scrap_rate)
            material_cost += required * unit_cost

    labor_cost = 0.0
    for op in repo.get_routing(conn, material.material_id):
        wc = repo.get_work_center(conn, op.work_center_id)
        if wc:
            labor_cost += (op.setup_min + op.run_min_per_unit * qty) * wc.cost_rate_per_min
    return round(material_cost, 2), round(labor_cost, 2)
