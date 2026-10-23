"""MRP 核心：BOM 展开 + 库存净算 + 批量 + 提前期 + 有效期 + 生产订单/采购申请。

处理逻辑（按低层码逐层计算）：
  1. 取门店要货单（SD 接口）的成品需求；
  2. 按物料汇总需求，按需求日期升序模拟批次消耗（FEFO）：
        先剔除需求日前已过期的批次；可用量 = 批次结存 - 安全库存；
        净需求 = max(0, 毛需求 - 可用量)；计划入库按需求日到货并生成新批次；
        扣减本次需求后余额结转到下一个需求日；
  3. 净需求先满足最小起订量，再向上取整到批量倍数；
  4. 自制件生成生产订单（PLANNED），并按 BOM 把组件需求下推到下一层；
     外购件生成采购申请（OPEN），下单日期 = 需求日期 - 采购提前期；
  5. 生产订单按工艺路线做有限产能排产；
  6. 所有需求、净算、单号、异常写入数据库，形成可追溯的 MRP 运行记录。
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable

from . import repo
from .db import emit_event, next_number, transaction
from .models import Material
from .scheduling import schedule_orders
from .util import (
    QTY_EPS,
    ceil_to,
    fmt_qty,
    now_iso,
    round_money,
    round_qty,
    sub_days,
    to_date,
)


class MrpError(Exception):
    """MRP 业务异常基类。"""


class NoDemandError(MrpError):
    pass


class BomCycleError(MrpError):
    pass


@dataclass
class Requirement:
    material_id: str
    qty: float
    need_date: str
    parent_material_id: str | None = None
    sources: tuple[str, ...] = field(default_factory=tuple)


def _join(values: Iterable[str]) -> str:
    unique = sorted({v for v in values if v})
    if len(unique) <= 3:
        return ",".join(unique)
    return ",".join(unique[:3]) + f" 等{len(unique)}张"


def compute_low_level_codes(conn: sqlite3.Connection) -> dict[str, int]:
    """低层码 = 物料在 BOM 中出现的最深层级；同一物料的所有需求汇总到该层净算。"""
    products = repo.list_bom_products(conn)
    graph: dict[str, list[str]] = {}
    components: set[str] = set()
    for product in products:
        bom = repo.get_bom(conn, product)
        children = [c.component_id for c in bom.components] if bom else []
        graph[product] = children
        components.update(children)

    llc: dict[str, int] = {product: 0 for product in products}
    state: set[str] = set()

    def walk(material_id: str, depth: int) -> None:
        if material_id in state:
            raise BomCycleError(f"BOM 存在循环，涉及物料 {material_id}")
        state.add(material_id)
        for child in graph.get(material_id, []):
            if depth + 1 > llc.get(child, 0):
                llc[child] = depth + 1
            walk(child, depth + 1)
        state.remove(material_id)

    roots = [product for product in products if product not in components]
    for root in roots:
        walk(root, 0)
    for product in products:
        if product not in roots:
            walk(product, llc.get(product, 0))
    return llc


class MrpEngine:
    def __init__(
        self,
        conn: sqlite3.Connection,
        run_date: str | date | None = None,
        run_by: str = "郭晨阳（PP）",
    ) -> None:
        self.conn = conn
        self.run_date = to_date(run_date) or date.today()
        self.run_by = run_by
        self.materials: dict[str, Material] = {
            m.material_id: m for m in repo.list_materials(conn)
        }
        self.llc = compute_low_level_codes(conn)
        self.run_id = ""
        self.explosion: list[dict] = []
        self.exceptions: list[dict] = []
        self.order_ids: list[str] = []
        self.pr_ids: list[str] = []

    # -- 公共入口 ---------------------------------------------------------
    def run(
        self,
        requisition_ids: list[str] | None = None,
        horizon_days: int = 30,
        extra_demands: list[dict] | None = None,
    ) -> dict:
        with transaction(self.conn):
            return self._run(requisition_ids, horizon_days, extra_demands or [])

    # -- 内部实现 ---------------------------------------------------------
    def _run(
        self,
        requisition_ids: list[str] | None,
        horizon_days: int,
        extra_demands: list[dict],
    ) -> dict:
        requisitions = repo.list_requisitions(self.conn, status="SUBMITTED", requisition_ids=requisition_ids)
        if not requisitions and not extra_demands:
            raise NoDemandError("没有状态为 SUBMITTED 的门店要货单或其它独立需求，无法运行 MRP")

        self.run_id = next_number(self.conn, f"MRP-{self.run_date:%Y%m%d}")
        self.conn.execute(
            """INSERT INTO mrp_run
               (run_id, run_date, run_at, run_by, planning_horizon_days, status)
               VALUES (?, ?, ?, ?, ?, 'RUNNING')""",
            (self.run_id, self.run_date.isoformat(), now_iso(), self.run_by, horizon_days),
        )
        horizon_end = self.run_date + timedelta(days=horizon_days)
        level_reqs: dict[int, list[Requirement]] = defaultdict(list)
        included: list[str] = []

        for req in requisitions:
            required = to_date(req["required_date"])
            if required is None:
                continue
            if required > horizon_end:
                self._exception(
                    0,
                    None,
                    "OUT_OF_HORIZON",
                    "INFO",
                    f"要货单 {req['requisition_id']} 需求日期 {required} 超出计划展望期，本次不参与计算",
                )
                continue
            need = max(required, self.run_date)
            if required < self.run_date:
                self._exception(
                    0,
                    None,
                    "PAST_DUE_DEMAND",
                    "WARNING",
                    f"要货单 {req['requisition_id']} 需求日期 {required} 已过，按 {self.run_date} 紧急排产",
                )
            included.append(req["requisition_id"])
            for item in req["items"]:
                material = self.materials.get(item["product_id"])
                if material is None:
                    self._exception(
                        0, item["product_id"], "MATERIAL_NOT_FOUND", "ERROR",
                        f"要货单 {req['requisition_id']} 引用了不存在的物料 {item['product_id']}",
                    )
                    continue
                level = self.llc.get(material.material_id, 0)
                level_reqs[level].append(
                    Requirement(
                        material_id=material.material_id,
                        qty=float(item["qty"]),
                        need_date=need.isoformat(),
                        parent_material_id=None,
                        sources=(req["requisition_id"],),
                    )
                )

        accepted_extra = 0
        for index, demand in enumerate(extra_demands, start=1):
            product_id = demand.get("product_id")
            source_id = demand.get("source_id") or f"EXT-{index:04d}"
            required = to_date(demand.get("required_date"))
            qty = float(demand.get("qty") or 0)
            material = self.materials.get(product_id) if product_id else None
            if material is None or qty <= 0 or required is None:
                self._exception(
                    0, product_id, "INVALID_EXTRA_DEMAND", "ERROR",
                    f"独立需求 {source_id} 参数不完整或物料不存在",
                )
                continue
            if required > horizon_end:
                self._exception(
                    0, product_id, "OUT_OF_HORIZON", "INFO",
                    f"独立需求 {source_id} 需求日期 {required} 超出计划展望期，本次不参与计算",
                )
                continue
            need = max(required, self.run_date)
            level = self.llc.get(material.material_id, 0)
            level_reqs[level].append(
                Requirement(
                    material_id=material.material_id,
                    qty=qty,
                    need_date=need.isoformat(),
                    parent_material_id=None,
                    sources=(source_id,),
                )
            )
            accepted_extra += 1

        if not included and accepted_extra == 0:
            raise NoDemandError("展望期内没有需要计算的门店要货需求")

        max_level = max(self.llc.values(), default=0)
        for level in range(max_level + 1):
            pending = level_reqs.get(level, [])
            if not pending:
                continue
            grouped: dict[str, list[Requirement]] = defaultdict(list)
            for requirement in pending:
                grouped[requirement.material_id].append(requirement)
            for material_id in sorted(grouped):
                children = self._plan_material(material_id, grouped[material_id], level)
                for child in children:
                    child_level = self.llc.get(child.material_id, level + 1)
                    level_reqs[child_level].append(child)

        # 排产（有限产能）
        if self.order_ids:
            warnings = schedule_orders(self.conn, self.order_ids, self.run_date)
            for warning in warnings:
                self._exception(
                    None, None, warning["type"], "WARNING", warning["message"],
                    order_id=warning["order_id"],
                )
        # 排产可能把完成日期推后，输出订单级延误告警
        for order_id in self.order_ids:
            order = repo.get_production_order(self.conn, order_id)
            if not order or not order["planned_finish"]:
                continue
            required = to_date(order["required_date"])
            finish = to_date(order["planned_finish"])
            if required and finish and finish > required:
                # schedule_orders 已记录过的不重复
                exists = self.conn.execute(
                    """SELECT 1 FROM mrp_exception
                       WHERE run_id = ? AND exception_type = 'SCHEDULE_DELAY'
                         AND message LIKE ? LIMIT 1""",
                    (self.run_id, f"%{order_id}%"),
                ).fetchone()
                if not exists:
                    self._exception(
                        None, order["material_id"], "SCHEDULE_DELAY", "WARNING",
                        f"生产订单 {order_id} 排产完成 {finish} 晚于需求 {required}",
                        order_id=order_id,
                    )

        for req_id in included:
            self.conn.execute(
                "UPDATE store_requisition SET status = 'PLANNED', mrp_run_id = ? WHERE requisition_id = ?",
                (self.run_id, req_id),
            )

        exception_count = len(self.exceptions)
        self.conn.execute(
            """UPDATE mrp_run SET status = 'COMPLETED',
                   requisition_count = ?, production_order_count = ?,
                   purchase_requisition_count = ?, exception_count = ?
               WHERE run_id = ?""",
            (len(included), len(self.order_ids), len(self.pr_ids), exception_count, self.run_id),
        )

        return {
            "run_id": self.run_id,
            "run_date": self.run_date.isoformat(),
            "run_at": now_iso(),
            "run_by": self.run_by,
            "stats": {
                "requisition_count": len(included),
                "production_order_count": len(self.order_ids),
                "purchase_requisition_count": len(self.pr_ids),
                "exception_count": exception_count,
                "extra_demand_count": accepted_extra,
            },
            "production_order_ids": list(self.order_ids),
            "purchase_requisition_ids": list(self.pr_ids),
            "exceptions": list(self.exceptions),
        }

    # -- 单个物料的净算 ---------------------------------------------------
    def _plan_material(
        self,
        material_id: str,
        requirements: list[Requirement],
        level: int,
    ) -> list[Requirement]:
        material = self.materials[material_id]
        by_date: dict[str, float] = defaultdict(float)
        parents: dict[str, set[str]] = defaultdict(set)
        sources: dict[str, set[str]] = defaultdict(set)
        for requirement in requirements:
            by_date[requirement.need_date] += requirement.qty
            if requirement.parent_material_id:
                parents[requirement.need_date].add(requirement.parent_material_id)
            sources[requirement.need_date].update(requirement.sources)

        batches = repo.get_batches(self.conn, material_id)
        first_need = min(by_date)
        expired_free = sum(
            batch.free_qty
            for batch in batches
            if batch.status == "UNRESTRICTED"
            and batch.expiry_date is not None
            and batch.expiry_date < to_date(first_need)  # type: ignore[operator]
        )
        if expired_free > QTY_EPS:
            self._exception(
                level,
                material_id,
                "SHELF_LIFE_EXPIRY",
                "WARNING",
                (
                    f"{material.material_name} 有 {fmt_qty(expired_free, material.base_unit)} "
                    f"在首个需求日 {first_need} 前到期，不参与净算，请 MM 安排临期处理"
                ),
            )

        children: list[Requirement] = []
        # 用批次模拟 FEFO：期初批次 + 本次计划入库；先到期先出，过期批次直接损耗。
        lots: list[list] = [
            [batch.expiry_date, batch.free_qty]
            for batch in batches
            if batch.free_qty > QTY_EPS and batch.status == "UNRESTRICTED"
        ]
        lots.sort(key=lambda lot: (lot[0] is None, lot[0] or date.max))
        total_stock = sum(lot[1] for lot in lots)

        for need_date in sorted(by_date):
            need_day = to_date(need_date)
            # 需求日前已过期的批次不参与净算
            expired = sum(
                lot[1] for lot in lots if lot[0] is not None and lot[0] < need_day  # type: ignore[operator]
            )
            if expired > QTY_EPS:
                lots = [
                    lot for lot in lots
                    if lot[0] is None or lot[0] >= need_day  # type: ignore[operator]
                ]
                total_stock -= expired

            gross = round_qty(by_date[need_date])
            usable_stock = round_qty(total_stock - material.safety_stock)
            net_qty = round_qty(max(0.0, gross - usable_stock))

            order_type = "NONE"
            order_qty = 0.0
            release_date: str | None = None

            if net_qty > QTY_EPS:
                order_qty = max(net_qty, material.min_order_qty)
                order_qty = round_qty(ceil_to(order_qty, material.lot_size))

                if material.is_make:
                    order_type = "PRODUCTION"
                    release_date = sub_days(need_date, material.lead_time_days)
                    order_id, child_reqs = self._create_production_order(
                        material, order_qty, need_date, level, sources[need_date], parents[need_date]
                    )
                    self.order_ids.append(order_id)
                    children.extend(child_reqs)
                else:
                    order_type = "PURCHASE"
                    release_date = sub_days(need_date, material.lead_time_days)
                    self._create_purchase_requisition(
                        material, order_qty, need_date, release_date, level, sources[need_date]
                    )

                # 计划入库视为需求日可用；按物料保质期生成新批次
                new_expiry = None
                if material.shelf_life_days:
                    new_expiry = (need_day + timedelta(days=material.shelf_life_days))  # type: ignore[operator]
                lots.append([new_expiry, order_qty])
                total_stock += order_qty

                if to_date(release_date) < self.run_date:  # type: ignore[operator]
                    self._exception(
                        level,
                        material_id,
                        "LEAD_TIME_SHORTAGE",
                        "WARNING",
                        (
                            f"{material.material_name} 需求日 {need_date}，提前期 {material.lead_time_days} 天，"
                            f"应于 {release_date} 下单/开工，已早于运行日 {self.run_date}，需紧急处理"
                        ),
                    )

            # 消耗本次需求（FEFO），保持安全库存不被占用
            remaining_demand = gross
            lots.sort(key=lambda lot: (lot[0] is None, lot[0] or date.max))
            for lot in lots:
                if remaining_demand <= QTY_EPS:
                    break
                take = min(lot[1], remaining_demand)
                lot[1] -= take
                remaining_demand -= take
                total_stock -= take
            lots = [lot for lot in lots if lot[1] > QTY_EPS]
            if remaining_demand > QTY_EPS:  # 理论上不会发生，兜底
                self._exception(
                    level,
                    material_id,
                    "STOCK_SIMULATION_ERROR",
                    "ERROR",
                    f"{material.material_name} 在 {need_date} 的库存模拟出现缺口 {remaining_demand}",
                )

            self.explosion.append(
                {
                    "run_id": self.run_id,
                    "level": level,
                    "parent_material_id": ",".join(sorted(parents[need_date])) or None,
                    "material_id": material_id,
                    "gross_qty": gross,
                    "stock_available": usable_stock,
                    "net_qty": net_qty,
                    "order_qty": order_qty,
                    "order_type": order_type,
                    "required_date": need_date,
                    "release_date": release_date,
                    "source_requisition_id": ",".join(sorted(sources[need_date])),
                }
            )
            self.conn.execute(
                """INSERT INTO mrp_explosion
                   (run_id, level, parent_material_id, material_id, gross_qty, stock_available,
                    net_qty, order_qty, order_type, required_date, release_date, source_requisition_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self.run_id,
                    level,
                    ",".join(sorted(parents[need_date])) or None,
                    material_id,
                    gross,
                    usable_stock,
                    net_qty,
                    order_qty,
                    order_type,
                    need_date,
                    release_date,
                    ",".join(sorted(sources[need_date])),
                ),
            )
        return children

    # -- 生成生产订单 -----------------------------------------------------
    def _create_production_order(
        self,
        material: Material,
        qty: float,
        need_date: str,
        level: int,
        sources: set[str],
        parents: set[str],
    ) -> tuple[str, list[Requirement]]:
        start_date = sub_days(need_date, material.lead_time_days)
        order_id = next_number(self.conn, f"PO-{self.run_date:%Y%m%d}")
        bom = repo.get_bom(self.conn, material.material_id, start_date)
        if bom is None:
            self._exception(
                level,
                material.material_id,
                "BOM_MISSING",
                "ERROR",
                f"{material.material_name} 为自制件但没有生效 BOM，无法展开组件需求",
            )

        material_cost = 0.0
        components: list[tuple[int, str, float, float]] = []
        children: list[Requirement] = []
        if bom:
            for line, item in enumerate(bom.components, start=10):
                required = qty * item.qty_per_base / bom.base_qty * (1 + item.scrap_rate)
                required = round_qty(required)
                child = self.materials.get(item.component_id)
                unit_cost = child.standard_cost if child else 0.0
                material_cost += required * unit_cost
                components.append((line, item.component_id, required, unit_cost))
                children.append(
                    Requirement(
                        material_id=item.component_id,
                        qty=required,
                        need_date=start_date,
                        parent_material_id=material.material_id,
                        sources=tuple(sorted(sources)),
                    )
                )

        labor_cost = 0.0
        for op in repo.get_routing(self.conn, material.material_id):
            wc = repo.get_work_center(self.conn, op.work_center_id)
            if wc:
                labor_cost += (op.setup_min + op.run_min_per_unit * qty) * wc.cost_rate_per_min

        self.conn.execute(
            """INSERT INTO production_order
               (order_id, material_id, qty_planned, status, mrp_run_id, source_requisition_id,
                required_date, start_date, estimated_material_cost, estimated_labor_cost, created_at)
               VALUES (?, ?, ?, 'PLANNED', ?, ?, ?, ?, ?, ?, ?)""",
            (
                order_id,
                material.material_id,
                qty,
                self.run_id,
                ",".join(sorted(sources)),
                need_date,
                start_date,
                round_money(material_cost),
                round_money(labor_cost),
                now_iso(),
            ),
        )
        for line, component_id, required, unit_cost in components:
            self.conn.execute(
                """INSERT INTO production_order_component
                   (order_id, line_no, material_id, qty_required, unit_cost)
                   VALUES (?, ?, ?, ?, ?)""",
                (order_id, line, component_id, required, unit_cost),
            )
        for op in repo.get_routing(self.conn, material.material_id):
            self.conn.execute(
                """INSERT INTO production_order_operation
                   (order_id, op_no, operation_name, work_center_id, setup_min, run_min_per_unit)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    order_id,
                    op.op_no,
                    op.operation_name,
                    op.work_center_id,
                    op.setup_min,
                    op.run_min_per_unit,
                ),
            )

        emit_event(
            self.conn,
            "PRODUCTION_ORDER_CREATED",
            order_id,
            {
                "order_id": order_id,
                "material_id": material.material_id,
                "qty": qty,
                "required_date": need_date,
                "start_date": start_date,
                "source_requisition_id": sorted(sources),
            },
        )
        return order_id, children

    # -- 生成采购申请 -----------------------------------------------------
    def _create_purchase_requisition(
        self,
        material: Material,
        qty: float,
        need_date: str,
        release_date: str,
        level: int,
        sources: set[str],
    ) -> str:
        pr_id = next_number(self.conn, f"PR-{self.run_date:%Y%m%d}")
        self.conn.execute(
            """INSERT INTO purchase_requisition
               (pr_id, material_id, qty, unit, required_date, release_date, status,
                mrp_run_id, source_requisition_id, unit_cost, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)""",
            (
                pr_id,
                material.material_id,
                qty,
                material.base_unit,
                need_date,
                release_date,
                self.run_id,
                ",".join(sorted(sources)),
                material.standard_cost,
                now_iso(),
            ),
        )
        self.pr_ids.append(pr_id)
        emit_event(
            self.conn,
            "PURCHASE_REQUISITION_CREATED",
            pr_id,
            {
                "pr_id": pr_id,
                "material_id": material.material_id,
                "qty": qty,
                "unit": material.base_unit,
                "required_date": need_date,
                "release_date": release_date,
                "estimated_amount": round_money(qty * material.standard_cost),
            },
        )
        return pr_id

    # -- 异常 -------------------------------------------------------------
    def _exception(
        self,
        level: int | None,
        material_id: str | None,
        exception_type: str,
        severity: str,
        message: str,
        order_id: str | None = None,
    ) -> None:
        if order_id:
            message = f"[{order_id}] {message}"
        self.conn.execute(
            """INSERT INTO mrp_exception
               (run_id, level, material_id, exception_type, severity, message, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (self.run_id, level, material_id, exception_type, severity, message, now_iso()),
        )
        self.exceptions.append(
            {
                "level": level,
                "material_id": material_id,
                "exception_type": exception_type,
                "severity": severity,
                "message": message,
            }
        )


def run_mrp(
    conn: sqlite3.Connection,
    run_date: str | date | None = None,
    requisition_ids: list[str] | None = None,
    horizon_days: int = 30,
    run_by: str = "郭晨阳（PP）",
    extra_demands: list[dict] | None = None,
) -> dict:
    return MrpEngine(conn, run_date=run_date, run_by=run_by).run(
        requisition_ids=requisition_ids,
        horizon_days=horizon_days,
        extra_demands=extra_demands,
    )
