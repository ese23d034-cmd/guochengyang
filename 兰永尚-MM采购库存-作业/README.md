# 云谷鲜米粉 ERP · MM 采购与库存模块

> 课程项目：云谷鲜米粉 ERP（Project RiceFlow）
> 模块负责人：**兰永尚（MM 采购与供应链总监）**
> 技术栈：Python 3.10+ / SQLite / 标准库 REST API（零第三方依赖）

本仓库是兰永尚负责的 **MM（Materials Management，采购与库存）模块** 的独立可运行实现，覆盖项目文档中 MM 的职责：

- **Master Data**：物料主数据（大米、蔬菜、肉类、调味料、包装）、BOM 配方、按产品 ID 查询 BOM 的 API；
- **采购**：大米/蔬菜/肉类/碗盖等物料的采购订单管理，PR → PO 转换，供应商分配；
- **冷链收货**：按保质期生成批次，收货入库；
- **库存管理**：批次库存、FEFO 发料、效期预警、过期报损；
- **跨模块接口**：接收 PP 的采购申请，向 PP 发料，向 FI 发送采购收货/发料/报损事件。

---

## 1. 快速开始

要求 Python 3.10+（开发验证 3.13.14），**无需安装第三方包**。

```bash
# 1) 一键演示（重建 data/yungu_mm.db）
python run_demo.py

# 2) 测试（6 个用例）
python -m unittest discover -s tests -v

# 3) 启动 MM API
python -m mm.api --db data/yungu_mm.db --port 8100 --seed
# 打开 http://127.0.0.1:8100/health
```

Windows 可双击 `run_demo.bat` / `start_api.bat`。

### 在 VS Code 中运行

项目内置 `.vscode` 配置：

1. VS Code 打开 `yungu-mm-module` 根目录；
2. `Ctrl + Shift + P` → `Python: Select Interpreter` → Python 3.10+；
3. 按 `F5`，选择 **`MM：运行完整演示`**；
4. 测试：`F5` 选择 `MM：运行全部测试`；
5. API：`F5` 选择 `MM：启动 REST API`，打开 `http://127.0.0.1:8100/health`。

详见 [VSCODE运行指南.md](VSCODE运行指南.md)。

### 运行后生成

| 文件 | 内容 |
| --- | --- |
| `out/MM运行报告.txt` | 完整控制台报告 |
| `out/MM运行报告.md` | 可提交/展示的运行报告 |
| `out/mm_purchase_orders.csv` | 采购订单 |
| `out/mm_receipts.csv` | 收货批次 |
| `out/mm_inventory.csv` | 期末批次库存 |
| `out/mm_shelf_life.csv` | 效期预警 |
| `out/mm_goods_issues.csv` | 生产发料明细 |
| `out/mm_events.csv` | 集成事件 |
| `data/yungu_mm.db` | SQLite 演示数据库 |

---

## 2. 演示流程与结果

演示固定日期：2026-10-23 运行，2026-10-25 JIT 收货/生产发料。

### 2.1 输入

PP 的 MRP 运行输出 8 张采购申请：

| 物料 | 数量 | 需求日 |
| --- | ---: | --- |
| 大米 | 100 kg | 2026-10-25 |
| 牛腩 | 30 kg | 2026-10-25 |
| 生姜 | 2 kg | 2026-10-25 |
| 猪肥肠 | 20 kg | 2026-10-25 |
| 青菜 | 25 kg | 2026-10-26 |
| 香葱香菜包 | 300 包 | 2026-10-26 |
| 酸豆角包 | 150 包 | 2026-10-26 |
| 环保碗盖 | 1000 套 | 2026-10-26 |

### 2.2 执行结果

- **8 张采购订单**，按供应商拆单，金额 **¥4,868.00**；
- **8 张收货单**，全部生成批次和保质期；
- **效期预警**：青菜 25 kg（2 天）、牛腩 30 kg（3 天）、猪肥肠 20 kg（3 天）等；
- **过期批次**：牛腩 3 kg（10-24 到期）、青菜 6 kg（10-24 到期），不参与发料；
- **生产发料**：3 张生产订单（米粉 105kg、牛肉臊子 30kg、肥肠臊子 15kg），发料金额 **¥2,565.22**；
- **过期报损**：牛腩 3 kg + 青菜 6 kg，损失 **¥234.00**；
- **期末库存**：**¥4,211.65**，其中冷链仓 ¥1,572.12、原料仓 ¥2,639.54；
- **集成事件**：8 条采购订单创建、8 条采购收货、3 条生产发料、1 条过期报损。

### 2.3 FEFO 效果

牛腩发料：

- 过期批次 `BEEF-20261021`（3 kg，10-24 到期）被排除；
- 实际使用新批次 `POPUR-20261023-0002`（30 kg，10-28 到期）；
- 发料 22.248 kg，新批次剩余 7.752 kg。

大米发料：

- 期初 80 kg（2027-04-30）+ 新收货 100 kg（2027-10-25）；
- FEFO 先消耗 80 kg 旧批次，再消耗新批次 30.801 kg；
- 新批次剩余 69.199 kg。

