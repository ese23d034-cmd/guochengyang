"""云谷鲜米粉演示数据：42 家门店中的 3 家、1 个中央工厂、2 个成品、3 个半成品。

运行：
    python -m pp.seed --db data/yungu_pp.db --reset
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from . import costing
from .db import DEFAULT_DB_PATH, connect, init_db, reset_db
from .util import round_money

RUN_DATE = "2026-10-23"

WAREHOUSES = [
    ("WH-RAW", "原料仓", "RAW", "常温"),
    ("WH-COLD", "冷链仓", "COLD", "0-4℃"),
    ("WH-FG", "成品仓", "FINISHED", "0-4℃"),
]

STORES = [
    ("ST-A", "云谷旗舰店", "杭州"),
    ("ST-B", "滨江店", "杭州"),
    ("ST-C", "城西店", "杭州"),
]

WORK_CENTERS = [
    ("WC-NOODLE", "米粉生产线", 720, 0.80),
    ("WC-SOUP", "臊子炖煮线", 600, 0.90),
    ("WC-PACK", "成品包装线", 720, 0.70),
]

# (id, 名称, 类型, 单位, 采购类型, 标准成本, 售价, 安全库存, 最小起订, 批量, 提前期, 保质期, 默认仓)
MATERIALS = [
    ("FG-001", "招牌牛肉米粉", "FG", "碗", "IN_HOUSE", 0, 18.00, 0, 0, 0, 0, 3, "WH-FG"),
    ("FG-002", "酸辣肥肠米粉", "FG", "碗", "IN_HOUSE", 0, 16.00, 0, 0, 0, 0, 3, "WH-FG"),
    ("SF-001", "鲜湿米粉", "SF", "kg", "IN_HOUSE", 0, None, 0, 10, 5, 1, 3, "WH-COLD"),
    ("SF-002", "红烧牛肉臊子", "SF", "kg", "IN_HOUSE", 0, None, 0, 10, 10, 1, 5, "WH-COLD"),
    ("SF-003", "酸辣肥肠臊子", "SF", "kg", "IN_HOUSE", 0, None, 0, 5, 5, 1, 5, "WH-COLD"),
    ("RM-001", "大米", "RM", "kg", "EXTERNAL", 5.20, None, 50, 25, 25, 1, 365, "WH-RAW"),
    ("RM-002", "食用淀粉", "RM", "kg", "EXTERNAL", 6.00, None, 5, 5, 5, 2, 365, "WH-RAW"),
    ("RM-003", "食用油", "RM", "kg", "EXTERNAL", 12.00, None, 5, 5, 5, 2, 180, "WH-RAW"),
    ("RM-004", "牛腩", "RM", "kg", "EXTERNAL", 68.00, None, 5, 10, 5, 1, 3, "WH-COLD"),
    ("RM-005", "生姜", "RM", "kg", "EXTERNAL", 14.00, None, 2, 1, 1, 1, 30, "WH-RAW"),
    ("RM-006", "生抽", "RM", "kg", "EXTERNAL", 8.00, None, 5, 5, 5, 2, 365, "WH-RAW"),
    ("RM-007", "香料包", "RM", "kg", "EXTERNAL", 30.00, None, 1, 0.5, 0.5, 3, 180, "WH-RAW"),
    ("RM-008", "猪肥肠", "RM", "kg", "EXTERNAL", 36.00, None, 5, 10, 5, 1, 3, "WH-COLD"),
    ("RM-009", "酸笋", "RM", "kg", "EXTERNAL", 9.00, None, 3, 5, 5, 2, 90, "WH-RAW"),
    ("RM-010", "青菜", "RM", "kg", "EXTERNAL", 5.00, None, 3, 1, 1, 1, 2, "WH-COLD"),
    ("RM-011", "香葱香菜包", "PK", "包", "EXTERNAL", 1.20, None, 100, 100, 100, 2, 5, "WH-COLD"),
    ("RM-012", "酸豆角包", "PK", "包", "EXTERNAL", 1.50, None, 50, 50, 50, 2, 90, "WH-RAW"),
    ("RM-013", "辣椒油", "RM", "kg", "EXTERNAL", 18.00, None, 2, 1, 1, 2, 180, "WH-RAW"),
    ("PK-001", "环保碗盖", "PK", "套", "EXTERNAL", 0.85, None, 100, 1000, 500, 2, 365, "WH-RAW"),
    ("PK-002", "一次性餐具包", "PK", "套", "EXTERNAL", 0.35, None, 100, 500, 500, 2, 365, "WH-RAW"),
]

# (bom_id, 产品, 版本, 基准数量, [(行号, 组件, 单位用量, 损耗率)])
BOMS = [
    (
        "BOM-FG-001", "FG-001", "V1", 1,
        [
            (10, "SF-001", 0.25, 0.02),
            (20, "SF-002", 0.08, 0.01),
            (30, "RM-010", 0.05, 0.08),
            (40, "RM-011", 1.00, 0.00),
            (50, "PK-001", 1.00, 0.01),
            (60, "PK-002", 1.00, 0.00),
        ],
    ),
    (
        "BOM-FG-002", "FG-002", "V1", 1,
        [
            (10, "SF-001", 0.25, 0.02),
            (20, "SF-003", 0.09, 0.01),
            (30, "RM-010", 0.04, 0.08),
            (40, "RM-012", 1.00, 0.00),
            (50, "PK-001", 1.00, 0.01),
            (60, "PK-002", 1.00, 0.00),
        ],
    ),
    (
        "BOM-SF-001", "SF-001", "V1", 1,
        [
            (10, "RM-001", 1.05, 0.005),
            (20, "RM-002", 0.02, 0.00),
            (30, "RM-003", 0.005, 0.00),
        ],
    ),
    (
        "BOM-SF-002", "SF-002", "V1", 1,
        [
            (10, "RM-004", 0.72, 0.03),
            (20, "RM-005", 0.02, 0.01),
            (30, "RM-006", 0.05, 0.00),
            (40, "RM-007", 0.01, 0.00),
        ],
    ),
    (
        "BOM-SF-003", "SF-003", "V1", 1,
        [
            (10, "RM-008", 0.70, 0.05),
            (20, "RM-009", 0.10, 0.02),
            (30, "RM-005", 0.02, 0.01),
            (40, "RM-006", 0.04, 0.00),
            (50, "RM-013", 0.03, 0.00),
        ],
    ),
]

ROUTINGS = [
    ("FG-001", 10, "配料装碗", "WC-PACK", 10, 0.12),
    ("FG-001", 20, "封盖贴标", "WC-PACK", 5, 0.08),
    ("FG-001", 30, "装箱入库", "WC-PACK", 5, 0.05),
    ("FG-002", 10, "配料装碗", "WC-PACK", 10, 0.12),
    ("FG-002", 20, "封盖贴标", "WC-PACK", 5, 0.08),
    ("FG-002", 30, "装箱入库", "WC-PACK", 5, 0.05),
    ("SF-001", 10, "大米浸泡磨浆", "WC-NOODLE", 30, 0.60),
    ("SF-001", 20, "挤丝成型", "WC-NOODLE", 20, 0.50),
    ("SF-001", 30, "蒸煮", "WC-NOODLE", 15, 0.40),
    ("SF-001", 40, "冷却分装", "WC-NOODLE", 10, 0.30),
    ("SF-002", 10, "牛肉切配", "WC-SOUP", 15, 0.50),
    ("SF-002", 20, "红烧炖煮", "WC-SOUP", 20, 1.20),
    ("SF-002", 30, "冷却分装", "WC-SOUP", 10, 0.30),
    ("SF-003", 10, "肥肠清洗切配", "WC-SOUP", 25, 0.60),
    ("SF-003", 20, "酸辣炒制", "WC-SOUP", 15, 0.90),
    ("SF-003", 30, "冷却分装", "WC-SOUP", 10, 0.30),
]

# 期初库存（batch_id, 物料, 仓库, 批次号, 数量, 生产日期, 到期日, 单位成本）
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
    ("B-NOODLE-001", "SF-001", "WH-COLD", "NOODLE-20261022", 8, "2026-10-22", "2026-10-27", 7.74),
]

REQUISITIONS = [
    {
        "id": "REQ-20261023-001",
        "store": "ST-B",
        "request": "2026-10-23",
        "required": "2026-10-26",
        "items": [("FG-001", 200)],
        "remark": "周六备货，牛肉米粉为主",
    },
    {
        "id": "REQ-20261023-002",
        "store": "ST-C",
        "request": "2026-10-23",
        "required": "2026-10-26",
        "items": [("FG-001", 80), ("FG-002", 120)],
        "remark": "新增酸辣肥肠米粉试销",
    },
]


def seed_all(conn: sqlite3.Connection) -> dict:
    """清空并写入演示主数据（调用前需已建表）。"""
    for table in (
        "fi_voucher_line", "fi_voucher", "integration_event", "inventory_reservation",
        "inventory_movement", "production_confirmation", "production_order_operation",
        "production_order_component", "production_order", "purchase_requisition",
        "mrp_exception", "mrp_explosion", "mrp_run", "store_requisition_item",
        "store_requisition", "inventory_batch", "bom_item", "bom", "routing",
        "work_center", "material", "store", "warehouse", "number_range",
    ):
        conn.execute(f"DELETE FROM {table}")

    conn.executemany(
        "INSERT INTO warehouse(warehouse_id, warehouse_name, warehouse_type, temperature_zone) VALUES (?, ?, ?, ?)",
        WAREHOUSES,
    )
    conn.executemany(
        "INSERT INTO store(store_id, store_name, city) VALUES (?, ?, ?)",
        STORES,
    )
    conn.executemany(
        """INSERT INTO work_center(work_center_id, work_center_name, daily_capacity_min, cost_rate_per_min)
           VALUES (?, ?, ?, ?)""",
        WORK_CENTERS,
    )
    conn.executemany(
        """INSERT INTO material
           (material_id, material_name, material_type, base_unit, procurement_type,
            standard_cost, list_price, safety_stock, min_order_qty, lot_size,
            lead_time_days, shelf_life_days, default_warehouse_id)
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
        """INSERT INTO routing(material_id, op_no, operation_name, work_center_id,
                               setup_min, run_min_per_unit)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ROUTINGS,
    )
    conn.executemany(
        """INSERT INTO inventory_batch
           (batch_id, material_id, warehouse_id, batch_no, qty_on_hand, qty_reserved,
            production_date, expiry_date, unit_cost, status)
           VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, 'UNRESTRICTED')""",
        BATCHES,
    )
    for req in REQUISITIONS:
        conn.execute(
            """INSERT INTO store_requisition
               (requisition_id, store_id, request_date, required_date, status, remark, created_at)
               VALUES (?, ?, ?, ?, 'SUBMITTED', ?, ?)""",
            (req["id"], req["store"], req["request"], req["required"], req["remark"], req["request"] + " 09:00:00"),
        )
        conn.executemany(
            """INSERT INTO store_requisition_item(requisition_id, line_no, product_id, qty)
               VALUES (?, ?, ?, ?)""",
            [(req["id"], 10 + 10 * i, product, qty) for i, (product, qty) in enumerate(req["items"])],
        )

    costs = costing.rollup_all(conn, RUN_DATE)
    conn.commit()
    return {"materials": len(MATERIALS), "boms": len(BOMS), "batches": len(BATCHES), "costs": costs}


def init_and_seed(db_path: str | Path = DEFAULT_DB_PATH, reset: bool = True) -> sqlite3.Connection:
    if reset:
        reset_db(db_path)
    conn = connect(db_path)
    init_db(conn)
    seed_all(conn)
    return conn


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化云谷鲜米粉 PP 模块演示数据库")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--reset", action="store_true", help="删除已有数据库后重建")
    args = parser.parse_args()

    conn = init_and_seed(args.db, reset=args.reset)
    print(f"数据库已初始化: {Path(args.db).resolve()}")
    for material in ("SF-001", "SF-002", "SF-003", "FG-001", "FG-002"):
        row = conn.execute(
            "SELECT material_name, standard_cost FROM material WHERE material_id = ?", (material,)
        ).fetchone()
        print(f"  {material} {row['material_name']}: 标准成本 {round_money(row['standard_cost'])} 元")
    conn.close()


if __name__ == "__main__":
    main()
