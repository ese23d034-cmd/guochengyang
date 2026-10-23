"""MM 演示数据：物料/BOM/供应商/仓库/期初批次/采购申请。"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from .db import DEFAULT_DB_PATH, connect, init_db, reset_db
from .util import round_money

RUN_DATE = "2026-10-23"

WAREHOUSES = [
    ("WH-RAW", "原料仓", "RAW", "常温"),
    ("WH-COLD", "冷链仓", "COLD", "0-4℃"),
    ("WH-FG", "成品仓", "FINISHED", "0-4℃"),
]

SUPPLIERS = [
    ("SUP-GRAIN", "五常米业供应链", "GRAIN", 1, 4.8, "王经理 138-0000-1001"),
    ("SUP-MEAT", "鲜联肉食品", "MEAT", 1, 4.7, "李经理 138-0000-1002"),
    ("SUP-VEG", "绿源生鲜配送", "VEG", 1, 4.6, "周经理 138-0000-1003"),
    ("SUP-PACK", "恒信包材", "PACKAGING", 2, 4.5, "陈经理 138-0000-1004"),
]

# (id, 名称, 类型, 单位, 采购类型, 标准成本, 安全库存, MOQ, 批量, 提前期, 保质期, 默认仓, 默认供应商)
MATERIALS = [
    ("FG-001", "招牌牛肉米粉", "FG", "碗", "IN_HOUSE", 8.97, 0, 0, 0, 0, 3, "WH-FG", None),
    ("FG-002", "酸辣肥肠米粉", "FG", "碗", "IN_HOUSE", 7.65, 0, 0, 0, 0, 3, "WH-FG", None),
    ("SF-001", "鲜湿米粉", "SF", "kg", "IN_HOUSE", 7.11, 0, 10, 5, 1, 3, "WH-COLD", None),
    ("SF-002", "红烧牛肉臊子", "SF", "kg", "IN_HOUSE", 53.21, 0, 10, 10, 1, 5, "WH-COLD", None),
    ("SF-003", "酸辣肥肠臊子", "SF", "kg", "IN_HOUSE", 30.14, 0, 5, 5, 1, 5, "WH-COLD", None),
    ("RM-001", "大米", "RM", "kg", "EXTERNAL", 5.20, 50, 25, 25, 1, 365, "WH-RAW", "SUP-GRAIN"),
    ("RM-002", "食用淀粉", "RM", "kg", "EXTERNAL", 6.00, 5, 5, 5, 2, 365, "WH-RAW", "SUP-GRAIN"),
    ("RM-003", "食用油", "RM", "kg", "EXTERNAL", 12.00, 5, 5, 5, 2, 180, "WH-RAW", "SUP-GRAIN"),
    ("RM-004", "牛腩", "RM", "kg", "EXTERNAL", 68.00, 5, 10, 5, 1, 3, "WH-COLD", "SUP-MEAT"),
    ("RM-005", "生姜", "RM", "kg", "EXTERNAL", 14.00, 2, 1, 1, 1, 30, "WH-RAW", "SUP-VEG"),
    ("RM-006", "生抽", "RM", "kg", "EXTERNAL", 8.00, 5, 5, 5, 2, 365, "WH-RAW", "SUP-GRAIN"),
    ("RM-007", "香料包", "RM", "kg", "EXTERNAL", 30.00, 1, 0.5, 0.5, 3, 180, "WH-RAW", "SUP-GRAIN"),
    ("RM-008", "猪肥肠", "RM", "kg", "EXTERNAL", 36.00, 5, 10, 5, 1, 3, "WH-COLD", "SUP-MEAT"),
    ("RM-009", "酸笋", "RM", "kg", "EXTERNAL", 9.00, 3, 5, 5, 2, 90, "WH-RAW", "SUP-VEG"),
    ("RM-010", "青菜", "RM", "kg", "EXTERNAL", 5.00, 3, 1, 1, 1, 2, "WH-COLD", "SUP-VEG"),
    ("RM-011", "香葱香菜包", "PK", "包", "EXTERNAL", 1.20, 100, 100, 100, 2, 5, "WH-COLD", "SUP-PACK"),
    ("RM-012", "酸豆角包", "PK", "包", "EXTERNAL", 1.50, 50, 50, 50, 2, 90, "WH-RAW", "SUP-PACK"),
    ("RM-013", "辣椒油", "RM", "kg", "EXTERNAL", 18.00, 2, 1, 1, 2, 180, "WH-RAW", "SUP-VEG"),
    ("PK-001", "环保碗盖", "PK", "套", "EXTERNAL", 0.85, 100, 1000, 500, 2, 365, "WH-RAW", "SUP-PACK"),
    ("PK-002", "一次性餐具包", "PK", "套", "EXTERNAL", 0.35, 100, 500, 500, 2, 365, "WH-RAW", "SUP-PACK"),
]

BOMS = [
    ("BOM-FG-001", "FG-001", "V1", 1, [
        (10, "SF-001", 0.25, 0.02), (20, "SF-002", 0.08, 0.01),
        (30, "RM-010", 0.05, 0.08), (40, "RM-011", 1.00, 0.00),
        (50, "PK-001", 1.00, 0.01), (60, "PK-002", 1.00, 0.00),
    ]),
    ("BOM-FG-002", "FG-002", "V1", 1, [
        (10, "SF-001", 0.25, 0.02), (20, "SF-003", 0.09, 0.01),
        (30, "RM-010", 0.04, 0.08), (40, "RM-012", 1.00, 0.00),
        (50, "PK-001", 1.00, 0.01), (60, "PK-002", 1.00, 0.00),
    ]),
    ("BOM-SF-001", "SF-001", "V1", 1, [
        (10, "RM-001", 1.05, 0.005), (20, "RM-002", 0.02, 0.00), (30, "RM-003", 0.005, 0.00),
    ]),
    ("BOM-SF-002", "SF-002", "V1", 1, [
        (10, "RM-004", 0.72, 0.03), (20, "RM-005", 0.02, 0.01),
        (30, "RM-006", 0.05, 0.00), (40, "RM-007", 0.01, 0.00),
    ]),
    ("BOM-SF-003", "SF-003", "V1", 1, [
        (10, "RM-008", 0.70, 0.05), (20, "RM-009", 0.10, 0.02),
        (30, "RM-005", 0.02, 0.01), (40, "RM-006", 0.04, 0.00),
        (50, "RM-013", 0.03, 0.00),
    ]),
]

BATCHES = [
    ("B-RICE-001", "RM-001", "WH-RAW", "RICE-20261001", 80, "2026-10-01", "2027-04-30", 5.20),
    ("B-STARCH-001", "RM-002", "WH-RAW", "STARCH-20260701", 8, "2026-07-01", "2027-06-01", 6.00),
    ("B-OIL-001", "RM-003", "WH-RAW", "OIL-20260901", 10, "2026-09-01", "2027-03-01", 12.00),
    ("B-BEEF-001", "RM-004", "WH-COLD", "BEEF-20261021", 3, "2026-10-21", "2026-10-24", 68.00),
    ("B-GINGER-001", "RM-005", "WH-RAW", "GINGER-20261010", 1, "2026-10-10", "2026-11-30", 14.00),
    ("B-SOY-001", "RM-006", "WH-RAW", "SOY-20260901", 15, "2026-09-01", "2027-03-01", 8.00),
    ("B-SPICE-001", "RM-007", "WH-RAW", "SPICE-20260701", 2, "2026-07-01", "2027-01-01", 30.00),
    ("B-BAMBOO-001", "RM-009", "WH-RAW", "BAMBOO-20260901", 5, "2026-09-01", "2026-12-31", 9.00),
    ("B-VEG-001", "RM-010", "WH-COLD", "VEG-20261022", 6, "2026-10-22", "2026-10-24", 5.00),
    ("B-SCALLION-001", "RM-011", "WH-COLD", "SCALLION-20261020", 150, "2026-10-20", "2026-10-28", 1.20),
    ("B-BEAN-001", "RM-012", "WH-RAW", "BEAN-20260801", 60, "2026-08-01", "2027-01-15", 1.50),
    ("B-CHILI-001", "RM-013", "WH-RAW", "CHILI-20260801", 3, "2026-08-01", "2027-02-01", 18.00),
    ("B-BOWL-001", "PK-001", "WH-RAW", "BOWL-20260801", 500, None, None, 0.85),
    ("B-CUTLERY-001", "PK-002", "WH-RAW", "CUTLERY-20260801", 800, None, None, 0.35),
    ("B-NOODLE-001", "SF-001", "WH-COLD", "NOODLE-20261022", 8, "2026-10-22", "2026-10-27", 7.11),
]

# PP MRP 输出给 MM 的采购申请（对应 310 碗牛肉米粉 + 120 碗肥肠米粉场景）
REQUISITIONS = [
    ("PR-20261023-0001", "RM-001", 100, "kg", "2026-10-25", "2026-10-24", 5.20),
    ("PR-20261023-0002", "RM-004", 30, "kg", "2026-10-25", "2026-10-24", 68.00),
    ("PR-20261023-0003", "RM-005", 2, "kg", "2026-10-25", "2026-10-24", 14.00),
    ("PR-20261023-0004", "RM-008", 20, "kg", "2026-10-25", "2026-10-24", 36.00),
    ("PR-20261023-0005", "RM-010", 25, "kg", "2026-10-26", "2026-10-25", 5.00),
    ("PR-20261023-0006", "RM-011", 300, "包", "2026-10-26", "2026-10-24", 1.20),
    ("PR-20261023-0007", "RM-012", 150, "包", "2026-10-26", "2026-10-24", 1.50),
    ("PR-20261023-0008", "PK-001", 1000, "套", "2026-10-26", "2026-10-24", 0.85),
]

MRP_RUN_ID = "MRP-20261023-0001"


def seed_all(conn: sqlite3.Connection) -> dict:
    for table in (
        "integration_event", "mm_goods_issue_item", "mm_goods_issue",
        "mm_receipt_item", "mm_receipt", "purchase_order", "purchase_requisition",
        "inventory_movement", "inventory_batch", "bom_item", "bom",
        "material", "supplier", "warehouse", "number_range",
    ):
        conn.execute(f"DELETE FROM {table}")

    conn.executemany(
        "INSERT INTO warehouse(warehouse_id, warehouse_name, warehouse_type, temperature_zone) VALUES (?, ?, ?, ?)",
        WAREHOUSES,
    )
    conn.executemany(
        """INSERT INTO supplier(supplier_id, supplier_name, category, lead_time_days, rating, contact)
           VALUES (?, ?, ?, ?, ?, ?)""",
        SUPPLIERS,
    )
    conn.executemany(
        """INSERT INTO material
           (material_id, material_name, material_type, base_unit, procurement_type,
            standard_cost, safety_stock, min_order_qty, lot_size, lead_time_days,
            shelf_life_days, default_warehouse_id, default_supplier_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        MATERIALS,
    )
    for bom_id, product, version, base_qty, items in BOMS:
        conn.execute(
            """INSERT INTO bom(bom_id, product_id, version, base_qty, status, valid_from)
               VALUES (?, ?, ?, ?, 'ACTIVE', '2026-01-01')""",
            (bom_id, product, version, base_qty),
        )
        conn.executemany(
            """INSERT INTO bom_item(bom_id, line_no, component_id, qty_per_base, scrap_rate)
               VALUES (?, ?, ?, ?, ?)""",
            [(bom_id, line, comp, qty, scrap) for line, comp, qty, scrap in items],
        )
    conn.executemany(
        """INSERT INTO inventory_batch
           (batch_id, material_id, warehouse_id, batch_no, qty_on_hand,
            production_date, expiry_date, unit_cost, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'UNRESTRICTED')""",
        BATCHES,
    )
    conn.executemany(
        """INSERT INTO purchase_requisition
           (pr_id, mrp_run_id, material_id, qty, unit, required_date, release_date,
            status, unit_cost, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, '2026-10-23 18:00:00')""",
        [(pr, MRP_RUN_ID, mat, qty, unit, req, rel, cost)
         for pr, mat, qty, unit, req, rel, cost in REQUISITIONS],
    )
    conn.commit()
    return {
        "materials": len(MATERIALS),
        "boms": len(BOMS),
        "suppliers": len(SUPPLIERS),
        "batches": len(BATCHES),
        "requisitions": len(REQUISITIONS),
        "inventory_value": round_money(
            conn.execute(
                "SELECT COALESCE(SUM(qty_on_hand * unit_cost), 0) AS v FROM inventory_batch"
            ).fetchone()["v"]
        ),
    }


def init_and_seed(db_path: str | Path = DEFAULT_DB_PATH, reset: bool = True) -> sqlite3.Connection:
    if reset:
        reset_db(db_path)
    conn = connect(db_path)
    init_db(conn)
    seed_all(conn)
    return conn


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化云谷鲜米粉 MM 模块演示数据库")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    conn = init_and_seed(args.db, reset=args.reset)
    print(f"数据库已初始化: {Path(args.db).resolve()}")
    print(
        f"  物料 {len(MATERIALS)} / BOM {len(BOMS)} / 供应商 {len(SUPPLIERS)} / "
        f"期初批次 {len(BATCHES)} / 采购申请 {len(REQUISITIONS)}"
    )
    conn.close()


if __name__ == "__main__":
    main()