---

## 3. 目录结构

```text
yungu-mm-module/
├─ run_demo.py                 # 一键演示
├─ run_demo.bat                # Windows 双击运行
├─ start_api.bat               # Windows 双击启动 API
├─ mm/
│  ├─ schema.sql               # 全部建表语句
│  ├─ db.py                    # 连接/事务/单号/事件
│  ├─ models.py                # Material/Supplier/Bom/Batch
│  ├─ repo.py                  # 数据访问层
│  ├─ master_data.py           # 物料主数据、BOM 查询/维护
│  ├─ procurement.py           # 采购申请→采购订单→收货
│  ├─ inventory.py             # 批次入库、FEFO 发料、效期、报损
│  ├─ seed.py                  # 20 物料/5 BOM/4 供应商/8 采购申请
│  ├─ demo.py                  # 端到端演示与报告
│  └─ api.py                   # REST API
├─ tests/test_mm.py            # 6 个测试
├─ .vscode/                    # F5 配置
├─ out/                        # 运行报告/CSV
└─ data/                       # SQLite（自动生成）
```

---

## 4. 数据表设计

完整 DDL：`mm/schema.sql`。

| 表 | 关键字段 | 作用 |
| --- | --- | --- |
| `warehouse` | `warehouse_id`、`warehouse_type` | 原料仓/冷链仓/成品仓 |
| `supplier` | `supplier_id`、`category`、`lead_time_days`、`rating` | 供应商主数据 |
| `material` | `material_type`、`procurement_type`、`standard_cost`、`safety_stock`、`MOQ`、`lot_size`、`shelf_life_days`、`default_supplier_id` | 物料主数据 |
| `bom` / `bom_item` | `product_id`、`base_qty`、`component_id`、`qty_per_base`、`scrap_rate` | 多级 BOM |
| `inventory_batch` | `batch_id`、`qty_on_hand`、`production_date`、`expiry_date`、`unit_cost`、`status` | 批次库存 |
| `inventory_movement` | `movement_type`、`qty`（入库正/出库负）、`amount`、`reference_no` | 库存流水 |
| `purchase_requisition` | `pr_id`、`material_id`、`qty`、`required_date`、`release_date`、`status` | PP → MM 接口 |
| `purchase_order` | `po_id`、`supplier_id`、`unit_cost`、`expected_date`、`status` | 采购订单 |
| `mm_receipt` / `mm_receipt_item` | `receipt_id`、`batch_id`、`qty`、`amount` | 收货与批次 |
| `mm_goods_issue` / `_item` | `issue_id`、`order_id`、`batch_id`、`qty`、`amount` | 生产发料 |
| `integration_event` | `event_type`、`aggregate_id`、`payload` | FI/PP 集成事件 |

关系：

```text
material 1─n bom 1─n bom_item
material 1─n inventory_batch 1─n inventory_movement
purchase_requisition 1─1 purchase_order 1─n mm_receipt
purchase_order 1─n mm_receipt_item
mm_goods_issue 1─n mm_goods_issue_item
```

---

## 5. 模块功能

### 5.1 物料主数据 / BOM

- 物料类型：FG 成品、SF 半成品、RM 原料、PK 包装；
- 主数据字段：单位、采购类型、标准成本、安全库存、MOQ、批量、提前期、保质期、默认仓、默认供应商；
- BOM：多级、版本、基准数量、组件损耗率；
- `query_bom(product_id)`：按产品 ID 返回配方、单位用量、损耗率、标准成本、估算材料成本；
- `where_used(component_id)`：反查某原料被哪些产品使用；
- `upsert_material` / `upsert_bom`：主数据维护接口。

### 5.2 采购

- `create_purchase_orders(run_id, order_date)`：读取 PP 的 OPEN 采购申请，按物料默认供应商生成采购订单；
- 记录采购订单金额、预计到货日、仓库；
- `receive_purchase_orders(run_id, receipt_date)`：JIT 收货，生成采购收货单、批次、库存流水，回写 PO/PR 状态；
- 供应商评分卡：PO 数量、采购金额、评级。

### 5.3 库存与效期

- 批次库存：数量、生产日期、到期日、单位成本、状态；
- **FEFO**：先到期先出，过期批次不能用于发料；
- `shelf_life_alerts(as_of, within_days)`：未来 N 天到期批次，返回剩余天数和金额；
- `expired_stock(as_of)`：已过期批次；
- `scrap_expired(as_of)`：过期报损，清零库存、写流水、发 `EXPIRED_SCRAP` 事件；
- `stock_summary` / `warehouse_stock` / `inventory_value`：库存金额与仓库分布。

### 5.4 生产发料

`issue_to_production(order_id, lines, issue_date)`：

- 每个物料按 FEFO 消耗批次；
- 每个批次生成一条发料明细，便于追溯；
- 库存不足整单回滚；
- 汇总金额，发 `GOODS_ISSUE` 事件（PP/FI 可消费）。

---

## 6. REST API

启动：

