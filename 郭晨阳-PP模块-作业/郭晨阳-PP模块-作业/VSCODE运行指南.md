# 在 VS Code 中运行 PP 模块

本指南按你当前电脑的环境编写：

- VS Code：`1.142.0`，已安装 Python 扩展（`ms-python.python`、`ms-python.debugpy`）
- Python：`3.13.14`（`py -3` 可用）
- 项目目录：`C:\Users\19379\OneDrive\图片\文档\deepseek-harness\default-workspace\yungu-pp-module`

项目已经带好 `.vscode/launch.json`、`.vscode/tasks.json`，按下面的步骤可以直接 F5 运行。

---

## 1. 用 VS Code 打开正确的文件夹

打开 VS Code，选择：

```text
File → Open Folder…
```

粘贴并选择：

```text
C:\Users\19379\OneDrive\图片\文档\deepseek-harness\default-workspace\yungu-pp-module
```

> 一定要打开 **`yungu-pp-module` 这一层**，不要打开它的上一层，也不要只打开 `pp` 文件夹。
> 判断标准：左侧资源管理器里能同时看到 `run_demo.py`、`pp`、`tests`、`README.md`。

如果习惯用命令行，也可以：

```powershell
cd "C:\Users\19379\OneDrive\图片\文档\deepseek-harness\default-workspace\yungu-pp-module"
code .
```

> 你的 `code` 命令在 `D:\visual studio\Microsoft VS Code\bin\code.cmd`。如果提示找不到 `code`，把
> `D:\visual studio\Microsoft VS Code\bin` 加到 PATH，或直接用 VS Code 菜单打开文件夹。

---

## 2. 选择 Python 解释器（只需一次）

1. 按 `Ctrl + Shift + P`；
2. 输入并选择 `Python: Select Interpreter`；
3. 选择 `Python 3.13.14`（带 Store 字样也可以），路径大致是：

```text
C:\Users\19379\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe
```

4. VS Code 右下角状态栏应显示 `Python 3.13.14`。

项目没有第三方依赖，不需要 `pip install`。

---

## 3. 运行完整演示（最常用）

### 方式 A：按 F5（推荐）

1. 按 `F5`；
2. 在顶部下拉框选择 **`PP：运行完整演示`**；
3. 集成终端会输出 11 个部分：主数据 → 要货单 → MRP → 生产订单 → 采购申请 → 异常 → 执行 → 凭证 → 库存/毛利。

### 方式 B：直接运行 `run_demo.py`

1. 在左侧打开 `run_demo.py`；
2. 点右上角的 ▶（Run Python File）；
3. 或在编辑器里右键 → `Run Python File in Terminal`。

### 方式 C：终端命令

