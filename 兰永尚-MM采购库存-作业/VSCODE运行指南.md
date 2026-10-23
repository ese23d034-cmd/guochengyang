# 在 VS Code 中运行 MM 模块

适用环境：VS Code 1.142+、Python 3.10+（当前机器 3.13.14）。

---

## 1. 打开项目

VS Code → `File → Open Folder…` → 选择：

```text
C:\Users\19379\OneDrive\图片\文档\deepseek-harness\default-workspace\yungu-mm-module
```

左侧应看到 `run_demo.py`、`mm`、`tests`、`.vscode`。

---

## 2. 选择解释器

`Ctrl + Shift + P` → `Python: Select Interpreter` → 选择 Python 3.13（或 3.10+）。

项目零第三方依赖，不需要 `pip install`。

---

## 3. 运行演示

按 `F5`，选择：

```text
MM：运行完整演示
```

或终端：

```powershell
py -3 run_demo.py
```

输出覆盖：主数据/BOM → 采购申请 → 采购订单 → 收货 → 效期预警 → FEFO 发料 → 报损 → 库存。

运行后生成：

```text
out/MM运行报告.txt
out/MM运行报告.md
out/mm_purchase_orders.csv
out/mm_receipts.csv
out/mm_inventory.csv
out/mm_shelf_life.csv
out/mm_goods_issues.csv
out/mm_events.csv
```

---

## 4. 运行测试

`F5` → `MM：运行全部测试`，或：

```powershell
py -3 -m unittest discover -s tests -v
```

预期：**6 个测试 OK**。

---

## 5. 启动 MM API

`F5` → `MM：启动 REST API`，打开：

```text
http://127.0.0.1:8100/health
```

常用接口：

```text
GET  /api/materials/FG-001/bom
GET  /api/purchase-requisitions
POST /api/purchase-orders
POST /api/receipts
GET  /api/inventory
GET  /api/shelf-life?as_of=2026-10-25&days=7
POST /api/goods-issues
GET  /api/events
```

停止：顶部红色停止按钮或 `Ctrl + C`。

---

## 6. 断点调试

1. 打开 `mm/inventory.py`；
2. 在 `consume_fefo` 的 `if not batch.available_on(posting):` 行号左侧点红点；
3. `F5` 运行 `MM：运行完整演示`；
4. 断点处查看 `batch`、`posting`、`remaining`，可以看到过期批次被跳过；
5. `F10` 单步、`F5` 继续、`Shift + F5` 停止。

---

## 7. 常见问题

### Q1：`ModuleNotFoundError: No module named 'mm'`

VS Code 打开错目录，或终端不在项目根目录。确认根目录有 `mm`、`tests`、`run_demo.py`。

### Q2：F5 报 `debugpy is not supported`

更新 VS Code Python 扩展；或将 `.vscode/launch.json` 的 `"type": "debugpy"` 改为 `"type": "python"`。

### Q3：中文乱码

```powershell
chcp 65001
$env:PYTHONUTF8=1
py -3 run_demo.py
```

### Q4：端口 8100 被占用

```powershell
py -3 -m mm.api --db data/yungu_mm.db --port 8101
```

### Q5：清空数据重新演示

```powershell
py -3 -m mm.seed --db data/yungu_mm.db --reset
```

---

## 8. 课堂演示顺序

1. 看 BOM：FG-001 拆到米粉、牛肉臊子、青菜、碗盖；
2. 看 8 张采购申请；
3. 看 8 张采购订单按供应商拆单；
4. 看 JIT 收货批次与到期日；
5. 看效期预警/过期批次；
6. 看牛腩 FEFO 发料跳过过期批次；
7. 看报损 234 元和期末库存 4,211.65 元；
8. 打开 BOM 查询 API。