```bash
python -m mm.api --db data/yungu_mm.db --port 8100 --seed
```

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| GET | `/api/master-summary` | 主数据统计 |
| GET | `/api/materials?type=RM` | 物料列表 |
| GET | `/api/materials/{id}` | 物料卡片 |
| GET | `/api/materials/{id}/bom` | **按产品 ID 查 BOM** |
| GET | `/api/where-used/{component_id}` | 原料反查 |
| GET | `/api/suppliers` | 供应商 |
| GET | `/api/suppliers/scorecard` | 供应商评分卡 |
| GET | `/api/warehouses` | 仓库 |
| GET | `/api/purchase-requisitions` | 采购申请 |
| GET/POST | `/api/purchase-orders` | 采购订单列表/创建 |
| GET | `/api/purchase-orders/{id}` | 采购订单详情 |
| GET/POST | `/api/receipts` | 收货列表/收货 |
| GET | `/api/inventory` | 批次库存 |
| GET | `/api/inventory/summary` | 库存汇总 |
| GET | `/api/warehouse-stock` | 仓库库存 |
| GET | `/api/shelf-life?as_of=&days=` | 效期预警 |
| GET | `/api/expired?as_of=` | 已过期批次 |
| GET/POST | `/api/goods-issues` | 发料列表/生产发料 |
| GET | `/api/events` | 集成事件 |

示例：

```bash
curl http://127.0.0.1:8100/api/materials/FG-001/bom
curl http://127.0.0.1:8100/api/purchase-requisitions
curl http://127.0.0.1:8100/api/shelf-life?as_of=2026-10-25&days=7
curl -X POST http://127.0.0.1:8100/api/purchase-orders \
  -H "Content-Type: application/json" \
  -d '{"run_id":"MRP-20261023-0001","order_date":"2026-10-23"}'
```

---

## 7. 测试

```bash
python -m unittest discover -s tests -v
```

共 **6 个用例**：

- 物料/BOM 主数据与 BOM 查询；
- 采购申请→采购订单（8 张、¥4,868）→收货（8 张、批次/效期/状态）；
- 效期预警、过期批次、报损；
- FEFO 发料排除过期牛腩，使用新批次；
- 大米发料先消耗旧批次；
- 库存不足整单回滚。

---

## 8. 与项目文档的对应

| 文档职责 | 代码 |
| --- | --- |
| Material Master（大米、蔬菜、猪肉、调味料） | `mm/seed.py`、`material` 表 |
| BOM（一碗招牌牛肉米粉的原料消耗） | `bom` / `bom_item`、`master_data.query_bom` |
| API：按产品 ID 查询 BOM | `GET /api/materials/{id}/bom` |
| Purchase Order Management | `mm/procurement.py`、`purchase_order` |
| 冷链仓库收货/库存 | `inventory_batch`、`mm_receipt` |
| 鲜食原料效期预警 | `inventory.shelf_life_alerts` |
| 冷库发料 | `issue_to_production`、`mm_goods_issue` |
| 与 PP/FI 集成 | `purchase_requisition`、`integration_event` |

---

## 9. 设计取舍与简化

1. **单工厂/三仓**：原料仓、冷链仓、成品仓各一个；未实现多工厂调拨。
2. **收货一步完成**：JIT 收货直接生成批次，无 ASN、质检、退货流程。
3. **采购价格**：使用物料标准成本；未实现询价、比价、采购折扣。
4. **供应商分配**：按物料默认供应商，未实现多供应商配额/招标。
5. **效期**：按天粒度，未实现小时级冷链温度记录。
6. **报损**：过期批次直接报损，未实现审批流。
7. **权限**：MM API 无鉴权，适合本地课程演示。
8. **数据规模**：20 个物料、4 家供应商、8 张采购申请，足以覆盖流程；可扩展至全量物料。

---

## 10. 课堂演示建议（5 分钟）

1. 打开 `out/MM运行报告.txt`；
2. 讲 BOM：一碗牛肉米粉拆到米粉、牛肉臊子、青菜、碗盖；
3. 讲 PP 给 MM 的 8 张采购申请；
4. 讲采购订单按供应商拆单（米业/肉联/生鲜/包材）；
5. 讲 JIT 收货生成批次和保质期；
6. 讲效期预警：牛腩 3 kg、青菜 6 kg 已过期；
7. 讲 FEFO 发料：牛腩使用新批次，过期批次不投入生产；
8. 讲报损 234 元和期末库存 4,211.65 元；
9. 打开 `GET /api/materials/FG-001/bom` 展示 BOM 查询 API。

---

## 11. 提交清单

- [x] `mm/`：MM 模块全部代码
- [x] `mm/schema.sql`：物料/BOM/供应商/采购/库存表结构
- [x] `mm/demo.py`：端到端演示
- [x] `mm/api.py`：REST API
- [x] `tests/test_mm.py`：6 个测试
- [x] `out/`：运行报告 + 6 个 CSV
- [x] `README.md`：设计说明
- [x] `VSCODE运行指南.md`
- [x] `提交说明.md`
