# 云谷鲜米粉 ERP · PP 生产制造模块

> 课程项目：云谷鲜米粉 ERP 供应链与 FI/CO 集成（Project RiceFlow）
> 模块负责人：**郭晨阳（PP 生产制造总监）**
> 技术栈：Python 3.10+ / SQLite / 标准库 REST API（零第三方依赖）

本仓库是郭晨阳负责的 **PP（Production Planning，生产制造）模块** 的完整可运行实现。它把项目文档里 PP 的职责落成了代码：

- 读取 SD 门店要货单；
- 按多级 BOM 展开物料需求；
- 结合库存、安全库存、保质期、最小起订量/批量做净算；
- 生成 **生产订单**（自制件）和 **采购申请**（外购件，交给 MM）；
- 对生产订单做有限产能排产；
- 执行下达、发料、报工、成品入库，并按批次/效期形成库存；
- 通过集成事件把发料/报工/入库成本交给 FI 自动记账。

---

## 1. 快速开始

要求：Python 3.10 及以上（开发验证使用 3.12），**不需要安装任何第三方包**。

```bash
# 1) 一键跑完整演示（会重建 data/yungu_pp.db，生成 out/ 下的报告）
python run_demo.py

# 2) 跑自动化测试（17 个用例）
python -m unittest discover -s tests -v

# 3) 启动 REST API
python -m pp.api --db data/yungu_pp.db --port 8000 --seed
# 浏览器/命令行访问 http://127.0.0.1:8000/health
```

也可以使用 CLI：

```bash
python -m pp.cli demo                 # 完整演示
python -m pp.cli init --db data/yungu_pp.db --reset
python -m pp.cli mrp --db data/yungu_pp.db --date 2026-10-23
python -m pp.cli serve --db data/yungu_pp.db --port 8000 --seed
```

Windows 下也可以直接双击：

- `run_demo.bat`：一键跑演示（自动查找 `py -3` 或 `python`）；
- `start_api.bat`：启动 REST API（首次会自动建库并写入演示数据）。

### 在 VS Code 中运行

项目已内置 `.vscode` 配置，打开项目根目录后：

1. `Ctrl + Shift + P` → `Python: Select Interpreter` → 选择 Python 3.13（或 3.10+）；
2. 按 `F5`，选择 `PP：运行完整演示`；
3. 测试：Testing 侧边栏 → `Run All Tests`，或 `F5` 选择 `PP：运行全部测试`；
4. API：`F5` 选择 `PP：启动 REST API`，浏览器打开 `http://127.0.0.1:8000/health`。

更详细的步骤、断点调试和常见问题见 [VSCODE运行指南.md](VSCODE运行指南.md)。

演示使用固定业务日期 **2026-10-23**（周五），门店要货需求日 **2026-10-26**（周一），便于课堂复现。

### 运行后生成的文件

| 文件 | 内容 |
| --- | --- |
| `out/demo_report.txt` | 完整控制台报告（主数据、MRP、订单、采购申请、执行、凭证） |
| `out/MRP运行报告.md` | 可直接提交/展示的 Markdown 运行报告 |
| `out/mrp_explosion.csv` | MRP 展开与净算明细（Excel 可打开） |
| `out/production_orders.csv` | 生产订单清单 |
| `out/purchase_requisitions.csv` | 采购申请清单 |
| `data/yungu_pp.db` | SQLite 演示数据库 |

---

## 2. 目录结构

