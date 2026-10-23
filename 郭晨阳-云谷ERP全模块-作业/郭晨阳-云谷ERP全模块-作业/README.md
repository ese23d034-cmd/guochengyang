# 云谷鲜米粉 ERP · 全模块实现

> 课程项目：云谷鲜米粉 ERP 供应链与 FI/CO 集成（Project RiceFlow）
> 覆盖模块：**SD 销售 / PP 生产 / MM 采购 / FI 财务 / CO 管理会计 / HCM 人力资源**
> 技术栈：Python 3.10+ / SQLite / 标准库 REST API（零第三方依赖）

本项目把 `yungu.docx` 里的整个业务场景做成了一套可运行的迷你 ERP：从 42 家门店的 POS 销售、低于补货点自动要货、企业团餐订单，到中央工厂 MRP、采购收货、生产入库、门店配送、FI 自动凭证和 CO 门店/单品毛利，形成完整闭环。

PP 模块（郭晨阳负责）的 MRP 算法、生产订单生命周期、有限产能排产沿用独立 `pp/` 包；`erp/` 包在其上补齐 SD、MM、FI、CO、HCM 和总装演示。

---

## 1. 快速开始

要求：Python 3.10 及以上（开发验证 Python 3.13.14），**不需要安装任何第三方包**。

```bash
# 1) 一键运行全模块演示（会重建 data/yungu_erp.db）
python run_full_demo.py

# 2) 运行全部测试（23 个用例）
python -m unittest discover -s tests -v

# 3) 启动全模块 REST API
python -m erp.api --db data/yungu_erp.db --port 8001 --seed
# 打开 http://127.0.0.1:8001/health
```

Windows 下可双击 `run_full_demo.bat` / `start_erp_api.bat`。

### 在 VS Code 中运行

项目已内置 `.vscode` 配置：

1. VS Code 打开项目根目录 `yungu-erp`；
2. `Ctrl + Shift + P` → `Python: Select Interpreter` → 选择 Python 3.10+；
3. 按 `F5`，选择 **`ERP：运行全模块演示`**；
4. 测试：`F5` 选择 `ERP：运行全部测试`，或 Testing 侧边栏 Run All；
5. API：`F5` 选择 `ERP：启动全模块 API`，打开 `http://127.0.0.1:8001/health`。

详见 [VSCODE运行指南.md](VSCODE运行指南.md)。

### 运行后生成

| 文件 | 内容 |
| --- | --- |
| `out/ERP运行报告.txt` | 全流程控制台报告 |
| `out/ERP运行报告.md` | 可直接提交/展示的运行报告 |
| `out/erp_mrp_explosion.csv` | MRP 展开与净算明细 |
| `out/erp_purchase_orders.csv` | 采购订单 |
| `out/erp_store_inventory.csv` | 门店期末库存 |
| `out/erp_store_pnl.csv` | 门店利润中心 |
| `out/erp_product_margin.csv` | 单品毛利 |
| `out/erp_trial_balance.csv` | 科目发生额 |
| `data/yungu_erp.db` | 全模块 SQLite 数据库 |

---

## 2. 项目目标与痛点对应

| 文档中的痛点 | 本实现中的应对 |
| --- | --- |
| 门店销售与工厂脱节，热销品缺货、鲜食原料过期 | 门店 POS 扣减门店库存 → 低于补货点自动生成要货单 → MRP 按 BOM 展开；批次 + 保质期参与净算，临期批次不参与可用量 |
| 采购靠经验，鲜食原料损耗率 13% | 采购申请 → 采购订单 → JIT 收货；按供应商、提前期、批次效期管理，MM 输出 7 天临期预警 |
| 42 家店月底手工对账，算不清单店利润和单品毛利 | 业务事件自动生成 FI 凭证；CO 按门店利润中心分摊租金/人工/水电，输出门店损益与单品毛利 |
| 集团团餐订单 | SD 支持企业客户订单，作为独立需求进入 MRP，配送后自动确认收入/成本 |
| 排班与权限 | HCM 维护员工、门店/工厂排班、班次覆盖检查，以及基于角色的模块权限 |

---

## 3. 端到端流程

