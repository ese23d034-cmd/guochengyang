"""MM 领域对象。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Material:
    material_id: str
    material_name: str
    material_type: str
    base_unit: str
    procurement_type: str
    standard_cost: float
    safety_stock: float
    min_order_qty: float
    lot_size: float
    lead_time_days: int
    shelf_life_days: int | None
    default_warehouse_id: str | None
    default_supplier_id: str | None
    is_active: bool

    @property
    def is_external(self) -> bool:
        return self.procurement_type == "EXTERNAL"


@dataclass(frozen=True)
class Supplier:
    supplier_id: str
    supplier_name: str
    category: str
    lead_time_days: int
    rating: float
    contact: str | None
    status: str


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
    status: str
    components: tuple[BomComponent, ...]


@dataclass(frozen=True)
class Batch:
    batch_id: str
    material_id: str
    warehouse_id: str
    batch_no: str
    qty_on_hand: float
    production_date: str | None
    expiry_date: date | None
    unit_cost: float
    status: str

    def available_on(self, need_date: date) -> bool:
        return self.status == "UNRESTRICTED" and (
            self.expiry_date is None or self.expiry_date >= need_date
        )
