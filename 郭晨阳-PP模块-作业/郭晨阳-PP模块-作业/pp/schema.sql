-- ============================================================================
-- 云谷鲜米粉 ERP —— PP 生产制造模块（负责人：郭晨阳）
-- 数据库：SQLite 3
-- 说明：
--   1) 本脚本只覆盖 PP 模块自有表，以及与 SD/MM/FI 交互所必需的最小接口表；
--   2) 金额单位：人民币元；数量单位取物料主数据 base_unit；
--   3) 日期统一存 ISO 字符串（YYYY-MM-DD），时间戳存 ISO 8601。
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------------------
-- 0. 组织 / 基础主数据
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS store (
    store_id    TEXT PRIMARY KEY,
    store_name  TEXT NOT NULL,
    city        TEXT,
    status      TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS warehouse (
    warehouse_id    TEXT PRIMARY KEY,
    warehouse_name  TEXT NOT NULL,
    warehouse_type  TEXT NOT NULL CHECK (warehouse_type IN ('RAW', 'COLD', 'FINISHED')),
    temperature_zone TEXT
);

-- 物料主数据：原料 RM / 包装 PK / 半成品 SF / 成品 FG
CREATE TABLE IF NOT EXISTS material (
    material_id          TEXT PRIMARY KEY,
    material_name        TEXT NOT NULL,
    material_type        TEXT NOT NULL CHECK (material_type IN ('FG', 'SF', 'RM', 'PK')),
    base_unit            TEXT NOT NULL,
    procurement_type     TEXT NOT NULL CHECK (procurement_type IN ('IN_HOUSE', 'EXTERNAL')),
    standard_cost        REAL NOT NULL DEFAULT 0 CHECK (standard_cost >= 0),
    list_price           REAL,
    safety_stock         REAL NOT NULL DEFAULT 0 CHECK (safety_stock >= 0),
    min_order_qty        REAL NOT NULL DEFAULT 0 CHECK (min_order_qty >= 0),
    lot_size             REAL NOT NULL DEFAULT 0 CHECK (lot_size >= 0),
    lead_time_days       INTEGER NOT NULL DEFAULT 0 CHECK (lead_time_days >= 0),
    shelf_life_days      INTEGER,
    default_warehouse_id TEXT REFERENCES warehouse(warehouse_id),
    is_active            INTEGER NOT NULL DEFAULT 1
);

-- 工艺路线 / 工作中心（用于生产订单排产）
CREATE TABLE IF NOT EXISTS work_center (
    work_center_id     TEXT PRIMARY KEY,
    work_center_name   TEXT NOT NULL,
    daily_capacity_min REAL NOT NULL CHECK (daily_capacity_min > 0),
    cost_rate_per_min  REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS routing (
    material_id        TEXT NOT NULL REFERENCES material(material_id),
    op_no              INTEGER NOT NULL,
    operation_name     TEXT NOT NULL,
    work_center_id     TEXT NOT NULL REFERENCES work_center(work_center_id),
    setup_min          REAL NOT NULL DEFAULT 0,
    run_min_per_unit   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (material_id, op_no)
);

-- ----------------------------------------------------------------------------
-- 1. BOM（物料清单）：支持多级 BOM、版本、有效期、组件损耗率
-- ----------------------------------------------------------------------------
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
    bom_id          TEXT NOT NULL REFERENCES bom(bom_id) ON DELETE CASCADE,
    line_no         INTEGER NOT NULL,
    component_id    TEXT NOT NULL REFERENCES material(material_id),
    qty_per_base    REAL NOT NULL CHECK (qty_per_base > 0),
    scrap_rate      REAL NOT NULL DEFAULT 0 CHECK (scrap_rate >= 0 AND scrap_rate < 1),
    PRIMARY KEY (bom_id, line_no)
);

-- ----------------------------------------------------------------------------
-- 2. 库存：批次 + 保质期 + 预留（鲜食供应链必须按批次/效期管理）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS inventory_batch (
    batch_id        TEXT PRIMARY KEY,
    material_id     TEXT NOT NULL REFERENCES material(material_id),
    warehouse_id    TEXT NOT NULL REFERENCES warehouse(warehouse_id),
    batch_no        TEXT NOT NULL,
    qty_on_hand     REAL NOT NULL DEFAULT 0 CHECK (qty_on_hand >= 0),
    qty_reserved    REAL NOT NULL DEFAULT 0 CHECK (qty_reserved >= 0),
    production_date TEXT,
    expiry_date     TEXT,
    unit_cost       REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'UNRESTRICTED'
                    CHECK (status IN ('UNRESTRICTED', 'BLOCKED', 'EXPIRED')),
    UNIQUE (material_id, batch_no)
);

CREATE TABLE IF NOT EXISTS inventory_movement (
    movement_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    movement_type   TEXT NOT NULL,          -- PURCHASE_RECEIPT / GOODS_ISSUE / PRODUCTION_OUTPUT ...
    material_id     TEXT NOT NULL REFERENCES material(material_id),
    batch_id        TEXT REFERENCES inventory_batch(batch_id),
    qty             REAL NOT NULL,          -- 入库为正、出库为负
    unit_cost       REAL NOT NULL DEFAULT 0,
    amount          REAL NOT NULL DEFAULT 0,
    order_id        TEXT,
    reference_no    TEXT,
    posting_date    TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

-- 生产订单组件预留记录
CREATE TABLE IF NOT EXISTS inventory_reservation (
    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id       TEXT NOT NULL,
    material_id    TEXT NOT NULL REFERENCES material(material_id),
    batch_id       TEXT NOT NULL REFERENCES inventory_batch(batch_id),
    qty            REAL NOT NULL CHECK (qty > 0),
    qty_consumed   REAL NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED', 'CANCELLED')),
    created_at     TEXT NOT NULL
);

-- ----------------------------------------------------------------------------
-- 3. SD -> PP 接口：门店要货单
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS store_requisition (
    requisition_id TEXT PRIMARY KEY,
    store_id       TEXT NOT NULL REFERENCES store(store_id),
    request_date   TEXT NOT NULL,
    required_date  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'SUBMITTED'
                   CHECK (status IN ('SUBMITTED', 'PLANNED', 'CLOSED', 'CANCELLED')),
    mrp_run_id     TEXT,
    remark         TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS store_requisition_item (
    requisition_id TEXT NOT NULL REFERENCES store_requisition(requisition_id) ON DELETE CASCADE,
    line_no        INTEGER NOT NULL,
    product_id     TEXT NOT NULL REFERENCES material(material_id),
    qty            REAL NOT NULL CHECK (qty > 0),
    PRIMARY KEY (requisition_id, line_no)
);

-- ----------------------------------------------------------------------------
-- 4. MRP 运行结果（可追溯）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mrp_run (
    run_id                    TEXT PRIMARY KEY,
    run_date                  TEXT NOT NULL,
    run_at                    TEXT NOT NULL,
    run_by                    TEXT,
    planning_horizon_days     INTEGER NOT NULL DEFAULT 30,
    status                    TEXT NOT NULL DEFAULT 'COMPLETED',
    requisition_count         INTEGER NOT NULL DEFAULT 0,
    production_order_count    INTEGER NOT NULL DEFAULT 0,
    purchase_requisition_count INTEGER NOT NULL DEFAULT 0,
    exception_count           INTEGER NOT NULL DEFAULT 0
);

-- 展开/净算明细（每个物料每个需求日期一行）
CREATE TABLE IF NOT EXISTS mrp_explosion (
    explosion_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                TEXT NOT NULL REFERENCES mrp_run(run_id) ON DELETE CASCADE,
    level                 INTEGER NOT NULL,
    parent_material_id    TEXT,
    material_id           TEXT NOT NULL REFERENCES material(material_id),
    gross_qty             REAL NOT NULL,
    stock_available       REAL NOT NULL,
    net_qty               REAL NOT NULL,
    order_qty             REAL NOT NULL,
    order_type            TEXT NOT NULL CHECK (order_type IN ('PRODUCTION', 'PURCHASE', 'NONE')),
    required_date         TEXT NOT NULL,
    release_date          TEXT,
    source_requisition_id TEXT,
    remark                TEXT
);

CREATE TABLE IF NOT EXISTS mrp_exception (
    exception_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL REFERENCES mrp_run(run_id) ON DELETE CASCADE,
    level          INTEGER,
    material_id    TEXT,
    exception_type TEXT NOT NULL,
    severity       TEXT NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'ERROR')),
    message        TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

-- ----------------------------------------------------------------------------
-- 5. PP 输出：生产订单 + 工序 + 组件 + 报工
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS production_order (
    order_id              TEXT PRIMARY KEY,
    material_id           TEXT NOT NULL REFERENCES material(material_id),
    qty_planned           REAL NOT NULL CHECK (qty_planned > 0),
    qty_good              REAL NOT NULL DEFAULT 0,
    qty_scrap             REAL NOT NULL DEFAULT 0,
    qty_received          REAL NOT NULL DEFAULT 0,
    status                TEXT NOT NULL DEFAULT 'PLANNED'
                          CHECK (status IN ('PLANNED', 'RELEASED', 'IN_PROCESS', 'CONFIRMED', 'COMPLETED', 'CANCELLED')),
    availability_status   TEXT NOT NULL DEFAULT 'UNCHECKED',
    mrp_run_id            TEXT REFERENCES mrp_run(run_id),
    source_requisition_id TEXT,
    required_date         TEXT NOT NULL,
    start_date            TEXT NOT NULL,
    planned_start         TEXT,
    planned_finish        TEXT,
    actual_start          TEXT,
    actual_finish         TEXT,
    estimated_material_cost REAL NOT NULL DEFAULT 0,
    estimated_labor_cost    REAL NOT NULL DEFAULT 0,
    actual_material_cost    REAL NOT NULL DEFAULT 0,
    actual_labor_cost       REAL NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS production_order_component (
    order_id      TEXT NOT NULL REFERENCES production_order(order_id) ON DELETE CASCADE,
    line_no       INTEGER NOT NULL,
    material_id   TEXT NOT NULL REFERENCES material(material_id),
    qty_required  REAL NOT NULL CHECK (qty_required > 0),
    qty_issued    REAL NOT NULL DEFAULT 0,
    qty_reserved  REAL NOT NULL DEFAULT 0,
    unit_cost     REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (order_id, line_no)
);

CREATE TABLE IF NOT EXISTS production_order_operation (
    order_id          TEXT NOT NULL REFERENCES production_order(order_id) ON DELETE CASCADE,
    op_no             INTEGER NOT NULL,
    operation_name    TEXT NOT NULL,
    work_center_id    TEXT NOT NULL REFERENCES work_center(work_center_id),
    setup_min         REAL NOT NULL DEFAULT 0,
    run_min_per_unit  REAL NOT NULL DEFAULT 0,
    planned_start     TEXT,
    planned_end       TEXT,
    status            TEXT NOT NULL DEFAULT 'PENDING',
    PRIMARY KEY (order_id, op_no)
);

CREATE TABLE IF NOT EXISTS production_confirmation (
    confirmation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        TEXT NOT NULL REFERENCES production_order(order_id),
    qty_good        REAL NOT NULL,
    qty_scrap       REAL NOT NULL DEFAULT 0,
    labor_cost      REAL NOT NULL DEFAULT 0,
    confirmed_at    TEXT NOT NULL,
    confirmed_by    TEXT
);

-- ----------------------------------------------------------------------------
-- 6. PP -> MM 接口：采购申请
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS purchase_requisition (
    pr_id                 TEXT PRIMARY KEY,
    material_id           TEXT NOT NULL REFERENCES material(material_id),
    qty                   REAL NOT NULL CHECK (qty > 0),
    unit                  TEXT NOT NULL,
    required_date         TEXT NOT NULL,
    release_date          TEXT,
    status                TEXT NOT NULL DEFAULT 'OPEN'
                          CHECK (status IN ('OPEN', 'APPROVED', 'RECEIVED', 'CLOSED')),
    mrp_run_id            TEXT REFERENCES mrp_run(run_id),
    source_requisition_id TEXT,
    unit_cost             REAL NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL
);

-- ----------------------------------------------------------------------------
-- 7. 集成事件（Outbox）：PP 把业务事件交给 MM / FI / SD
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS integration_event (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type   TEXT NOT NULL,
    aggregate_id TEXT,
    payload      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'PENDING'
                 CHECK (status IN ('PENDING', 'PROCESSED', 'FAILED')),
    created_at   TEXT NOT NULL,
    processed_at TEXT
);

-- ----------------------------------------------------------------------------
-- 8. FI 模块占位表（仅用于演示事件驱动凭证，正式实现由 FI 组完成）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fi_voucher (
    voucher_id    TEXT PRIMARY KEY,
    voucher_type  TEXT NOT NULL,
    posting_date  TEXT NOT NULL,
    reference_id  TEXT,
    header_text   TEXT,
    total_amount  REAL NOT NULL DEFAULT 0,
    source_event  INTEGER,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fi_voucher_line (
    voucher_id    TEXT NOT NULL REFERENCES fi_voucher(voucher_id) ON DELETE CASCADE,
    line_no       INTEGER NOT NULL,
    account_code  TEXT NOT NULL,
    account_name  TEXT NOT NULL,
    debit         REAL NOT NULL DEFAULT 0,
    credit        REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (voucher_id, line_no)
);

-- ----------------------------------------------------------------------------
-- 9. 单据编号流水
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS number_range (
    scope   TEXT PRIMARY KEY,
    next_no INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_bom_product ON bom(product_id, status);
CREATE INDEX IF NOT EXISTS idx_batch_material ON inventory_batch(material_id, expiry_date);
CREATE INDEX IF NOT EXISTS idx_mrp_explosion_run ON mrp_explosion(run_id, level);
CREATE INDEX IF NOT EXISTS idx_po_status ON production_order(status, required_date);
CREATE INDEX IF NOT EXISTS idx_pr_status ON purchase_requisition(status, required_date);
CREATE INDEX IF NOT EXISTS idx_event_status ON integration_event(status, event_id);