```text
HCM 排班/权限
      │
SD 门店 POS 销售 ──→ 门店库存低于补货点 ──→ 自动生成门店要货单
      │                                              │
      └── 企业团餐订单 ───────────────────────────────┤
                                                     ▼
PP MRP：门店要货 + 团餐需求 → 多级 BOM 展开 → 净算/批量/提前期
                                                     │
                         ┌───────────────────────────┴──────────────────┐
                         ▼                                              ▼
                 生产订单（自制件）                              采购申请（外购件）
                         │                                              │
                         │                                        MM 采购订单
                         │                                              │
                         │                                        JIT 收货/冷链批次
                         ▼                                              │
                 发料 → 报工 → 成品入库 ◀──────────────────────────────┘
                         │
                         ▼
SD 工厂成品仓 → 门店配送 / 团餐配送 → 门店次日 POS
                         │
                         ▼
FI 事件驱动凭证：采购/发料/报工/入库/配送/销售
                         │
                         ▼
CO 门店利润中心、单品毛利、渠道结构
```

演示数据：

- **42 家直营门店**主数据（其中滨江店、城西店参与完整流程）+ 1 个中央工厂；
- 20 个物料、5 套 BOM、20 个期初批次；
- 4 家供应商、1 家企业客户、17 名关键岗位员工、48 条排班。

---

## 4. 演示场景与结果

### 4.1 门店销售与补货

期初门店库存：

| 门店 | 商品 | 数量 | 补货点 | 目标库存 |
| --- | --- | ---: | ---: | ---: |
| 滨江店 | 招牌牛肉米粉 | 240 碗 | 220 | 400 |
| 城西店 | 招牌牛肉米粉 | 120 碗 | 100 | 160 |
| 城西店 | 酸辣肥肠米粉 | 80 碗 | 60 | 160 |

2026-10-23 POS 销售：

- 滨江店卖出 40 碗牛肉米粉；
- 城西店卖出 40 碗牛肉米粉 + 40 碗肥肠米粉；
- 收入 ¥2,080.00，成本 ¥1,023.31，毛利 ¥1,056.69。

销售后滨江店剩 200 碗（低于 220），城西店剩 80 / 40（低于 100 / 60），SD 自动生成：

- `REQ-20261023-0001` 滨江店：牛肉米粉 200 碗；
- `REQ-20261023-0002` 城西店：牛肉米粉 80 碗 + 肥肠米粉 120 碗。

另外，云谷科技园下 30 碗牛肉米粉团餐订单，单价 ¥15，需求日 10-26。

### 4.2 MRP 与采购

MRP 将门店要货 280 + 80 + 120 碗和团餐 30 碗合并计算：

- FG-001 招牌牛肉米粉：**310 碗**
- FG-002 酸辣肥肠米粉：**120 碗**
- 半成品：鲜湿米粉 105 kg、红烧牛肉臊子 30 kg、酸辣肥肠臊子 15 kg
- 采购申请 8 张：大米 100 kg、牛腩 30 kg、生姜 2 kg、猪肥肠 20 kg、青菜 25 kg、香葱香菜包 300 包、酸豆角包 150 包、碗盖 1000 套
- 2 条效期预警：牛腩 3 kg、青菜 6 kg 在需求日前到期，不参与净算

关键 MRP 逻辑：

- 低层码：同一物料的所有需求汇总到最深层级统一净算；
- 多级 BOM：成品 → 半成品 → 原料逐层展开，组件损耗率参与计算；
- 安全库存与批量：可用量扣安全库存，净需求先满足 MOQ 再向上取整；
- 有效期：需求日前到期的批次不参与净算；
- 提前期：采购建议下单日 = 需求日 − 供应商提前期。

### 4.3 采购、生产与配送

- MM 将 8 张采购申请按物料类别分配给 4 家供应商，生成 8 张采购订单；
- 2026-10-25 JIT 收货 ¥4,868.00，按保质期生成批次；
- PP 在 10-25 生产 3 个半成品，在 10-26 生产 2 个成品，全部入库；
- SD 配送：滨江店 200 碗、城西店 200 碗（80 + 120）、企业客户 30 碗；
- 工厂成品库存清零，门店库存与移动平均成本同步更新。

### 4.4 财务与管理会计结果