```text
yungu-pp-module/
├─ run_demo.py                  # 一键演示入口
├─ run_demo.bat                 # Windows 双击演示
├─ start_api.bat                # Windows 双击启动 API
├─ VSCODE运行指南.md             # VS Code 运行/调试/排错说明
├─ .vscode/
│  ├─ launch.json               # F5：演示 / API / 测试 / 当前文件
│  ├─ tasks.json                # Ctrl+Shift+B 任务
│  ├─ settings.json             # UTF-8 / unittest / PYTHONUTF8
│  └─ extensions.json           # 推荐 Python 扩展
├─ pp/
│  ├─ schema.sql                # 全部建表语句（PP 主表 + 接口表 + FI 占位）
│  ├─ models.py                 # 领域对象（Material / Bom / Batch ...）
│  ├─ db.py                     # 连接、事务、单号、Outbox 事件
│  ├─ repo.py                   # 数据访问层（SQL 集中在此）
│  ├─ costing.py                # 标准成本逐层卷算、订单成本估算
│  ├─ mrp.py                    # ★ MRP 核心（BOM 展开/净算/批量/提前期）
│  ├─ scheduling.py             # 有限产能排产
│  ├─ service.py                # 生产订单生命周期、要货单接口
│  ├─ stubs.py                  # MM 收货 / FI 记账占位（演示用）
│  ├─ seed.py                   # 演示主数据与期初库存
│  ├─ demo.py                   # 端到端演示与报告生成
│  ├─ api.py                    # 标准库 REST API
│  └─ cli.py                    # 命令行入口
├─ tests/test_pp.py             # 17 个 unittest 用例
├─ data/                        # 运行时数据库（自动生成）
└─ out/                         # 运行时报告（自动生成）
```

---

## 3. 与项目文档的对应关系

项目文档中 PP 的任务是：

> “Write core MRP logic function: Read Store Requisition Order data, combine with BOM to explode component requirement; generate Production Order and Purchase Requisition.”
> “Central factory MRP calculation (compute raw-material demand based on store requisition orders and product BOM), production scheduling for fresh rice noodles and side dishes, finished-goods warehouse receipt.”

代码对应关系：

| 文档要求 | 实现位置 |
| --- | --- |
| 读取门店要货单 | `repo.list_requisitions` + `mrp.MrpEngine._run` |
| 按 BOM 逐层展开 | `mrp.MrpEngine._plan_material`、`_create_production_order` |
| 计算原料/包装需求 | BOM 组件用量 × 订单数量 ×（1 + 损耗率） |
| 生成生产订单 | `_create_production_order` → `production_order` |
| 生成采购申请 | `_create_purchase_requisition` → `purchase_requisition` |
| 鲜米粉/臊子排产 | `scheduling.schedule_orders`，按工作中心日产能正排 |
| 成品入库 | `service.goods_receipt`，按批次 + 生产日期 + 保质期 |
| 与 SD/MM/FI/CO 集成 | `store_requisition` / `purchase_requisition` / `integration_event` |

---

## 4. 数据表设计

完整 DDL 见 `pp/schema.sql`。核心表如下。

### 4.1 主数据与 BOM

| 表 | 作用 | 关键字段 |
| --- | --- | --- |
| `material` | 物料主数据 | `material_id`、`material_type`（FG/SF/RM/PK）、`procurement_type`（IN_HOUSE/EXTERNAL）、`base_unit`、`standard_cost`、`safety_stock`、`min_order_qty`、`lot_size`、`lead_time_days`、`shelf_life_days` |
| `bom` | BOM 头 | `bom_id`、`product_id`、`version`、`base_qty`、`valid_from/valid_to` |
| `bom_item` | BOM 行 | `bom_id`、`line_no`、`component_id`、`qty_per_base`、`scrap_rate` |
| `work_center` | 工作中心 | `daily_capacity_min`、`cost_rate_per_min` |
| `routing` | 工艺路线 | 产品 + 工序号 + 工作中心 + `setup_min` + `run_min_per_unit` |

多级 BOM 示例（鲜米粉）：