在 VS Code 的集成终端（`` Ctrl + ` ``）里执行：

```powershell
py -3 run_demo.py
```

运行完成后会生成：

```text
out/demo_report.txt
out/MRP运行报告.md
out/mrp_explosion.csv
out/production_orders.csv
out/purchase_requisitions.csv
data/yungu_pp.db
```

在 VS Code 左侧直接点开 `out/MRP运行报告.md`，按 `Ctrl + Shift + V` 可预览。

---

## 4. 运行测试

### 方式 A：Testing 侧边栏

1. 点左侧活动栏的「烧杯」图标（Testing）；
2. 如果没看到测试，点 `Configure Python Tests` → 选 `unittest` → 目录选 `tests`；
3. 点 `Run All Tests`，应看到 **17 passed**。

项目 `.vscode/settings.json` 已经配置：

```json
"python.testing.unittestEnabled": true,
"python.testing.unittestArgs": ["-v", "-s", "./tests", "-p", "test_*.py"]
```

### 方式 B：F5 运行测试

按 `F5`，选择 **`PP：运行全部测试`**。

### 方式 C：终端

```powershell
py -3 -m unittest discover -s tests -v
```

---

## 5. 启动 REST API 并调试

1. 按 `F5`，选择 **`PP：启动 REST API`**；
2. 终端出现：

```text
PP API 已启动: http://127.0.0.1:8000
```

3. 浏览器打开：

```text
http://127.0.0.1:8000/health
```

4. 常用接口：

```text
GET  http://127.0.0.1:8000/api/materials/FG-001/bom
GET  http://127.0.0.1:8000/api/production-orders
GET  http://127.0.0.1:8000/api/mrp/runs
POST http://127.0.0.1:8000/api/mrp/run
```

5. 停止：点 VS Code 顶部的红色停止按钮，或在终端按 `Ctrl + C`。

> API 启动配置没有加 `--seed`，所以不会覆盖已有数据库；如果 `data/yungu_pp.db` 不存在，程序会自动建库并写入演示数据。

---

## 6. 打断点调试 MRP（答辩/排错用）

1. 打开 `pp/mrp.py`；
2. 在 `_plan_material` 里找一行，例如：

```python
net_qty = round_qty(max(0.0, gross - usable_stock))
```

在行号左侧点一下，出现红点（断点）；
3. 按 `F5` 选 **`PP：运行完整演示`**；
4. 程序会停在断点，左侧 `VARIABLES` 面板可以看：
   - `material_id`
   - `gross`
   - `usable_stock`
   - `net_qty`
   - `lots`（批次模拟的剩余库存）
5. 按 `F10` 单步、`F5` 继续、`Shift + F5` 停止。

也可以直接在 `pp/mrp.py` 的 `run` 方法、`pp/service.py` 的 `release_order` 等方法上打断点。

---

## 7. 用 VS Code 任务运行（不用记命令）

按 `Ctrl + Shift + P` → `Tasks: Run Task`，可选：

- `PP：运行完整演示`
- `PP：运行全部测试`
- `PP：初始化演示数据库`

`PP：运行完整演示` 已设为默认构建任务，按 `Ctrl + Shift + B` 可直接执行。

---

## 8. 常见问题

### Q1：`ModuleNotFoundError: No module named 'pp'`

原因：VS Code 打开的目录不对，或终端当前目录不在项目根目录。

解决：

1. 确认左侧根目录下有 `pp`、`tests`、`run_demo.py`；
2. 终端执行：

```powershell
cd "C:\Users\19379\OneDrive\图片\文档\deepseek-harness\default-workspace\yungu-pp-module"
py -3 run_demo.py
```

### Q2：按 F5 提示 “configured debug type 'debugpy' is not supported”

原因：Python 扩展未启用或版本太旧。

解决：

1. 扩展面板搜索 `Python`，确认 `ms-python.python` 已安装并启用；
2. 更新 Python 扩展；
3. 或把 `.vscode/launch.json` 里的 `"type": "debugpy"` 临时改成 `"type": "python"`（旧版扩展使用）。

### Q3：终端中文乱码

项目已在 `.vscode/settings.json` 和 `run_demo.py` 里设置 UTF-8。仍乱码时在终端执行：

```powershell
chcp 65001
$env:PYTHONUTF8=1
py -3 run_demo.py
```

### Q4：`py` 不是内部或外部命令

说明 Python 没装或没加到 PATH。你当前机器 `py -3` 是可用的；如果换了电脑，安装 Python 3.10+ 时勾选 **Add python.exe to PATH**。

### Q5：端口 8000 被占用

改 `.vscode/launch.json` 里 API 配置的 `--port`，或终端：

```powershell
py -3 -m pp.api --db data/yungu_pp.db --port 8001
```

查看占用：

```powershell
netstat -ano | findstr :8000
```

### Q6：想清空数据重新演示

```powershell
py -3 -m pp.seed --db data/yungu_pp.db --reset
```

或运行任务 `PP：初始化演示数据库`。

---

## 9. 课堂演示顺序（VS Code 版）

1. VS Code 打开项目，展示目录结构；
2. `F5` → `PP：运行完整演示`；
3. 终端里指出：2 张要货单、5 张生产订单、8 张采购申请；
4. 打开 `out/MRP运行报告.md` 预览，讲三个数字：米粉 95 kg、牛腩 30 kg、碗盖 1000 套；
5. 在 `pp/mrp.py` 打断点，单步演示 `net_qty` 的计算；
6. `F5` → `PP：启动 REST API`，浏览器打开 `/api/materials/FG-001/bom`；
7. 最后打开 `out/demo_report.txt` 看 FI 凭证和毛利。

---

## 10. 项目里与 VS Code 相关的文件

```text
.vscode/
├─ launch.json       # F5 配置：演示 / API / 测试 / 当前文件
├─ tasks.json        # Ctrl+Shift+B 任务
├─ settings.json     # UTF-8、unittest、PYTHONUTF8
└─ extensions.json   # 推荐安装 Python / Pylance
run_demo.py          # 演示入口
run_demo.bat         # Windows 双击运行（不经过 VS Code）
start_api.bat        # Windows 双击启动 API
```

如果按上面操作仍报错，把 VS Code 终端里的完整报错贴给我，我按你的实际环境继续排查。