- FI：期初建账 1 张 + 业务凭证 30 张 + 门店期间费用凭证 2 张；
- 试算平衡：借方合计 = 贷方合计；
- 利润表：收入 **¥6,710.00**，成本 **¥3,414.77**，毛利 **¥3,295.23**（49.1%），期间费用 ¥2,700.00，净利润 **¥595.23**（8.9%）；
- 门店利润中心：

| 利润中心 | 收入 | 毛利 | 期间费用 | 营业利润 | 净利率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 城西店 | ¥3,380.00 | ¥1,695.79 | ¥1,200.00 | ¥495.79 | 14.7% |
| 滨江店 | ¥2,880.00 | ¥1,427.45 | ¥1,500.00 | ¥-72.55 | -2.5% |
| 企业团餐事业部 | ¥450.00 | ¥171.99 | ¥0.00 | ¥171.99 | 38.2% |

- 单品毛利：招牌牛肉米粉 48.6%，酸辣肥肠米粉 50.6%；
- 滨江店本期在分摊固定费用后接近盈亏平衡（-2.5%），这正是 CO 要提前发现的问题。

---

## 5. 目录结构

```text
yungu-erp/
├─ run_full_demo.py             # 全模块演示入口
├─ run_full_demo.bat            # Windows 双击运行
├─ start_erp_api.bat            # Windows 双击启动 API
├─ pp/                          # PP 生产模块（MRP / 排产 / 生产订单）
│  ├─ mrp.py                    # MRP 核心
│  ├─ service.py                # 生产订单生命周期
│  ├─ scheduling.py             # 有限产能排产
│  ├─ api.py                    # PP 独立 API（可选）
│  └─ schema.sql                # PP 基础表
├─ erp/
│  ├─ db.py                     # 基础表 + 扩展表 + 全量种子
│  ├─ schema_extra.sql          # SD/MM/FI/CO/HCM 扩展表
│  ├─ seed_full.py              # 42 店/供应商/员工/排班/门店库存
│  ├─ inventory.py              # 批次 FEFO、门店移动平均库存
│  ├─ sd.py                     # POS/团餐/补货/配送
│  ├─ mm.py                     # 采购订单/收货/效期预警
│  ├─ fi.py                     # 总账/凭证/试算/利润表
│  ├─ co.py                     # 门店利润中心/单品毛利
│  ├─ hcm.py                    # 员工/排班/权限
│  ├─ demo.py                   # 端到端演示与报告
│  └─ api.py                    # 全模块 REST API
├─ tests/
│  ├─ test_pp.py                # PP 17 个用例
│  └─ test_full.py              # 全模块 6 个用例
├─ .vscode/                     # F5 配置
├─ out/                         # 运行报告与 CSV
└─ data/                        # SQLite 数据库（自动生成）
```

---

## 6. 数据表设计

完整 DDL：`pp/schema.sql`（PP 基础表）+ `erp/schema_extra.sql`（扩展表）。

### 6.1 PP 核心表

| 表 | 作用 |
| --- | --- |
| `material` | 物料主数据：FG/SF/RM/PK、自制/外购、单位、标准成本、安全库存、MOQ、批量、提前期、保质期 |
| `bom` / `bom_item` | 多级 BOM、版本、有效期、组件用量、损耗率 |
| `work_center` / `routing` | 工作中心日产能、费率、工艺路线 |
| `inventory_batch` / `inventory_movement` | 批次库存、保质期、库存流水 |
| `inventory_reservation` | 生产订单组件预留 |
| `store_requisition` / `store_requisition_item` | SD → PP 门店要货单 |
| `mrp_run` / `mrp_explosion` / `mrp_exception` | MRP 运行、展开/净算明细、异常 |
| `production_order` / `_component` / `_operation` / `production_confirmation` | 生产订单、组件、工序、报工 |
| `purchase_requisition` | PP → MM 采购申请 |
| `integration_event` | 跨模块 Outbox 事件 |
| `fi_voucher` / `fi_voucher_line` | FI 凭证（PP 演示基础表） |

### 6.2 SD 表