```text
FG-001 招牌牛肉米粉（1 碗）
├─ SF-001 鲜湿米粉        0.25 kg（损耗 2%）
│  └─ BOM-SF-001（1 kg 米粉）
│     ├─ RM-001 大米      1.05 kg（损耗 0.5%）
│     ├─ RM-002 淀粉      0.02 kg
│     └─ RM-003 食用油    0.005 kg
├─ SF-002 红烧牛肉臊子    0.08 kg（损耗 1%）
│  └─ BOM-SF-002（1 kg 臊子）
│     ├─ RM-004 牛腩      0.72 kg（损耗 3%）
│     ├─ RM-005 生姜      0.02 kg
│     ├─ RM-006 生抽      0.05 kg
│     └─ RM-007 香料包    0.01 kg
├─ RM-010 青菜            0.05 kg（损耗 8%）
├─ RM-011 香葱香菜包      1 包
├─ PK-001 环保碗盖        1 套（损耗 1%）
└─ PK-002 一次性餐具包    1 套
```

### 4.2 库存（鲜食关键：批次 + 效期）

| 表 | 作用 | 关键字段 |
| --- | --- | --- |
| `inventory_batch` | 批次库存 | `material_id`、`batch_no`、`qty_on_hand`、`qty_reserved`、`production_date`、`expiry_date`、`unit_cost`、`status` |
| `inventory_movement` | 库存流水 | `movement_type`、`qty`（入库正/出库负）、`amount`、`order_id`、`reference_no` |
| `inventory_reservation` | 生产订单组件预留 | `order_id`、`batch_id`、`qty`、`qty_consumed`、`status` |

> MRP 的可用库存会排除：已预留、冻结、以及在需求日期前已经过期的批次。这是“鲜食”与普通制造业 MRP 最大的不同。

### 4.3 SD → PP 接口

| 表 | 作用 |
| --- | --- |
| `store_requisition` | 门店要货单头（`SUBMITTED` / `PLANNED` / `CLOSED`） |
| `store_requisition_item` | 要货明细：成品 + 数量 |

### 4.4 MRP 运行记录

| 表 | 作用 |
| --- | --- |
| `mrp_run` | 每次运行的编号、日期、统计信息 |
| `mrp_explosion` | 每个物料每个需求日的毛需求、可用库存、净需求、建议数量、单号类型、下达日 |
| `mrp_exception` | 计划异常（提前期不足、BOM 缺失、效期损耗、排产延误等） |

### 4.5 PP 输出

| 表 | 作用 |
| --- | --- |
| `production_order` | 生产订单头：计划量、良品/废品、状态、需求日/开始日、计划起止、材料/人工成本 |
| `production_order_component` | 生产订单组件需求、已发料、预留 |
| `production_order_operation` | 工序及排产结果 |
| `production_confirmation` | 报工记录 |
| `purchase_requisition` | 采购申请（对外购件） |

### 4.6 集成与 FI 占位

| 表 | 作用 |
| --- | --- |
| `integration_event` | Outbox 事件表：`GOODS_ISSUE`、`PRODUCTION_CONFIRMED`、`GOODS_RECEIPT` 等 |
| `fi_voucher` / `fi_voucher_line` | **仅演示用**的凭证表，正式实现由 FI 组完成 |

---

## 5. MRP 算法说明

入口：`pp/mrp.py::run_mrp`。

### 5.1 计算步骤

1. **取需求**：读取状态为 `SUBMITTED` 的门店要货单；超出计划展望期（默认 30 天）的不参与本次计算；需求日早于运行日的按运行日紧急处理并告警。
2. **算低层码（Low-Level Code）**：从所有 BOM 根节点 DFS，`LLC(子件) = max(LLC(父件) + 1)`。同一物料可能被多个产品使用，所有需求汇总到它最深的层级统一净算，避免重复下单。
3. **按层净算**：对每一层的每个物料，把需求按需求日期汇总，按日期升序模拟批次消耗（FEFO，先到期先出）：

   ```text
   1) 剔除在需求日之前已经过期的批次（鲜食损耗，不能用于净算）；
   2) 可用量 = 当前批次结存 − 安全库存（可能为负，表示安全库存已被占用）；
   3) 净需求 = max(0, 毛需求 − 可用量)；
   4) 若有净需求：建议数量 = max(最小起订量, 净需求)，再向上取整到批量 lot_size 的整数倍；
      计划入库视为需求日当天到货，并按物料保质期生成一个新批次；
   5) 从批次中按先到期先出扣减本次需求，余额结转到下一个需求日。
   ```

   这样既处理了“需求日不同、批次效期不同”的情况，也不会把已过期库存重复算进可用量。

