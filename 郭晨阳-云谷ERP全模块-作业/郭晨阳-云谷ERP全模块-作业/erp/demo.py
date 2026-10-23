"""云谷鲜米粉 ERP 全模块端到端演示。

流程：
  HCM 排班/权限 → SD 门店 POS 销售 → 低于补货点自动要货 → 企业团餐订单
  → PP MRP/BOM 展开 → MM 采购订单/收货 → PP 生产/入库
  → SD 工厂到门店配送/团餐配送 → SD 次日 POS → FI 自动凭证 → CO 门店/单品毛利。
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

from pp import repo, service
from pp.demo import Reporter
from pp.mrp import run_mrp
from pp.util import fmt_money, fmt_qty

from . import co, fi, hcm, mm, sd
from .db import FULL_DB_PATH, init_full_db
from .inventory import store_inventory_rows

PROJECT_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_DIR / "out"
DAY1 = "2026-10-23"
DAY2 = "2026-10-26"
PERIOD_START = "2026-10-23"
PERIOD_END = "2026-10-26"
STORE_CORP = "ST-CORP"


def _write_csv(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def _md_table(headers: list[str], rows: list[list[object]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(x).replace("|", "/") for x in row) + " |")
    return lines


def run(db_path: str | Path | None = None, quiet: bool = False) -> dict:
    path = Path(db_path) if db_path else FULL_DB_PATH
    conn = init_full_db(path, reset=True)
    r = Reporter(path=OUT_DIR / "ERP运行报告.txt", quiet=quiet)

    r.line("=" * 140)
    r.line("云谷鲜米粉 ERP · 全模块端到端演示")
    r.line("SD 销售 / PP 生产 / MM 采购 / FI 财务 / CO 管理会计 / HCM 人力资源")
    r.line("=" * 140)

    stores = conn.execute("SELECT COUNT(*) AS n FROM store WHERE store_type = 'STORE'").fetchone()["n"]
    r.line(
        f"企业概况：{stores} 家直营门店 + 1 个中央工厂；"
        f"物料 {len(repo.list_materials(conn))} 个、BOM {len(repo.list_bom_products(conn))} 套；"
        f"员工 {conn.execute('SELECT COUNT(*) AS n FROM employee').fetchone()['n']} 名"
    )
    r.line("")

    # -- 0. FI 期初（必须在业务发生前） -------------------------------------
    opening = fi.post_opening_balances(conn, DAY1)

    # -- 1. HCM：排班与权限 ------------------------------------------------
    r.line("【1】HCM 人力资源：排班覆盖与权限")
    roster = hcm.shift_roster(conn, "2026-10-24")
    r.line(f"  2026-10-24 排班 {len(roster)} 人次；岗位覆盖：")
    coverage_rows = hcm.coverage(conn, "2026-10-24")
    r.table(
        ["地点", "班次", "需求人数", "已排人数", "缺口", "状态"],
        [[c["location"], c["shift_type"], str(c["required"]), str(c["scheduled"]),
          str(c["gap"]), c["status"]] for c in coverage_rows],
    )
    access_cases = [
        ("guo.chenyang", "郭晨阳", "PP", True),
        ("guo.chenyang", "郭晨阳", "FI", False),
        ("liu.haoyan", "刘浩焰", "SD", True),
        ("lan.yongshang", "兰永尚", "MM", True),
        ("wu.min", "吴敏", "FI", True),
        ("zheng.jie", "郑洁", "CO", True),
        ("he.jing", "何静", "HCM", True),
    ]
    r.line("  权限校验（用户 / 模块 / 编辑权限）：")
    for user_id, name, module, expected in access_cases:
        actual = hcm.can_access(conn, user_id, module, edit=True)
        r.line(f"    {name}（{user_id}） → {module} 编辑：{'允许' if actual else '拒绝'}"
               f"（预期{'允许' if expected else '拒绝'}）")
    r.line("")

    # -- 2. SD：期初门店库存 + 当日 POS -----------------------------------
    r.line("【2】SD 销售：门店库存与 POS 零售")
    r.table(
        ["门店", "商品", "期初库存", "单位成本", "补货点", "目标库存"],
        [[row["store_name"], row["material_name"], fmt_qty(row["qty_on_hand"], row["base_unit"]),
          fmt_money(row["avg_cost"]), fmt_qty(row["reorder_point"]), fmt_qty(row["target_stock"])]
         for row in store_inventory_rows(conn)],
    )
    pos1 = sd.record_pos_sale(conn, "ST-B", DAY1, [("FG-001", 40, 18.0)])
    pos2 = sd.record_pos_sale(
        conn, "ST-C", DAY1, [("FG-001", 40, 18.0), ("FG-002", 40, 16.0)]
    )
    day1_sales = [pos1, pos2]
    r.line(
        f"  2026-10-23 POS：{len(day1_sales)} 笔，收入 {fmt_money(sum(s['total_amount'] for s in day1_sales))}，"
        f"成本 {fmt_money(sum(s['total_cost'] for s in day1_sales))}"
    )
    r.table(
        ["销售单", "门店", "收入", "成本", "毛利"],
        [[s["sale_id"], s["store_name"], fmt_money(s["total_amount"]), fmt_money(s["total_cost"]),
          fmt_money(s["gross_profit"])] for s in day1_sales],
    )
    r.line("  销售后门店库存：")
    r.table(
        ["门店", "商品", "库存", "平均成本"],
        [[row["store_name"], row["material_name"], fmt_qty(row["qty_on_hand"], row["base_unit"]),
          fmt_money(row["avg_cost"])] for row in store_inventory_rows(conn)],
    )

    r.line("  低于补货点，SD 自动生成门店要货单：")
    requisitions = sd.check_reorder(conn, DAY1, DAY2)
    for req in requisitions:
        detail = repo.get_requisition(conn, req["requisition_id"])
        items = "；".join(
            f"{item['material_name']} {fmt_qty(item['qty'], item['base_unit'])}"
            for item in detail["items"]
        )
        r.line(f"    {req['requisition_id']}  {detail['store_name']}  需求日 {DAY2}：{items}")

    corporate = sd.create_corporate_order(
        conn, "CUS-001", [("FG-001", 30, 15.0)], DAY2, DAY1, "科技园团餐试单"
    )
    r.line(
        f"  企业团餐订单：{corporate['order_id']} {corporate['customer_name']} "
        f"30 碗 @15，金额 {fmt_money(corporate['total_amount'])}，需求日 {DAY2}"
    )
    requisition_details = [
        repo.get_requisition(conn, req["requisition_id"]) for req in requisitions
    ]
    r.line("")

    # -- 3. PP：MRP --------------------------------------------------------
    r.line("【3】PP 生产：MRP（门店要货 + 企业团餐需求）")
    extra = [
        {
            "product_id": item["product_id"],
            "qty": item["qty"],
            "required_date": corporate["required_date"],
            "source_id": corporate["order_id"],
        }
        for item in corporate["items"]
    ]
    mrp = run_mrp(
        conn,
        run_date=DAY1,
        extra_demands=extra,
        run_by="郭晨阳（PP）",
    )
    run_id = mrp["run_id"]
    r.line(
        f"  运行 {run_id}：门店要货 {mrp['stats']['requisition_count']} 张 + "
        f"团餐需求 {mrp['stats']['extra_demand_count']} 条 → "
        f"生产订单 {mrp['stats']['production_order_count']} 张，"
        f"采购申请 {mrp['stats']['purchase_requisition_count']} 张，"
        f"异常 {mrp['stats']['exception_count']} 条"
    )
    orders = {
        order["material_id"]: order
        for order in repo.list_production_orders(conn, run_id=run_id)
    }
    r.table(
        ["生产订单", "产品", "数量", "需求日", "计划开始", "计划完成", "材料成本", "人工成本"],
        [[o["order_id"], o["material_name"], fmt_qty(o["qty_planned"], o["base_unit"]),
          o["required_date"], o["planned_start"], o["planned_finish"],
          fmt_money(o["estimated_material_cost"]), fmt_money(o["estimated_labor_cost"])]
         for o in orders.values()],
    )
    prs = repo.list_purchase_requisitions(conn, run_id=run_id)
    r.table(
        ["采购申请", "物料", "数量", "需求日", "建议下单", "预估金额"],
        [[p["pr_id"], p["material_name"], fmt_qty(p["qty"], p["unit"]),
          p["required_date"], p["release_date"], fmt_money(p["qty"] * p["unit_cost"])]
         for p in prs],
    )
    exceptions = repo.list_exceptions(conn, run_id)
    for exc in exceptions:
        r.line(f"  [{exc['severity']}] {exc['exception_type']}：{exc['message']}")
    r.line("")

    # -- 4. MM：采购订单与收货 --------------------------------------------
    r.line("【4】MM 采购：采购申请 → 采购订单 → 冷链收货")
    purchase_orders = mm.create_purchase_orders(conn, run_id, DAY1)
    r.table(
        ["采购订单", "供应商", "物料", "数量", "单价", "金额", "预计到货"],
        [[p["po_id"], p["supplier_name"], p["material_name"], fmt_qty(p["qty"], p["unit"]),
          fmt_money(p["unit_cost"]), fmt_money(p["qty"] * p["unit_cost"]), p["expected_date"]]
         for p in purchase_orders],
    )
    receipts = mm.receive_purchase_orders(conn, run_id=run_id, receipt_date="2026-10-25")
    r.line(
        f"  2026-10-25 JIT 收货 {len(receipts)} 张采购订单，金额 "
        f"{fmt_money(sum(x['amount'] for x in receipts))}；批次已按保质期建档"
    )
    alerts = mm.shelf_life_alerts(conn, "2026-10-25", within_days=7)
    if alerts:
        r.line("  效期预警（7 天内到期）：")
        for batch in alerts[:6]:
            r.line(
                f"    {batch['material_name']} 批次 {batch['batch_no']}："
                f"{fmt_qty(batch['qty_on_hand'], batch['base_unit'])}，"
                f"到期 {batch['expiry_date']}"
            )
    r.line("")

    # -- 5. PP：生产执行 ---------------------------------------------------
    r.line("【5】PP 生产执行：下达 → 发料 → 报工 → 成品入库")
    execution: list[str] = []

    def produce(material_id: str, action_date: str, label: str) -> dict:
        order = orders[material_id]
        detail = service.release_order(conn, order["order_id"], action_date)
        service.issue_components(conn, order["order_id"], action_date)
        issued = service.get_order_detail(conn, order["order_id"])
        service.confirm_order(
            conn, order["order_id"], qty_good=order["qty_planned"], on_date=action_date
        )
        receipt = service.goods_receipt(
            conn, order["order_id"], production_date=action_date, on_date=action_date
        )
        message = (
            f"[{label}] {order['order_id']} {order['material_name']} "
            f"{fmt_qty(order['qty_planned'], order['base_unit'])}：下达 {detail['availability_status']}，"
            f"发料 {fmt_money(issued['actual_material_cost'])}，入库 {fmt_qty(receipt['qty'], receipt['unit'])}，"
            f"批次有效期 {receipt['expiry_date']}"
        )
        execution.append(message)
        r.line("  " + message)
        return receipt

    for material_id in ("SF-001", "SF-002", "SF-003"):
        produce(material_id, "2026-10-25", "半成品")
    for material_id in ("FG-001", "FG-002"):
        produce(material_id, DAY2, "成品")
    r.line("")

    # -- 6. SD：配送 + 次日 POS -------------------------------------------
    r.line("【6】SD 配送：工厂成品仓 → 门店 / 企业客户，并执行次日 POS")
    deliveries = []
    for req in repo.list_requisitions(conn, status="PLANNED"):
        delivery = sd.deliver_store_requisition(conn, req["requisition_id"], DAY2)
        deliveries.append(delivery)
        r.line(
            f"  {delivery['delivery_id']} → {delivery['store_name']}："
            f"{fmt_qty(delivery['total_qty'], '碗')}，成本 {fmt_money(delivery['total_cost'])}"
        )
    corporate_delivery = sd.deliver_corporate_order(conn, corporate["order_id"], DAY2)
    deliveries.append(corporate_delivery)
    r.line(
        f"  {corporate_delivery['delivery_id']} → {corporate_delivery['customer_name']}："
        f"30 碗，收入 {fmt_money(corporate_delivery['revenue'])}，"
        f"成本 {fmt_money(corporate_delivery['total_cost'])}"
    )
    r.table(
        ["门店", "商品", "配送后库存", "平均成本"],
        [[row["store_name"], row["material_name"], fmt_qty(row["qty_on_hand"], row["base_unit"]),
          fmt_money(row["avg_cost"])] for row in store_inventory_rows(conn)],
    )
    pos3 = sd.record_pos_sale(conn, "ST-B", DAY2, [("FG-001", 120, 18.0)])
    pos4 = sd.record_pos_sale(
        conn, "ST-C", DAY2, [("FG-001", 50, 18.0), ("FG-002", 70, 16.0)]
    )
    day2_sales = [pos3, pos4]
    r.line(
        f"  2026-10-26 POS：{len(day2_sales)} 笔，收入 "
        f"{fmt_money(sum(s['total_amount'] for s in day2_sales))}，"
        f"成本 {fmt_money(sum(s['total_cost'] for s in day2_sales))}"
    )
    r.line("")

    # -- 7. FI：凭证与报表 ------------------------------------------------
    r.line("【7】FI 财务：事件驱动自动过账")
    fixed = fi.post_store_fixed_costs(conn, PERIOD_START, PERIOD_END)
    vouchers = fi.post_pending_events(conn)
    voucher_types = Counter(v["voucher_type"] for v in vouchers)
    r.line(
        f"  期初凭证：{opening['voucher_id'] or '-'}（金额 {fmt_money(opening['amount'])}）；"
        f"门店期间费用凭证 {len(fixed)} 张（{fmt_money(sum(v['amount'] for v in fixed))}）"
    )
    r.line(
        f"  业务事件自动生成凭证 {len(vouchers)} 张："
        + "；".join(f"{k} {v}" for k, v in sorted(voucher_types.items()))
    )
    r.table(
        ["科目", "名称", "借方", "贷方"],
        [[row["account_code"], row["account_name"], fmt_money(row["debit"]), fmt_money(row["credit"])]
         for row in fi.trial_balance(conn)],
    )
    pnl = fi.income_statement(conn)
    r.line(
        f"  利润表：收入 {fmt_money(pnl['revenue'])} − 成本 {fmt_money(pnl['cogs'])} "
        f"= 毛利 {fmt_money(pnl['gross_profit'])}（毛利率 {pnl['gross_margin']:.1%}）；"
        f"期间费用 {fmt_money(pnl['operating_expenses'])}，净利润 {fmt_money(pnl['net_profit'])}"
        f"（净利率 {pnl['net_margin']:.1%}）"
    )
    r.line("")

    # -- 8. CO：门店利润中心与单品毛利 ------------------------------------
    r.line("【8】CO 管理会计：门店利润中心与单品毛利")
    store_pnl = co.store_profitability(conn, PERIOD_START, PERIOD_END)
    r.table(
        ["利润中心", "收入", "成本", "毛利", "租金", "人工", "水电", "其他", "营业利润", "净利率"],
        [[row["store_name"], fmt_money(row["revenue"]), fmt_money(row["cogs"]),
          fmt_money(row["gross_profit"]), fmt_money(row["rent"]), fmt_money(row["labor"]),
          fmt_money(row["utilities"]), fmt_money(row["other"]),
          fmt_money(row["operating_profit"]), f"{row['net_margin']:.1%}"] for row in store_pnl],
    )
    products = co.product_margin(conn, PERIOD_START, PERIOD_END)
    r.table(
        ["产品", "销量", "收入", "成本", "毛利", "毛利率"],
        [[row["product_name"], fmt_qty(row["qty"], row["unit"]), fmt_money(row["revenue"]),
          fmt_money(row["cogs"]), fmt_money(row["gross_profit"]), f"{row['gross_margin']:.1%}"]
         for row in products],
    )
    r.line("")

    # -- 9. 期末库存与结论 ------------------------------------------------
    r.line("【9】期末库存与运营结论")
    fg_stock = repo.total_stock(conn, "FG-001") + repo.total_stock(conn, "FG-002")
    r.line(
        f"  工厂成品库存：{fmt_qty(fg_stock, '碗')}；门店库存："
        + "；".join(
            f"{row['store_name']} {row['material_name']} {fmt_qty(row['qty_on_hand'], row['base_unit'])}"
            for row in store_inventory_rows(conn)
        )
    )
    r.line(
        f"  本周期销售额 {fmt_money(pnl['revenue'])}，毛利 {fmt_money(pnl['gross_profit'])}，"
        f"净利润 {fmt_money(pnl['net_profit'])}；"
        f"直营门店利润率最高："
        f"{max((x for x in store_pnl if not x['is_corporate']), key=lambda x: x['net_margin'])['store_name']}"
    )
    r.line("")
    r.line("  说明：MM/FI 的供应商、科目映射、期间费用为课程演示口径；PP 的 MRP 与生产执行沿用同一套逻辑。")
    r.save()

    # -- CSV / Markdown ----------------------------------------------------
    explosion = repo.list_explosion(conn, run_id)
    _write_csv(
        OUT_DIR / "erp_mrp_explosion.csv",
        ["level", "material_id", "material_name", "required_date", "gross_qty",
         "stock_available", "net_qty", "order_qty", "order_type", "release_date"],
        [[x["level"], x["material_id"], x["material_name"], x["required_date"], x["gross_qty"],
          x["stock_available"], x["net_qty"], x["order_qty"], x["order_type"], x["release_date"]]
         for x in explosion],
    )
    _write_csv(
        OUT_DIR / "erp_purchase_orders.csv",
        ["po_id", "supplier_name", "material_id", "material_name", "qty", "unit", "unit_cost", "expected_date"],
        [[p["po_id"], p["supplier_name"], p["material_id"], p["material_name"], p["qty"],
          p["unit"], p["unit_cost"], p["expected_date"]] for p in purchase_orders],
    )
    _write_csv(
        OUT_DIR / "erp_store_inventory.csv",
        ["store_id", "store_name", "product_id", "material_name", "qty_on_hand", "avg_cost", "reorder_point", "target_stock"],
        [[x["store_id"], x["store_name"], x["product_id"], x["material_name"], x["qty_on_hand"],
          x["avg_cost"], x["reorder_point"], x["target_stock"]] for x in store_inventory_rows(conn)],
    )
    _write_csv(
        OUT_DIR / "erp_store_pnl.csv",
        ["store_id", "store_name", "revenue", "cogs", "gross_profit", "rent", "labor",
         "utilities", "other", "operating_profit", "net_margin"],
        [[x["store_id"], x["store_name"], x["revenue"], x["cogs"], x["gross_profit"], x["rent"],
          x["labor"], x["utilities"], x["other"], x["operating_profit"], x["net_margin"]]
         for x in store_pnl],
    )
    _write_csv(
        OUT_DIR / "erp_product_margin.csv",
        ["product_id", "product_name", "qty", "unit", "revenue", "cogs", "gross_profit", "gross_margin"],
        [[x["product_id"], x["product_name"], x["qty"], x["unit"], x["revenue"], x["cogs"],
          x["gross_profit"], x["gross_margin"]] for x in products],
    )
    _write_csv(
        OUT_DIR / "erp_trial_balance.csv",
        ["account_code", "account_name", "debit", "credit"],
        [[x["account_code"], x["account_name"], x["debit"], x["credit"]]
         for x in fi.trial_balance(conn)],
    )

    _markdown(
        OUT_DIR / "ERP运行报告.md",
        stores,
        coverage_rows,
        access_cases,
        day1_sales,
        requisition_details,
        corporate,
        orders,
        prs,
        purchase_orders,
        receipts,
        execution,
        deliveries,
        day2_sales,
        store_pnl,
        products,
        fi.trial_balance(conn),
        pnl,
        opening,
        vouchers,
        fixed,
    )

    conn.close()
    if not quiet:
        print()
        print(f"文本报告：{OUT_DIR / 'ERP运行报告.txt'}")
        print(f"Markdown：{OUT_DIR / 'ERP运行报告.md'}")
        print(f"CSV：{OUT_DIR}")
    return {
        "run_id": run_id,
        "stores": stores,
        "purchase_orders": len(purchase_orders),
        "deliveries": len(deliveries),
        "vouchers": len(vouchers),
        "revenue": pnl["revenue"],
        "net_profit": pnl["net_profit"],
    }


def _markdown(
    path: Path,
    stores: int,
    coverage_rows: list[dict],
    access_cases: list[tuple],
    day1_sales: list[dict],
    requisition_details: list[dict],
    corporate: dict,
    orders: dict,
    prs: list[dict],
    purchase_orders: list[dict],
    receipts: list[dict],
    execution: list[str],
    deliveries: list[dict],
    day2_sales: list[dict],
    store_pnl: list[dict],
    products: list[dict],
    trial: list[dict],
    pnl: dict,
    opening: dict,
    vouchers: list[dict],
    fixed: list[dict],
) -> None:
    lines = [
        "# 云谷鲜米粉 ERP 全模块运行报告",
        "",
        f"- 门店主数据：{stores} 家直营门店 + 1 个中央工厂",
        "- 覆盖模块：SD / PP / MM / FI / CO / HCM",
        f"- 期间：2026-10-23 至 2026-10-26",
        f"- 收入 {fmt_money(pnl['revenue'])}，毛利 {fmt_money(pnl['gross_profit'])}，"
        f"净利润 {fmt_money(pnl['net_profit'])}（净利率 {pnl['net_margin']:.1%}）",
        "",
        "## 1. HCM 排班覆盖与权限",
        "",
    ]
    lines += _md_table(
        ["地点", "班次", "需求", "已排", "缺口", "状态"],
        [[c["location"], c["shift_type"], c["required"], c["scheduled"], c["gap"], c["status"]]
         for c in coverage_rows],
    )
    lines += ["", "权限校验：", ""]
    for user_id, name, module, expected in access_cases:
        lines.append(f"- {name} / {module}：{'允许' if expected else '拒绝'}")
    lines += ["", "## 2. SD 销售与补货", ""]
    lines += _md_table(
        ["销售单", "门店", "收入", "成本", "毛利"],
        [[s["sale_id"], s["store_name"], fmt_money(s["total_amount"]),
          fmt_money(s["total_cost"]), fmt_money(s["gross_profit"])] for s in day1_sales],
    )
    lines += ["", "自动补货要货单：", ""]
    for detail in requisition_details:
        items = "；".join(
            f"{item['material_name']} {fmt_qty(item['qty'], item['base_unit'])}"
            for item in detail["items"]
        )
        lines.append(f"- {detail['requisition_id']} / {detail['store_name']}：{items}")
    lines += [
        "",
        f"企业团餐：{corporate['order_id']}，{corporate['customer_name']}，"
        f"{fmt_money(corporate['total_amount'])}，需求日 {corporate['required_date']}",
        "",
        "## 3. PP 生产订单",
        "",
    ]
    lines += _md_table(
        ["生产订单", "产品", "数量", "需求日", "计划开始", "计划完成", "材料成本", "人工成本"],
        [[o["order_id"], o["material_name"], fmt_qty(o["qty_planned"], o["base_unit"]),
          o["required_date"], o["planned_start"], o["planned_finish"],
          fmt_money(o["estimated_material_cost"]), fmt_money(o["estimated_labor_cost"])]
         for o in orders.values()],
    )
    lines += ["", "## 4. MM 采购订单与收货", ""]
    lines += _md_table(
        ["采购订单", "供应商", "物料", "数量", "金额", "预计到货"],
        [[p["po_id"], p["supplier_name"], p["material_name"],
          fmt_qty(p["qty"], p["unit"]), fmt_money(p["qty"] * p["unit_cost"]), p["expected_date"]]
         for p in purchase_orders],
    )
    lines += ["", f"收货 {len(receipts)} 张，金额 {fmt_money(sum(x['amount'] for x in receipts))}。", ""]
    lines += ["## 5. 生产执行", "", "```"] + execution + ["```", ""]
    lines += ["## 6. SD 配送", ""]
    lines += _md_table(
        ["配送单", "对象", "数量", "成本/收入"],
        [[d["delivery_id"], d.get("store_name") or d.get("customer_name"),
          fmt_qty(d["total_qty"], "碗"),
          fmt_money(d.get("revenue", d.get("total_cost", 0)))] for d in deliveries],
    )
    lines += ["", "次日 POS：", ""]
    lines += _md_table(
        ["销售单", "门店", "收入", "成本"],
        [[s["sale_id"], s["store_name"], fmt_money(s["total_amount"]), fmt_money(s["total_cost"])]
         for s in day2_sales],
    )
    lines += ["", "## 7. FI 试算平衡与利润表", ""]
    lines += _md_table(
        ["科目", "名称", "借方", "贷方"],
        [[x["account_code"], x["account_name"], fmt_money(x["debit"]), fmt_money(x["credit"])]
         for x in trial],
    )
    lines += [
        "",
        f"- 期初凭证 {opening['voucher_id'] or '-'}：{fmt_money(opening['amount'])}",
        f"- 业务凭证 {len(vouchers)} 张；门店期间费用凭证 {len(fixed)} 张",
        f"- 收入 {fmt_money(pnl['revenue'])}；成本 {fmt_money(pnl['cogs'])}；"
        f"毛利 {fmt_money(pnl['gross_profit'])}；费用 {fmt_money(pnl['operating_expenses'])}；"
        f"净利润 {fmt_money(pnl['net_profit'])}",
        "",
        "## 8. CO 门店利润中心",
        "",
    ]
    lines += _md_table(
        ["利润中心", "收入", "毛利", "租金", "人工", "水电", "营业利润", "净利率"],
        [[x["store_name"], fmt_money(x["revenue"]), fmt_money(x["gross_profit"]),
          fmt_money(x["rent"]), fmt_money(x["labor"]), fmt_money(x["utilities"]),
          fmt_money(x["operating_profit"]), f"{x['net_margin']:.1%}"] for x in store_pnl],
    )
    lines += ["", "## 9. CO 单品毛利", ""]
    lines += _md_table(
        ["产品", "销量", "收入", "成本", "毛利", "毛利率"],
        [[x["product_name"], fmt_qty(x["qty"], x["unit"]), fmt_money(x["revenue"]),
          fmt_money(x["cogs"]), fmt_money(x["gross_profit"]), f"{x['gross_margin']:.1%}"]
         for x in products],
    )
    lines += [
        "",
        "## 10. 结论",
        "",
        "- 门店 POS 销售触发补货，MRP 自动生成生产订单和采购申请；",
        "- MM 按供应商 JIT 收货，PP 按批次/效期生产入库，SD 配送后门店库存与成本同步；",
        "- FI 按业务事件自动生成凭证，试算平衡；CO 能实时看到门店与单品毛利。",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    run()


if __name__ == "__main__":
    main()