| 表 | 关键字段 | 作用 |
| --- | --- | --- |
| `store` | `store_id`、`store_type`（STORE/CORP） | 42 家门店 + 企业团餐利润中心 |
| `store_inventory` | `qty_on_hand`、`avg_cost`、`reorder_point`、`target_stock` | 门店成品库存与补货策略 |
| `pos_sale` / `pos_sale_item` | 门店、日期、收入、成本、明细 | POS 零售单 |
| `sales_order` / `sales_order_item` | 客户、需求日、单价、状态 | 企业团餐订单 |
| `outbound_delivery` / `_item` | 配送类型、门店/客户、批次、成本 | 工厂到门店/客户的配送 |
| `sales_fact` | 渠道、门店、产品、收入、成本 | CO 统一销售事实表 |

### 6.3 MM / FI / CO / HCM 表

| 模块 | 表 | 作用 |
| --- | --- | --- |
| MM | `supplier` | 供应商类别、提前期、评级 |
| MM | `purchase_order` | 采购订单（由 PR 转换） |
| MM | `mm_receipt` / `mm_receipt_item` | 采购收货单与批次 |
| FI | `gl_account` | 会计科目表 |
| FI | `fi_voucher` / `fi_voucher_line` | 自动凭证与借贷行 |
| CO | `store_fixed_cost` | 门店租金/人工/水电/其他分摊 |
| CO | `sales_fact` | 收入、成本、渠道、门店/产品维度 |
| HCM | `employee` | 员工、部门、门店、岗位、时薪 |
| HCM | `shift` / `shift_requirement` | 排班与班次最低人数 |
| HCM | `app_user` / `role_permission` | 用户、角色、模块查看/编辑权限 |

主要关系：

```text
material 1─n bom 1─n bom_item
store_requisition 1─n store_requisition_item
mrp_run 1─n mrp_explosion / mrp_exception
production_order 1─n production_order_component / production_order_operation
purchase_requisition 1─1 purchase_order 1─n mm_receipt
store 1─n store_inventory / pos_sale / outbound_delivery
sales_fact n─1 material / store
fi_voucher 1─n fi_voucher_line
employee 1─n shift；app_user n─1 role_permission
```

---

## 7. 模块说明

### 7.1 SD 销售与分销（刘浩焰）

- **POS 零售**：校验门店库存，按移动平均成本结转 COGS，写入 `pos_sale` 和 `sales_fact`，发 `POS_SALE` 事件；
- **门店补货**：`qty_on_hand < reorder_point` 时，按 `target_stock − qty_on_hand` 自动生成要货单，避免重复提交；
- **企业团餐**：`sales_order` 记录客户、数量、团餐价、需求日，作为独立需求进入 MRP；
- **配送**：从成品仓按 FEFO 扣批次，门店库存按移动加权平均入账；团餐配送直接确认收入/成本；
- **门店库存**：支持期初、配送入库、POS 出库、补货点/目标库存。

### 7.2 PP 生产制造（郭晨阳）

- MRP：读取门店要货 + 团餐独立需求，按低层码逐层展开；
- 净算：库存、安全库存、批次效期、MOQ、批量、采购提前期；
- 输出：生产订单 + 采购申请；生产订单含组件需求、工艺路线、计划起止；
- 生命周期：`PLANNED → RELEASED → IN_PROCESS → CONFIRMED → COMPLETED`；
- 发料按预留优先、FEFO；成品入库按批次 + 生产日期 + 保质期；
- 详细算法见 `pp/mrp.py` 顶部注释和原 PP 模块 README 说明。

### 7.3 MM 采购与库存（兰永尚）

- 采购申请 → 采购订单：按物料类别映射供应商，带预计到货日；
- 收货：生成批次、效期，写库存流水与收货单，更新 PO/PR 状态；
- 效期预警：查询未来 N 天到期批次，供门店/工厂临期处理；
- 供应商评分卡：PO 数量、采购金额、评级。

### 7.4 FI 财务会计

- 科目表：资产、负债、权益、收入、费用；
- 事件驱动凭证：