4. **生成单据**：
   - 自制件（`IN_HOUSE`）→ **生产订单**，状态 `PLANNED`；
     组件需求按 `订单数量 × 单位用量 / 基准数量 × (1 + 损耗率)` 展开，组件需求日期 = 生产订单开始日。
   - 外购件（`EXTERNAL`）→ **采购申请**，状态 `OPEN`；
     建议下单日 = 需求日 − 采购提前期。
5. **排产**：按需求日、单号顺序，在各自工作中心的日产能上正排；工序不跨天（超日产能按加班处理并告警）。
6. **回写与留痕**：要货单状态改为 `PLANNED`，写入 `mrp_run`、`mrp_explosion`、`mrp_exception`；同一张要货单不会被第二次 MRP 重复计算。

### 5.2 关键细节（答辩可讲）

| 细节 | 代码行为 | 演示中的体现 |
| --- | --- | --- |
| 多级 BOM | 自制半成品会生成自己的生产订单，再向下展开 | 5 张生产订单：FG×2 + SF×3 |
| 需求汇总 | 两张要货单的鲜湿米粉需求合并 | 102 kg 毛需求 → 扣 8 kg 库存 → 净 94 kg → 批量 5 → **95 kg** |
| 安全库存 | 可用量要扣安全库存；表中出现负数表示安全库存已被占用 | 大米：80 − 50 = 30 kg 可用 |
| 保质期 | 需求日前到期的批次不参与净算 | 牛腩 3 kg 于 10-24 到期，需求日 10-25 → 不可用，另采购 30 kg |
| 最小起订量/批量 | 先满足 MOQ，再向上取整 | 碗盖净需求仅 4 套，MOQ 1000 → **采购 1000 套** |
| 采购提前期 | 下单日 = 需求日 − lead time | 大米 10-25 需用、提前期 1 天 → 10-24 下单 |
| 组件损耗率 | 青菜 8%、牛腩 3%、肥肠 5% 等 | 280 碗 × 0.05 × 1.08 = 15.12 kg |
| 计划异常 | 提前期不足、临期、BOM 缺失、排产延误 | 牛腩/青菜两条临期预警 |

### 5.3 演示场景的核心数字

输入：

- 滨江店：招牌牛肉米粉 200 碗，需求日 2026-10-26
- 城西店：招牌牛肉米粉 80 碗 + 酸辣肥肠米粉 120 碗，需求日 2026-10-26

输出：

- **5 张生产订单**：FG-001 280 碗、FG-002 120 碗、SF-001 95 kg、SF-002 30 kg、SF-003 15 kg
- **8 张采购申请**：大米 75 kg、牛腩 30 kg、生姜 2 kg、猪肥肠 20 kg、青菜 24 kg、香葱香菜包 300 包、酸豆角包 150 包、碗盖 1000 套
- **2 条预警**：牛腩 3 kg、青菜 6 kg 在需求日前到期，不参与净算
- 全部执行后：FG-001 入库 280 碗、FG-002 入库 120 碗，自动生成 23 张业务凭证 + 1 张期初建账凭证，借贷平衡

---

## 6. 生产订单生命周期

```text
PLANNED ──release──> RELEASED ──issue──> IN_PROCESS ──confirm──> CONFIRMED ──goods_receipt──> COMPLETED
```

