# 在 VS Code 中运行全模块 ERP

适用环境：

- VS Code 1.142+，已安装 Python / Pylance 扩展
- Python 3.10+（当前机器为 3.13.14）
- 项目目录：`...\yungu-erp`（包含 `run_full_demo.py`、`pp`、`erp`、`tests` 的那一层）

---

## 1. 打开项目

VS Code → `File → Open Folder…` → 选择：

```text
C:\Users\19379\OneDrive\图片\文档\deepseek-harness\default-workspace\yungu-erp
```

左侧应同时看到 `run_full_demo.py`、`pp`、`erp`、`tests`、`.vscode`。

---

## 2. 选择 Python 解释器

`Ctrl + Shift + P` → `Python: Select Interpreter` → 选择 `Python 3.13.14`（或任意 3.10+）。

项目零第三方依赖，不需要 `pip install`。

---

## 3. 运行全模块演示

按 `F5`，选择：

```text
ERP：运行全模块演示
```

或者在终端：

```powershell
py -3 run_full_demo.py
```

运行后会输出 HCM → SD → PP → MM → FI → CO 各段结果，并生成：

```text
out/ERP运行报告.txt
out/ERP运行报告.md
out/erp_mrp_explosion.csv
out/erp_purchase_orders.csv
out/erp_store_inventory.csv
out/erp_store_pnl.csv
out/erp_product_margin.csv
out/erp_trial_balance.csv
```

在 VS Code 中打开 `out/ERP运行报告.md`，按 `Ctrl + Shift + V` 预览。

---

## 4. 运行测试

`F5` → `ERP：运行全部测试`，或终端：

```powershell
py -3 -m unittest discover -s tests -v
```

预期：**23 个测试全部 OK**。

Testing 侧边栏也可以直接 `Run All Tests`（`.vscode/settings.json` 已启用 unittest）。

---

## 5. 启动全模块 API

`F5` → `ERP：启动全模块 API`，然后打开：

```text
http://127.0.0.1:8001/health
```

常用接口：

```text
GET  /api/sd/store-inventory
GET  /api/materials/FG-001/bom
GET  /api/pp/production-orders
GET  /api/mm/purchase-orders
GET  /api/fi/trial-balance
GET  /api/fi/income-statement
GET  /api/co/store-pnl
GET  /api/co/product-margin
GET  /api/hcm/coverage
GET  /api/hcm/access/guo.chenyang/PP?edit=1
```

停止：点击顶部红色停止按钮，或终端 `Ctrl + C`。

---

## 6. 断点调试 MRP

1. 打开 `pp/mrp.py`；
2. 在 `_plan_material` 的 `net_qty = ...` 行号左侧点红点；
3. `F5` 运行 `ERP：运行全模块演示`；
4. 程序停在断点后，在 `VARIABLES` 查看：
   - `material_id`
   - `gross`
   - `usable_stock`
   - `net_qty`
   - `lots`
5. `F10` 单步、`F5` 继续、`Shift + F5` 停止。

---

## 7. 常见问题

### Q1：`ModuleNotFoundError: No module named 'erp'` / `'pp'`

VS Code 打开错目录，或终端不在项目根目录。

确认根目录下有 `pp`、`erp`、`run_full_demo.py`，然后：

```powershell
cd "C:\Users\19379\OneDrive\图片\文档\deepseek-harness\default-workspace\yungu-erp"
py -3 run_full_demo.py
```

### Q2：F5 提示 `debugpy is not supported`

更新 VS Code Python 扩展；或将 `.vscode/launch.json` 里的 `"type": "debugpy"` 改为 `"type": "python"`（旧版扩展）。

### Q3：中文乱码

项目已在 `.vscode/settings.json` 和脚本中设置 UTF-8；仍乱码时：

```powershell
chcp 65001
$env:PYTHONUTF8=1
py -3 run_full_demo.py
```

### Q4：端口 8001 被占用

修改 `.vscode/launch.json` 的 `--port`，或：

```powershell
py -3 -m erp.api --db data/yungu_erp.db --port 8002
```

### Q5：想清空数据重新演示

```powershell
py -3 -m erp.seed
```

或运行任务 `ERP：初始化全模块数据库`。

---

## 8. 课堂演示顺序

1. `F5` 运行 `ERP：运行全模块演示`；
2. 先看 HCM：42 店、排班覆盖、郭晨阳 PP 权限；
3. 看 SD：POS 卖出 40 碗 → 库存 200 < 220 → 自动要货 200 碗；
4. 看 PP：门店 + 团餐 → 310 碗 → 5 张生产订单、8 张采购申请；
5. 看 MM：8 张 PO 按供应商拆单、JIT 收货；
6. 看生产/配送：半成品 10-25、成品 10-26、门店库存更新；
7. 看 FI：30 张业务凭证、试算平衡、净利润 595.23；
8. 看 CO：城西店 14.7%、滨江店 -2.5%、单品毛利 48.6%/50.6%；
9. 最后打断点讲 MRP 净需求。