| 事件 | 借方 | 贷方 |
| --- | --- | --- |
| 采购收货 | 1403 原材料 | 2202 应付账款 |
| 生产发料 | 5001 生产成本 | 1403 原材料 |
| 生产报工 | 5001 生产成本 | 2211 应付职工薪酬 |
| 成品入库 | 1405 库存商品 | 5001 生产成本 |
| 配送到店 | 1406 门店库存商品 | 1405 库存商品 |
| POS 销售 | 1002 银行存款 / 6401 主营业务成本 | 6001 主营业务收入 / 1406 门店库存商品 |
| 团餐配送 | 1122 应收账款 / 6401 主营业务成本 | 6001 主营业务收入 / 1405 库存商品 |
| 门店期间费用 | 6601 销售费用 | 2241 其他应付款 |

- 期初建账：原材料 + 库存商品 + 门店库存，贷方期初余额；
- 输出：凭证、试算平衡、利润表（收入/成本/毛利/费用/净利润）。

### 7.5 CO 管理会计

- 门店利润中心：收入 − COGS − 租金/人工/水电/其他；
- 单品毛利：销量、收入、成本、毛利率；
- 渠道结构：POS / GROUP；
- 与 FI 利润表勾稽：各利润中心营业利润之和 = FI 净利润。

### 7.6 HCM 人力资源

- 员工主数据：门店/工厂/总部、岗位、时薪；
- 排班：4 天 × 12 名一线员工，早班/中班；
- 班次覆盖：按 `shift_requirement` 检查缺口；
- 权限：7 个角色 × 6 个模块的查看/编辑矩阵，`can_access()` 校验；
- 演示校验：郭晨阳可编辑 PP、不可编辑 FI；刘浩焰可编辑 SD；兰永尚可编辑 MM 等。

---

## 8. REST API

启动：

```bash
python -m erp.api --db data/yungu_erp.db --port 8001 --seed
```

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| GET | `/api/materials` | 物料列表 |
| GET | `/api/materials/{id}/bom` | 按产品查 BOM |
| GET | `/api/sd/store-inventory` | 门店库存 |
| GET/POST | `/api/sd/sales` | 查询/登记 POS 销售 |
| POST | `/api/sd/reorder-check` | 检查补货点并生成要货单 |
| GET | `/api/sd/requisitions` | 门店要货单 |
| POST | `/api/sd/corporate-orders` | 创建团餐订单 |
| GET | `/api/sd/deliveries` | 配送单 |
| POST | `/api/sd/deliveries/store` | 门店配送 |
| POST | `/api/sd/deliveries/corporate` | 团餐配送 |
| POST | `/api/pp/mrp` | 运行 MRP（支持 `extra_demands`） |
| GET | `/api/pp/production-orders` | 生产订单 |
| POST | `/api/pp/production-orders/{id}/release` | 下达 |
| POST | `/api/pp/production-orders/{id}/issue` | 发料 |
| POST | `/api/pp/production-orders/{id}/confirm` | 报工 |
| POST | `/api/pp/production-orders/{id}/goods-receipt` | 成品入库 |
| GET/POST | `/api/mm/purchase-orders` | 采购订单 |
| POST | `/api/mm/receipts` | 采购收货 |
| GET | `/api/mm/shelf-life` | 效期预警 |
| POST | `/api/fi/post` | 消费事件生成凭证 |
| POST | `/api/fi/opening` | 期初建账 |
| POST | `/api/fi/fixed-costs` | 门店期间费用 |
| GET | `/api/fi/vouchers` | 凭证 |
| GET | `/api/fi/trial-balance` | 试算平衡 |
| GET | `/api/fi/income-statement` | 利润表 |
| GET | `/api/co/store-pnl` | 门店利润中心 |
| GET | `/api/co/product-margin` | 单品毛利 |
| GET | `/api/co/channel-mix` | 渠道结构 |
| GET | `/api/hcm/employees` | 员工 |
| GET | `/api/hcm/shifts` | 排班 |
| GET | `/api/hcm/coverage` | 班次覆盖 |
| GET | `/api/hcm/access/{user}/{module}` | 权限校验 |

示例：

```bash
curl http://127.0.0.1:8001/api/sd/store-inventory
curl http://127.0.0.1:8001/api/materials/FG-001/bom
curl http://127.0.0.1:8001/api/co/store-pnl
curl "http://127.0.0.1:8001/api/hcm/access/guo.chenyang/PP?edit=1"
```

---

## 9. 测试