| 动作 | 业务含义 | 关键校验 | 副作用 |
| --- | --- | --- | --- |
| `release` | 下达生产订单，按 FEFO 预留组件 | 只有 `PLANNED` 可下达；允许部分预留并返回缺口 | 写 `inventory_reservation`，`qty_reserved` 增加 |
| `issue` | 生产发料 | 必须已下达；组件不足整单回滚 | 按批次扣减库存，记录 `GOODS_ISSUE`，累计实际材料成本 |
| `confirm` | 报工 | 良品 + 废品 ≤ 计划量 | 记录良品/废品、人工成本，发 `PRODUCTION_CONFIRMED` |
| `goods_receipt` | 成品入库 | 必须已报工；可分次入库 | 生成成品批次（生产日期 + 保质期），发 `GOODS_RECEIPT` |

库存消耗顺序：**本订单预留 → 自由库存（FEFO，先到期先出）**。
任何一步失败都会回滚整个事务，不会出现“扣了料但订单没更新”的脏数据。

---

## 7. REST API

启动：`python -m pp.api --db data/yungu_pp.db --port 8000 --seed`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| GET | `/api/materials?type=FG` | 物料列表 |
| GET | `/api/materials/{id}` | 物料详情（含库存、BOM 标记、工艺路线） |
| GET | `/api/materials/{id}/bom` | **按产品 ID 查 BOM 配方**（也可供 MM 组使用） |
| GET | `/api/materials/{id}/cost` | 标准成本与卷算成本 |
| GET | `/api/requisitions?status=SUBMITTED` | 门店要货单 |
| POST | `/api/requisitions` | 新建要货单（SD 模拟） |
| POST | `/api/mrp/run` | 运行 MRP |
| GET | `/api/mrp/runs` | MRP 运行历史 |
| GET | `/api/mrp/runs/{run_id}` | 运行结果（展开、异常、订单、采购申请） |
| GET | `/api/production-orders?run_id=&status=` | 生产订单列表 |
| GET | `/api/production-orders/{id}` | 订单详情（组件、工序、报工） |
| POST | `/api/production-orders/{id}/release` | 下达 |
| POST | `/api/production-orders/{id}/issue` | 发料 |
| POST | `/api/production-orders/{id}/confirm` | 报工 |
| POST | `/api/production-orders/{id}/goods-receipt` | 成品入库 |
| GET | `/api/purchase-requisitions?run_id=` | 采购申请 |
| GET | `/api/inventory` | 批次库存 |
| GET | `/api/events?status=PENDING` | 集成事件 |
| POST | `/api/fi/post` | FI 占位：消费事件生成凭证 |
| GET | `/api/fi/vouchers` | 凭证与科目发生额 |
| POST | `/api/demo/mm-receive` | MM 占位：采购申请收货 |

### 7.1 示例：查询 BOM

```bash
curl http://127.0.0.1:8000/api/materials/FG-001/bom
```

```json
{
  "product_id": "FG-001",
  "product_name": "招牌牛肉米粉",
  "bom_id": "BOM-FG-001",
  "version": "V1",
  "base_qty": 1.0,
  "components": [
    {"line_no": 10, "component_id": "SF-001", "component_name": "鲜湿米粉", "qty_per_base": 0.25, "scrap_rate": 0.02, "qty_per_unit": 0.255}
  ]
}
```

### 7.2 示例：运行 MRP

```bash
curl -X POST http://127.0.0.1:8000/api/mrp/run \
  -H "Content-Type: application/json" \
  -d "{\"run_date\":\"2026-10-23\",\"horizon_days\":30}"
```

### 7.3 示例：完整生产执行

