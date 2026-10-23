-- ============================================================================
-- 云谷鲜米粉 ERP —— 全模块扩展表（SD / MM / FI / CO / HCM）
-- 在 pp/schema.sql 之后执行；PP 相关表仍由 pp/schema.sql 维护。
-- ============================================================================

-- 门店类型：STORE = 直营门店，CORP = 企业团餐利润中心
-- 由 erp/db.py 以 ALTER TABLE 方式补充，兼容已存在的旧库。

-- ----------------------------------------------------------------------------
-- 主数据：供应商 / 客户 / 总账科目
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS supplier (
    supplier_id     TEXT PRIMARY KEY,
    supplier_name   TEXT NOT NULL,
    category        TEXT NOT NULL,       -- GRAIN / MEAT / VEG / PACKAGING
    lead_time_days  INTEGER NOT NULL DEFAULT 1,
    rating          REAL NOT NULL DEFAULT 5,
    contact         TEXT,
    status          TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS customer (
    customer_id    TEXT PRIMARY KEY,
    customer_name  TEXT NOT NULL,
    customer_type  TEXT NOT NULL DEFAULT 'CORPORATE',
    contact        TEXT,
    status         TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS gl_account (
    account_code  TEXT PRIMARY KEY,
    account_name  TEXT NOT NULL,
    account_type  TEXT NOT NULL CHECK (account_type IN ('ASSET', 'LIABILITY', 'EQUITY', 'REVENUE', 'EXPENSE')),
    parent_code   TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1
);

-- ----------------------------------------------------------------------------
-- SD：企业团餐销售订单、门店库存、POS 销售、配送
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales_order (
    order_id       TEXT PRIMARY KEY,
    customer_id    TEXT NOT NULL REFERENCES customer(customer_id),
    order_date     TEXT NOT NULL,
    required_date  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'OPEN'
                   CHECK (status IN ('OPEN', 'PLANNED', 'DELIVERED', 'CLOSED', 'CANCELLED')),
    total_amount   REAL NOT NULL DEFAULT 0,
    remark         TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales_order_item (
    order_id    TEXT NOT NULL REFERENCES sales_order(order_id) ON DELETE CASCADE,
    line_no     INTEGER NOT NULL,
    product_id  TEXT NOT NULL REFERENCES material(material_id),
    qty         REAL NOT NULL CHECK (qty > 0),
    unit_price  REAL NOT NULL CHECK (unit_price >= 0),
    PRIMARY KEY (order_id, line_no)
);

-- 门店成品库存（按移动加权平均计价）
CREATE TABLE IF NOT EXISTS store_inventory (
    store_id       TEXT NOT NULL REFERENCES store(store_id),
    product_id     TEXT NOT NULL REFERENCES material(material_id),
    qty_on_hand    REAL NOT NULL DEFAULT 0 CHECK (qty_on_hand >= 0),
    avg_cost       REAL NOT NULL DEFAULT 0,
    reorder_point  REAL NOT NULL DEFAULT 0,
    target_stock   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (store_id, product_id)
);

CREATE TABLE IF NOT EXISTS pos_sale (
    sale_id        TEXT PRIMARY KEY,
    store_id       TEXT NOT NULL REFERENCES store(store_id),
    business_date  TEXT NOT NULL,
    channel        TEXT NOT NULL DEFAULT 'POS' CHECK (channel IN ('POS', 'GROUP')),
    total_amount   REAL NOT NULL DEFAULT 0,
    total_cost     REAL NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'POSTED',
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pos_sale_item (
    sale_id     TEXT NOT NULL REFERENCES pos_sale(sale_id) ON DELETE CASCADE,
    line_no     INTEGER NOT NULL,
    product_id  TEXT NOT NULL REFERENCES material(material_id),
    qty         REAL NOT NULL CHECK (qty > 0),
    unit_price  REAL NOT NULL DEFAULT 0,
    unit_cost   REAL NOT NULL DEFAULT 0,
    amount      REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (sale_id, line_no)
);

CREATE TABLE IF NOT EXISTS outbound_delivery (
    delivery_id   TEXT PRIMARY KEY,
    delivery_type TEXT NOT NULL CHECK (delivery_type IN ('STORE', 'CORPORATE')),
    store_id      TEXT REFERENCES store(store_id),
    customer_id   TEXT REFERENCES customer(customer_id),
    delivery_date TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'POSTED',
    total_qty     REAL NOT NULL DEFAULT 0,
    total_cost    REAL NOT NULL DEFAULT 0,
    reference_id  TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbound_delivery_item (
    delivery_id TEXT NOT NULL REFERENCES outbound_delivery(delivery_id) ON DELETE CASCADE,
    line_no     INTEGER NOT NULL,
    product_id  TEXT NOT NULL REFERENCES material(material_id),
    qty         REAL NOT NULL CHECK (qty > 0),
    batch_id    TEXT,
    unit_cost   REAL NOT NULL DEFAULT 0,
    amount      REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (delivery_id, line_no)
);

-- CO 统一销售事实表：POS 与团餐共用
CREATE TABLE IF NOT EXISTS sales_fact (
    sale_line_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id      TEXT NOT NULL,
    sale_date    TEXT NOT NULL,
    channel      TEXT NOT NULL,          -- POS / GROUP
    store_id     TEXT,
    customer_id  TEXT,
    product_id   TEXT NOT NULL REFERENCES material(material_id),
    qty          REAL NOT NULL,
    unit_price   REAL NOT NULL,
    revenue      REAL NOT NULL,
    unit_cost    REAL NOT NULL,
    cogs         REAL NOT NULL
);

-- ----------------------------------------------------------------------------
-- MM：采购订单 / 收货
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS purchase_order (
    po_id         TEXT PRIMARY KEY,
    pr_id         TEXT REFERENCES purchase_requisition(pr_id),
    supplier_id   TEXT NOT NULL REFERENCES supplier(supplier_id),
    material_id   TEXT NOT NULL REFERENCES material(material_id),
    qty           REAL NOT NULL CHECK (qty > 0),
    unit          TEXT NOT NULL,
    unit_cost     REAL NOT NULL DEFAULT 0,
    order_date    TEXT NOT NULL,
    expected_date TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'OPEN'
                  CHECK (status IN ('OPEN', 'RECEIVED', 'CANCELLED')),
    warehouse_id  TEXT REFERENCES warehouse(warehouse_id),
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mm_receipt (
    receipt_id    TEXT PRIMARY KEY,
    po_id         TEXT NOT NULL REFERENCES purchase_order(po_id),
    receipt_date  TEXT NOT NULL,
    warehouse_id  TEXT NOT NULL REFERENCES warehouse(warehouse_id),
    total_amount  REAL NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'POSTED',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mm_receipt_item (
    receipt_id  TEXT NOT NULL REFERENCES mm_receipt(receipt_id) ON DELETE CASCADE,
    line_no     INTEGER NOT NULL,
    material_id TEXT NOT NULL REFERENCES material(material_id),
    qty         REAL NOT NULL,
    batch_id    TEXT,
    unit_cost   REAL NOT NULL DEFAULT 0,
    amount      REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (receipt_id, line_no)
);

-- ----------------------------------------------------------------------------
-- CO：门店固定费用
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS store_fixed_cost (
    store_id     TEXT NOT NULL REFERENCES store(store_id),
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    rent         REAL NOT NULL DEFAULT 0,
    labor        REAL NOT NULL DEFAULT 0,
    utilities    REAL NOT NULL DEFAULT 0,
    other        REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (store_id, period_start, period_end)
);

-- ----------------------------------------------------------------------------
-- HCM：员工 / 排班 / 权限
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS employee (
    employee_id   TEXT PRIMARY KEY,
    employee_name TEXT NOT NULL,
    department    TEXT NOT NULL,          -- STORE / FACTORY / FINANCE / HQ
    store_id      TEXT REFERENCES store(store_id),
    position      TEXT,
    hourly_rate   REAL NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS shift (
    shift_id     TEXT PRIMARY KEY,
    employee_id  TEXT NOT NULL REFERENCES employee(employee_id),
    work_date    TEXT NOT NULL,
    shift_type   TEXT NOT NULL CHECK (shift_type IN ('MORNING', 'MIDDLE', 'NIGHT')),
    start_time   TEXT NOT NULL,
    end_time     TEXT NOT NULL,
    location     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'PLANNED'
);

CREATE TABLE IF NOT EXISTS shift_requirement (
    location    TEXT NOT NULL,
    shift_type  TEXT NOT NULL,
    min_staff   INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (location, shift_type)
);

CREATE TABLE IF NOT EXISTS app_user (
    user_id      TEXT PRIMARY KEY,
    employee_id  TEXT REFERENCES employee(employee_id),
    display_name TEXT NOT NULL,
    role_code    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS role_permission (
    role_code  TEXT NOT NULL,
    module_code TEXT NOT NULL CHECK (module_code IN ('SD', 'PP', 'MM', 'FI', 'CO', 'HCM')),
    can_view   INTEGER NOT NULL DEFAULT 0,
    can_edit   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (role_code, module_code)
);

CREATE INDEX IF NOT EXISTS idx_store_inventory_store ON store_inventory(store_id, product_id);
CREATE INDEX IF NOT EXISTS idx_sales_fact_date ON sales_fact(sale_date, store_id);
CREATE INDEX IF NOT EXISTS idx_po_status ON purchase_order(status, expected_date);
CREATE INDEX IF NOT EXISTS idx_shift_date ON shift(work_date, location);
CREATE INDEX IF NOT EXISTS idx_delivery_date ON outbound_delivery(delivery_date, delivery_type);