```bash
python -m unittest discover -s tests -v
```

当前 **23 个用例全部通过**：

- PP 17 个：BOM、成本卷算、MRP 数量/日期/批量/效期、多级汇总、生产订单生命周期、库存不足回滚、API；
- 全模块 6 个：42 店种子、HCM 排班/权限、POS 扣减与补货、团餐 + MRP、MM 采购收货、全流程 FI/CO 勾稽。

---

## 10. 与项目文档的对应

| 文档角色 | 职责 | 代码 |
| --- | --- | --- |
| SD 刘浩焰 | POS、团餐、门店要货、配送 | `erp/sd.py`、`store_requisition` |
| PP 郭晨阳 | MRP、生产订单、排产、成品入库 | `pp/mrp.py`、`pp/service.py`、`pp/scheduling.py` |
| MM 兰永尚 | 采购订单、冷链收货、效期预警 | `erp/mm.py`、`erp/inventory.py` |
| FI 财务 | 科目表、自动凭证 | `erp/fi.py`、`fi_voucher` |
| CO 管理会计 | 门店利润中心、成本分摊、单品毛利 | `erp/co.py`、`store_fixed_cost` |
| HCM 人力资源 | 排班、权限 | `erp/hcm.py`、`shift`、`role_permission` |
| Master Data | 物料、BOM、供应商、科目 | `pp/seed.py`、`erp/seed_full.py` |

---

## 11. 设计取舍与已知简化

1. **数据规模**：保留 42 家门店主数据，但只让滨江店/城西店参与完整流程；员工 17 名关键岗位，非 650 人全量排班。
2. **日期固定**：演示固定 2026-10-23 ~ 10-26，保证每次运行结果可复现。
3. **单工厂/单仓网**：没有多工厂调拨、运输排程、门店间调拨。
4. **成本口径**：标准成本 + 移动平均；未分摊制造费用差异、未做标准成本差异分析。
5. **MM/FI 为教学简化**：供应商收货一步完成；无采购审批流、发票校验、付款、税码。
6. **HCM**：排班为固定模板 + 覆盖检查，未实现请假/调班/考勤打卡。
7. **权限**：仅在服务层/API 层演示 `can_access()`，没有登录、Token、会话。
8. **技术栈**：Python + SQLite 便于课程演示和单机运行；生产环境可替换 PostgreSQL / FastAPI，表结构与领域逻辑可保留。

---

## 12. 课堂演示建议（8 分钟）

1. 打开 `out/ERP运行报告.txt`，先讲 42 店 + 1 工厂的全景；
2. 讲 SD：POS 卖出 40 碗 → 库存 200 < 补货点 220 → 自动要货 200 碗；
3. 讲 PP：门店要货 + 团餐 30 碗 → MRP 310 碗 → 5 张生产订单、8 张采购申请；
4. 讲 MM：8 张 PO 按供应商拆分，JIT 收货，批次效期；
5. 讲生产：半成品 10-25、成品 10-26，FEFO 发料，成品批次入库；
6. 讲 SD 配送：工厂成品清零，门店库存与移动平均成本更新；
7. 讲 FI：采购/发料/报工/入库/配送/销售自动凭证，试算平衡；
8. 讲 CO：城西店净利率 14.7%，滨江店 -2.5%，单品毛利 48.6% / 50.6%；
9. 打开 `pp/mrp.py` 打断点，演示净需求 `net_qty` 的计算。

---

## 13. 提交清单

- [x] `pp/`：PP 模块（MRP、生产订单、排产、API、表结构）
- [x] `erp/`：SD/MM/FI/CO/HCM 全模块
- [x] `erp/schema_extra.sql`：扩展表结构
- [x] `erp/demo.py`：端到端演示
- [x] `erp/api.py`：全模块 REST API
- [x] `tests/`：23 个自动化用例
- [x] `out/`：运行报告 + CSV
- [x] `README.md`：设计说明
- [x] `VSCODE运行指南.md`：运行与调试
- [x] `提交说明.md`：姓名/模块/验收步骤

如课程要求 Word/PDF 版设计文档，可把本 README 的“数据表设计 / 模块说明 / 演示结果”三节整理成文档；所有数字均来自实际运行结果，可复现。