```bash
RUN_ID=MRP-20261023-0001
curl -X POST http://127.0.0.1:8000/api/demo/mm-receive \
  -H "Content-Type: application/json" -d "{\"run_id\":\"$RUN_ID\",\"on_date\":\"2026-10-25\"}"

curl -X POST http://127.0.0.1:8000/api/production-orders/PO-20261023-0003/release \
  -H "Content-Type: application/json" -d "{\"on_date\":\"2026-10-25\"}"
curl -X POST http://127.0.0.1:8000/api/production-orders/PO-20261023-0003/issue \
  -H "Content-Type: application/json" -d "{\"on_date\":\"2026-10-25\"}"
curl -X POST http://127.0.0.1:8000/api/production-orders/PO-20261023-0003/confirm \
  -H "Content-Type: application/json" -d "{\"qty_good\":95,\"on_date\":\"2026-10-25\"}"
curl -X POST http://127.0.0.1:8000/api/production-orders/PO-20261023-0003/goods-receipt \
  -H "Content-Type: application/json" -d "{\"production_date\":\"2026-10-25\",\"on_date\":\"2026-10-25\"}"

curl -X POST http://127.0.0.1:8000/api/fi/post
```

> API 只监听 `127.0.0.1`，没有鉴权，适合本地课堂演示；不要直接暴露到公网。

---

## 8. 与同组模块的接口

### 8.1 SD（刘浩焰）

- PP 读取 `store_requisition` 中 `status='SUBMITTED'` 的数据；
- 要货明细必须是成品（`FG`），数量 > 0，需求日 ≥ 要货日；
- PP 计算完成后把要货单置为 `PLANNED` 并回写 `mrp_run_id`；
- 成品入库后，SD 可从 `inventory_batch`/`inventory_movement` 看到可发货库存。

### 8.2 MM（兰永尚）

- PP 输出 `purchase_requisition`：物料、数量、单位、需求日、建议下单日、预估单价；
- MM 据此创建采购订单、收货；`stubs.receive_purchase_requisitions` 是可运行的占位示例；
- 收货批次必须带 `expiry_date`，否则 PP 无法做效期净算。

### 8.3 FI（FI 组）

PP 只发事件，不决定会计科目：

| 事件 | 触发 | 金额 | 文档中的凭证模板 |
| --- | --- | --- | --- |
| `GOODS_ISSUE` | 生产发料 | 实际材料成本 | 借：生产成本 5001 / 贷：原材料 1403 |
| `PRODUCTION_CONFIRMED` | 报工 | 人工成本 | 借：生产成本 5001 / 贷：应付职工薪酬 2211 |
| `GOODS_RECEIPT` | 成品入库 | 实际总成本 | 借：库存商品 1405 / 贷：生产成本 5001 |
| `PURCHASE_REQUISITION_CREATED` | 生成采购申请 | 预估金额 | 不记账（供 MM 使用） |

`pp/stubs.py` 里的 FI 占位按上表自动生成凭证，用于演示“业务发生 → 自动记账”的闭环；正式科目映射、期间、税码由 FI 组维护。

### 8.4 CO

- 生产订单记录 `actual_material_cost`、`actual_labor_cost`，成品批次记录实际单位成本；
- 演示输出每个成品的实际单位成本、售价与毛利率（FG-001 约 48.4%，FG-002 约 48.7%）；
- 可直接用于按门店/单品的毛利分析。

---

## 9. 演示与测试

### 9.1 一键演示

```bash
python run_demo.py
```

控制台会依次展示 11 个部分：主数据 → 要货单 → MRP → 展开净算 → 生产订单 → 采购申请 → 异常 → 跨模块执行 → FI 凭证 → 成品库存/毛利 → 关键物料结存。

### 9.2 自动化测试

```bash
python -m unittest discover -s tests -v
```

覆盖：

- BOM 查询、标准成本卷算；
- MRP 订单/采购申请数量、日期、批量、安全库存、效期；
- 多级 BOM 汇总（两张要货单合并成一张 95 kg 米粉订单）；
- 第二次 MRP 不重复计算；
- 生产订单完整生命周期；
- 库存不足整单回滚；
- 非法状态流转；
- REST API（健康检查、BOM、MRP、下达）。

