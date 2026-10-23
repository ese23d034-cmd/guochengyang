"""领域对象（只读快照）。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Material:
    material_id: str
    material_name: str
    material_type: str          # FG / SF / RM / PK
    base_unit: str
    procurement_type: str       # IN_HOUSE / EXTERNAL
    standard_cost: float
    list_price: float | None
    safety_stock: float
    min_order_qty: float
    lot_size: float
    lead_time_days: int
    shelf_life_days: int | None
    default_warehouse_id: str | None
    is_active: bool

    @property
    def is_make(self) -> bool:
        return self.procurement_type == "IN_HOUSE"


@dataclass(frozen=True)
class BomComponent:
    bom_id: str
    product_id: str
    base_qty: float
    line_no: int
    component_id: str
    qty_per_base: float
    scrap_rate: float


@dataclass(frozen=True)
class Bom:
    bom_id: str
    product_id: str
    version: str
    base_qty: float
    components: tuple[BomComponent, ...]


@dataclass(frozen=True)
class WorkCenter:
    work_center_id: str
    work_center_name: str
    daily_capacity_min: float
    cost_rate_per_min: float


@dataclass(frozen=True)
class RoutingOp:
    material_id: str
    op_no: int
    operation_name: str
    work_center_id: str
    setup_min: float
    run_min_per_unit: float


@dataclass(frozen=True)
class Batch:
    batch_id: str
    material_id: str
    warehouse_id: str
    batch_no: str
    qty_on_hand: float
    qty_reserved: float
    production_date: str | None
    expiry_date: date | None
    unit_cost: float
    status: str

    @property
    def free_qty(self) -> float:
        return max(0.0, self.qty_on_hand - self.qty_reserved)

    def available_on(self, need_date: date) -> bool:
        return self.status == "UNRESTRICTED" and (
            self.expiry_date is None or self.expiry_date >= need_date
        )
