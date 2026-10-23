-- ============================================================================
-- 云谷鲜米粉 ERP —— MM 采购与库存模块（负责人：兰永尚）
-- 数据库：SQLite 3
-- 覆盖：物料主数据 / BOM / 供应商 / 采购申请 / 采购订单 / 收货 / 批次库存 / 效期预警
-- ============================================================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS warehouse (
    warehouse_id     TEXT PRIMARY KEY,
    warehouse_name   TEXT NOT NULL,
    warehouse_type   TEXT NOT NULL CHECK (warehouse_type IN ('RAW', 'COLD', 'FINISHED')),
    temperature_zone TEXT
);

CREATE TABLE IF NOT EXISTS supplier (
    supplier_id    TEXT PRIMARY KEY,
    supplier_name  TEXT NOT NULL,
    category       TEXT NOT NULL CHECK (category IN ('GRAIN', 'MEAT', 'VEG', 'PACKAGING')),
    lead_time_days INTEGER NOT NULL DEFAULT 1,
    rating         REAL NOT NULL DEFAULT 5,
    contact        TEXT,
    status         TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS material (
    material_id          TEXT PRIMARY KEY,
    material_name        TEXT NOT NULL,
    material_type        TEXT NOT NULL CHECK (material_type IN ('FG', 'SF', 'RM', 'PK')),
    base_unit            TEXT NOT NULL,
    procurement_type     TEXT NOT NULL CHECK (procurement_type IN ('IN_HOUSE', 'EXTERNAL')),
    standard_cost        REAL NOT NULL DEFAULT 0 CHECK (standard_cost >= 0),
    safety_stock         REAL NOT NULL DEFAULT 0 CHECK (safety_stock >= 0),
    min_order_qty        REAL NOT NULL DEFAULT 0 CHECK (min_order_qty >= 0),
    lot_size             REAL NOT NULL DEFAULT 0 CHECK (lot_size >= 0),
    lead_time_days       INTEGER NOT NULL DEFAULT 0 CHECK (lead_time_days >= 0),
    shelf_life_days      INTEGER,
    default_warehouse_id TEXT REFERENCES warehouse(warehouse_id),
    default_supplier_id  TEXT REFERENCES supplier(supplier_id),
    is_active            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS bom (
    bom_id      TEXT PRIMARY KEY,
    product_id  TEXT NOT NULL REFERENCES material(material_id),
    version     TEXT NOT NULL DEFAULT 'V1',
    base_qty    REAL NOT NULL DEFAULT 1 CHECK (base_qty > 0),
    status      TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE')),
    valid_from  TEXT,
    valid_to    TEXT,
    UNIQUE (product_id, version)
);

CREATE TABLE IF NOT EXISTS bom_item (
    bom_id       TEXT NOT NULL REFERENCES bom(bom_id) ON DELETE CASCADE,
    line_no      INTEGER NOT NULL,
    component_id TEXT NOT NULL REFERENCES material(material_id),
    qty_per_base REAL NOT NULL CHECK (qty_per_base > 0),
    scrap_rate   REAL NOT NULL DEFAULT 0 CHECK (scrap_rate >= 0 AND scrap_rate < 1),
    PRIMARY KEY (bom_id, line_no)
);

CREATE TABLE IF NOT EXISTS inventory_batch (
    batch_id        TEXT PRIMARY KEY,
    material_id     TEXT NOT NULL REFERENCES material(material_id),
    warehouse_id    TEXT NOT NULL REFERENCES warehouse(warehouse_id),
    batch_no        TEXT NOT NULL,
    qty_on_hand     REAL NOT NULL DEFAULT 0 CHECK (qty_on_hand >= 0),
    production_date TEXT,
    expiry_date     TEXT,
    unit_cost       REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'UNRESTRICTED'
                    CHECK (status IN ('UNRESTRICTED', 'BLOCKED', 'EXPIRED')),
    UNIQUE (material_id, batch_no)
);

CREATE TABLE IF NOT EXISTS inventory_movement (
    movement_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    movement_type TEXT NOT NULL,          -- PURCHASE_RECEIPT / GOODS_ISSUE
    material_id   TEXT NOT NULL REFERENCES material(material_id),
    batch_id      TEXT REFERENCES inventory_batch(batch_id),
    qty           REAL NOT NULL,          -- 入库正、出库负
    unit_cost     REAL NOT NULL DEFAULT 0,
    amount        REAL NOT NULL DEFAULT 0,
    reference_no  TEXT,
    posting_date  TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

-- PP -> MM 采购申请接口
CREATE TABLE IF NOT EXISTS purchase_requisition (
    pr_id          TEXT PRIMARY KEY,
    mrp_run_id     TEXT,
    material_id    TEXT NOT NULL REFERENCES material(material_id),
    qty            REAL NOT NULL CHECK (qty > 0),
    unit           TEXT NOT NULL,
    required_date  TEXT NOT NULL,
    release_date   TEXT,
    status         TEXT NOT NULL DEFAULT 'OPEN'
                   CHECK (status IN ('OPEN', 'APPROVED', 'RECEIVED', 'CLOSED')),
    unit_cost      REAL NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);

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
    receipt_id   TEXT PRIMARY KEY,
    po_id        TEXT NOT NULL REFERENCES purchase_order(po_id),
    receipt_date TEXT NOT NULL,
    warehouse_id TEXT NOT NULL REFERENCES warehouse(warehouse_id),
    total_amount REAL NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'POSTED',
    created_at   TEXT NOT NULL
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

-- MM -> PP 生产发料
CREATE TABLE IF NOT EXISTS mm_goods_issue (
    issue_id     TEXT PRIMARY KEY,
    order_id     TEXT NOT NULL,
    issue_date   TEXT NOT NULL,
    warehouse_id TEXT,
    total_amount REAL NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'POSTED',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mm_goods_issue_item (
    issue_id    TEXT NOT NULL REFERENCES mm_goods_issue(issue_id) ON DELETE CASCADE,
    line_no     INTEGER NOT NULL,
    material_id TEXT NOT NULL REFERENCES material(material_id),
    batch_id    TEXT,
    qty         REAL NOT NULL,
    unit_cost   REAL NOT NULL DEFAULT 0,
    amount      REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (issue_id, line_no)
);

CREATE TABLE IF NOT EXISTS integration_event (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type   TEXT NOT NULL,
    aggregate_id TEXT,
    payload      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'PENDING',
    created_at   TEXT NOT NULL,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS number_range (
    scope   TEXT PRIMARY KEY,
    next_no INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_material_type ON material(material_type);
CREATE INDEX IF NOT EXISTS idx_bom_product ON bom(product_id, status);
CREATE INDEX IF NOT EXISTS idx_batch_material ON inventory_batch(material_id, expiry_date);
CREATE INDEX IF NOT EXISTS idx_pr_status ON purchase_requisition(status, required_date);
CREATE INDEX IF NOT EXISTS idx_po_status ON purchase_order(status, expected_date);
CREATE INDEX IF NOT EXISTS idx_issue_order ON mm_goods_issue(order_id);