### 9.3 手动验证 API

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/materials/FG-001/bom
curl "http://127.0.0.1:8000/api/production-orders?status=PLANNED"
```

---

## 10. 设计取舍与已知简化

这是课程 P0 版本，重点是“把 MRP 逻辑跑通、把表结构和模块边界讲清楚”。以下简化是刻意为之：

1. **单工厂、单仓网**：中央工厂 + 3 个门店，未实现多工厂调拨。
2. **产能模型简化**：按工作中心“每日可用分钟”正排，工序不跨天；没有节假日、班次、换型矩阵。
3. **批量策略**：最小起订量 + 固定批量倍数，未实现 FOQ/POQ/期间批量。
4. **成本口径**：材料按标准成本，人工按工艺路线工时 × 费率；未分摊制造费用、未做差异分析。
5. **MRP 不冲销已有供应**：只计算 `SUBMITTED` 要货单，已生成的订单/采购申请不会在同一轮重复生成；跨轮重排需要先取消旧单（可扩展）。
6. **MM/FI 是占位**：`stubs.py` 只实现“够演示闭环”的收货和凭证，不是完整采购/财务模块。
7. **无鉴权/无限流**：API 仅本地演示使用。
8. **语言选择**：课程若要求 ABAP/Java，可保留 `schema.sql` 与 MRP 算法，按相同表结构和状态机移植；本实现用 Python 是为了让算法和流程可读、可运行、可测试。

### 可扩展方向

- MRP 净算加入“已下达未完工生产订单 + 已批准未收货采购申请”作为预计入库；
- 按门店/产品做预测需求（Forecast）与安全库存动态计算；
- 排产加入班次日历、换型时间、设备停机；
- 生产订单部分发料/部分报工/反冲（Backflush）；
- 批次追溯（从成品批次反查原料批次）与临期自动促销；
- 用 FastAPI/PostgreSQL 替换标准库/SQLite，接入真实 POS 与供应商门户。

---

## 11. 课堂演示建议（5 分钟）

1. **先讲边界**：我负责 PP，上游是 SD 门店要货，下游是 MM 采购和 FI 记账；PP 的核心是“把要货变成生产订单和采购申请”。
2. **打开 `out/demo_report.txt`**：先看两张要货单（200 + 80 + 120 碗）。
3. **重点解释三个数字**：
   - 鲜湿米粉为什么是 95 kg：毛需求 102，扣 8 kg 库存，净 94，批量 5；
   - 牛腩为什么是 30 kg：3 kg 在需求日前过期，不能用于净算，还要补安全库存；
   - 碗盖为什么是 1000 套：净需求只有 4 套，但 MOQ 是 1000。
4. **展示 5 张生产订单 + 8 张采购申请**，说明多级 BOM 和“自制/外购”分流。
5. **跑一张订单的生命周期**：下达 → 发料 → 报工 → 入库，指出批次和有效期。
6. **最后看 FI 凭证**：发料 借生产成本/贷原材料，入库 借库存商品/贷生产成本，说明 PP 只发事件、FI 管科目。
7. 如果老师问“为什么不用 Excel”：库存扣减、批次效期、并发回滚、可追溯性，以及 API 可被 SD/MM 直接调用。

---

## 12. 提交清单

- [x] `pp/schema.sql`：完整表结构
- [x] `pp/mrp.py`：MRP 核心逻辑
- [x] `pp/service.py`：生产订单生命周期
- [x] `pp/scheduling.py`：有限产能排产
- [x] `pp/api.py`：REST API
- [x] `pp/seed.py`：演示数据（20 个物料、5 套 BOM、15 个期初批次）
- [x] `tests/test_pp.py`：17 个自动化用例
- [x] `提交说明.md`：姓名/模块/验收步骤
- [x] `out/MRP运行报告.md` + CSV：运行结果
- [x] `README.md`：设计说明与答辩要点

如课程要求提交 Word/PDF 版设计文档，可把本 README 的“数据表设计 / MRP 算法 / 接口”三节直接整理成文档；代码和报告中的数字均来自实际运行结果，可复现。
