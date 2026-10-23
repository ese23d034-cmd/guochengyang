"""全模块演示数据：42 家门店主数据、企业客户、供应商、门店库存、员工排班与权限。"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from pp import repo

PERIOD_START = "2026-10-23"
PERIOD_END = "2026-10-26"

SUPPLIERS = [
    ("SUP-GRAIN", "五常米业供应链", "GRAIN", 1, 4.8, "王经理 138-0000-1001"),
    ("SUP-MEAT", "鲜联肉食品", "MEAT", 1, 4.7, "李经理 138-0000-1002"),
    ("SUP-VEG", "绿源生鲜配送", "VEG", 1, 4.6, "周经理 138-0000-1003"),
    ("SUP-PACK", "恒信包材", "PACKAGING", 2, 4.5, "陈经理 138-0000-1004"),
]

CUSTOMERS = [
    ("CUS-001", "云谷科技园管委会", "CORPORATE", "后勤部 0571-8888-0001"),
]

GL_ACCOUNTS = [
    ("1002", "银行存款", "ASSET"),
    ("1122", "应收账款", "ASSET"),
    ("1403", "原材料", "ASSET"),
    ("1405", "库存商品", "ASSET"),
    ("1406", "门店库存商品", "ASSET"),
    ("2202", "应付账款", "LIABILITY"),
    ("2211", "应付职工薪酬", "LIABILITY"),
    ("2241", "其他应付款", "LIABILITY"),
    ("3001", "期初余额", "EQUITY"),
    ("5001", "生产成本", "EXPENSE"),
    ("6001", "主营业务收入", "REVENUE"),
    ("6401", "主营业务成本", "EXPENSE"),
    ("6601", "销售费用", "EXPENSE"),
]

EMPLOYEES = [
    ("E001", "王敏", "STORE", "ST-B", "店长", 32),
    ("E002", "李娜", "STORE", "ST-B", "收银员", 22),
    ("E003", "张伟", "STORE", "ST-B", "后厨", 24),
    ("E004", "陈强", "STORE", "ST-B", "后厨", 24),
    ("E005", "赵磊", "STORE", "ST-C", "店长", 32),
    ("E006", "周婷", "STORE", "ST-C", "收银员", 22),
    ("E007", "孙浩", "STORE", "ST-C", "后厨", 24),
    ("E008", "郭晨阳", "FACTORY", None, "生产计划", 40),
    ("E009", "刘强", "FACTORY", None, "米粉线班长", 28),
    ("E010", "王芳", "FACTORY", None, "臊子线班长", 28),
    ("E011", "陈静", "FACTORY", None, "质检员", 26),
    ("E012", "杨帆", "FACTORY", None, "仓管员", 24),
    ("E013", "兰永尚", "HQ", None, "采购经理", 45),
    ("E014", "吴敏", "HQ", None, "总账会计", 38),
    ("E015", "郑洁", "HQ", None, "管理会计", 38),
    ("E016", "何静", "HQ", None, "人事经理", 40),
    ("E017", "刘浩焰", "HQ", None, "销售总监", 50),
]

USERS = [
    ("liu.haoyan", "E017", "刘浩焰", "SD_MANAGER"),
    ("guo.chenyang", "E008", "郭晨阳", "PP_PLANNER"),
    ("lan.yongshang", "E013", "兰永尚", "MM_BUYER"),
    ("wu.min", "E014", "吴敏", "FI_ACCOUNTANT"),
    ("zheng.jie", "E015", "郑洁", "CO_ANALYST"),
    ("he.jing", "E016", "何静", "HCM_ADMIN"),
    ("admin", None, "系统管理员", "ADMIN"),
]

MODULES = ["SD", "PP", "MM", "FI", "CO", "HCM"]
PERMISSIONS = {
    "SD_MANAGER": {"SD": (1, 1), "PP": (1, 0), "MM": (1, 0), "FI": (0, 0), "CO": (1, 0), "HCM": (0, 0)},
    "PP_PLANNER": {"SD": (1, 0), "PP": (1, 1), "MM": (1, 0), "FI": (0, 0), "CO": (0, 0), "HCM": (0, 0)},
    "MM_BUYER": {"SD": (0, 0), "PP": (1, 0), "MM": (1, 1), "FI": (1, 0), "CO": (0, 0), "HCM": (0, 0)},
    "FI_ACCOUNTANT": {"SD": (1, 0), "PP": (1, 0), "MM": (1, 0), "FI": (1, 1), "CO": (1, 0), "HCM": (0, 0)},
    "CO_ANALYST": {"SD": (1, 0), "PP": (0, 0), "MM": (0, 0), "FI": (1, 0), "CO": (1, 1), "HCM": (0, 0)},
    "HCM_ADMIN": {"SD": (1, 0), "PP": (1, 0), "MM": (1, 0), "FI": (0, 0), "CO": (0, 0), "HCM": (1, 1)},
    "ADMIN": {module: (1, 1) for module in MODULES},
}

SHIFT_REQUIREMENTS = [
    ("ST-B", "MORNING", 2),
    ("ST-B", "MIDDLE", 2),
    ("ST-C", "MORNING", 1),
    ("ST-C", "MIDDLE", 2),
    ("FACTORY", "MORNING", 3),
    ("FACTORY", "MIDDLE", 2),
]


def _add_stores(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM store WHERE store_id NOT IN ('ST-A', 'ST-B', 'ST-C')")
    for number in range(4, 43):
        conn.execute(
            """INSERT INTO store(store_id, store_name, city, status, store_type)
               VALUES (?, ?, ?, 'ACTIVE', 'STORE')""",
            (f"ST-{number:03d}", f"云谷鲜米粉{number:02d}店", "杭州"),
        )
    conn.execute(
        """INSERT INTO store(store_id, store_name, city, status, store_type)
           VALUES ('ST-CORP', '企业团餐事业部', '杭州', 'ACTIVE', 'CORP')"""
    )
    conn.execute("UPDATE store SET store_type = 'STORE' WHERE store_type IS NULL OR store_type = ''")


def _add_store_inventory(conn: sqlite3.Connection) -> None:
    fg001 = repo.get_material(conn, "FG-001")
    fg002 = repo.get_material(conn, "FG-002")
    rows = [
        ("ST-B", "FG-001", 240, fg001.standard_cost, 220, 400),
        ("ST-C", "FG-001", 120, fg001.standard_cost, 100, 160),
        ("ST-C", "FG-002", 80, fg002.standard_cost, 60, 160),
    ]
    conn.executemany(
        """INSERT INTO store_inventory
           (store_id, product_id, qty_on_hand, avg_cost, reorder_point, target_stock)
           VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )


def _add_shifts(conn: sqlite3.Connection) -> None:
    store_shifts = {
        "ST-B": [
            ("E001", "MORNING", "08:00", "16:00"),
            ("E002", "MORNING", "08:00", "16:00"),
            ("E003", "MIDDLE", "12:00", "20:00"),
            ("E004", "MIDDLE", "12:00", "20:00"),
        ],
        "ST-C": [
            ("E005", "MORNING", "08:00", "16:00"),
            ("E006", "MIDDLE", "12:00", "20:00"),
            ("E007", "MIDDLE", "12:00", "20:00"),
        ],
    }
    factory = [
        ("E008", "MORNING", "08:00", "16:00"),
        ("E009", "MORNING", "06:00", "14:00"),
        ("E010", "MIDDLE", "14:00", "22:00"),
        ("E011", "MORNING", "08:00", "16:00"),
        ("E012", "MIDDLE", "14:00", "22:00"),
    ]
    start = date.fromisoformat(PERIOD_START)
    for offset in range((date.fromisoformat(PERIOD_END) - start).days + 1):
        work_date = (start + timedelta(days=offset)).isoformat()
        for store_id, staff in store_shifts.items():
            for employee_id, shift_type, begin, end in staff:
                conn.execute(
                    """INSERT INTO shift(shift_id, employee_id, work_date, shift_type,
                                         start_time, end_time, location)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (f"SH-{work_date.replace('-', '')}-{employee_id}-{shift_type[0]}",
                     employee_id, work_date, shift_type, begin, end, store_id),
                )
        for employee_id, shift_type, begin, end in factory:
            conn.execute(
                """INSERT INTO shift(shift_id, employee_id, work_date, shift_type,
                                     start_time, end_time, location)
                   VALUES (?, ?, ?, ?, ?, ?, 'FACTORY')""",
                (f"SH-{work_date.replace('-', '')}-{employee_id}-{shift_type[0]}",
                 employee_id, work_date, shift_type, begin, end),
            )


def seed_extra(conn: sqlite3.Connection) -> None:
    """在 PP 基础数据之后追加全模块主数据；会清空扩展业务表。"""
    for table in (
        "sales_fact", "outbound_delivery_item", "outbound_delivery",
        "pos_sale_item", "pos_sale", "sales_order_item", "sales_order",
        "mm_receipt_item", "mm_receipt", "purchase_order",
        "shift", "shift_requirement", "app_user", "role_permission",
        "employee", "store_fixed_cost", "store_inventory",
        "supplier", "customer", "gl_account",
    ):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM store_requisition_item")
    conn.execute("DELETE FROM store_requisition")

    _add_stores(conn)
    conn.executemany(
        """INSERT INTO supplier(supplier_id, supplier_name, category, lead_time_days, rating, contact)
           VALUES (?, ?, ?, ?, ?, ?)""",
        SUPPLIERS,
    )
    conn.executemany(
        "INSERT INTO customer(customer_id, customer_name, customer_type, contact) VALUES (?, ?, ?, ?)",
        CUSTOMERS,
    )
    conn.executemany(
        "INSERT INTO gl_account(account_code, account_name, account_type) VALUES (?, ?, ?)",
        GL_ACCOUNTS,
    )
    _add_store_inventory(conn)
    conn.executemany(
        """INSERT INTO employee(employee_id, employee_name, department, store_id, position, hourly_rate)
           VALUES (?, ?, ?, ?, ?, ?)""",
        EMPLOYEES,
    )
    conn.executemany(
        "INSERT INTO app_user(user_id, employee_id, display_name, role_code) VALUES (?, ?, ?, ?)",
        USERS,
    )
    for role, matrix in PERMISSIONS.items():
        for module in MODULES:
            view, edit = matrix.get(module, (0, 0))
            conn.execute(
                """INSERT INTO role_permission(role_code, module_code, can_view, can_edit)
                   VALUES (?, ?, ?, ?)""",
                (role, module, view, edit),
            )
    conn.executemany(
        "INSERT INTO shift_requirement(location, shift_type, min_staff) VALUES (?, ?, ?)",
        SHIFT_REQUIREMENTS,
    )
    _add_shifts(conn)
    conn.executemany(
        """INSERT INTO store_fixed_cost
           (store_id, period_start, period_end, rent, labor, utilities, other)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            ("ST-B", PERIOD_START, PERIOD_END, 800, 500, 120, 80),
            ("ST-C", PERIOD_START, PERIOD_END, 600, 400, 120, 80),
        ],
    )
    conn.commit()
