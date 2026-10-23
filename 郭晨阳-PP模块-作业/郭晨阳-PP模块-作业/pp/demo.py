"""端到端演示：门店要货 -> MRP -> 生产订单/采购申请 -> 排产 -> 发料/报工/入库 -> FI 凭证。

运行：
    python run_demo.py
输出：
    out/demo_report.txt
    out/MRP运行报告.md
    out/mrp_explosion.csv / production_orders.csv / purchase_requisitions.csv
"""
from __future__ import annotations

import csv
import sqlite3
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import repo, service, stubs
from .db import DEFAULT_DB_PATH, connect
from .mrp import run_mrp
from .seed import RUN_DATE, init_and_seed
from .util import fmt_money, fmt_qty, round_money

PROJECT_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_DIR / "out"

TYPE_LABEL = {"FG": "成品", "SF": "半成品", "RM": "原料", "PK": "包装"}
ORDER_TYPE_LABEL = {"PRODUCTION": "生产订单", "PURCHASE": "采购申请", "NONE": "无需下单"}


def disp_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - disp_width(text))


@dataclass
class Reporter:
    path: Path | None = None
    quiet: bool = False

    def __post_init__(self) -> None:
        self.lines: list[str] = []

    def line(self, text: str = "") -> None:
        self.lines.append(text)
        if not self.quiet:
            print(text)

    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        if not rows:
            return
        widths = [
            max(disp_width(headers[i]), *(disp_width(str(row[i])) for row in rows))
            for i in range(len(headers))
        ]
        border = "+-" + "-+-".join("-" * w for w in widths) + "-+"
        self.line(border)
        self.line("| " + " | ".join(pad(headers[i], widths[i]) for i in range(len(headers))) + " |")
        self.line(border)
        for row in rows:
            self.line("| " + " | ".join(pad(str(row[i]), widths[i]) for i in range(len(headers))) + " |")
        self.line(border)

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def _fmt(value: object) -> str:
    return "-" if value in (None, "") else str(value)


def _explosion_rows(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    return repo.list_explosion(conn, run_id)


def _write_csv(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def _markdown_report(
    path: Path,
    run_result: dict,
    requisitions: list[dict],
    explosion: list[dict],
    orders: list[dict],
    prs: list[dict],
    exceptions: list[dict],
    execution: list[str],
    vouchers: list[dict],
    trial: list[dict],
) -> None:
    def md_table(headers: list[str], rows: list[list[object]]) -> list[str]:
        lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
        for row in rows:
            lines.append("| " + " | ".join(str(x).replace("|", "/") for x in row) + " |")
        return lines

    stats = run_result["stats"]
    lines = [
        "# 云谷鲜米粉 PP 模块 MRP 运行报告",
        "",
        f"- 运行编号：`{run_result['run_id']}`",
        f"- 运行日期：{run_result['run_date']}",
        f"- 运行人：{run_result['run_by']}",
        f"- 结果：要货单 {stats['requisition_count']} 张，生产订单 {stats['production_order_count']} 张，"
        f"采购申请 {stats['purchase_requisition_count']} 张，异常 {stats['exception_count']} 条",
        "",
        "## 1. 门店要货需求",
        "",
    ]
    lines += md_table(
        ["要货单", "门店", "需求日期", "产品", "数量"],
        [
            [req["requisition_id"], req["store_name"], req["required_date"], item["material_name"],
             fmt_qty(item["qty"], item["base_unit"])]
            for req in requisitions
            for item in req["items"]
        ],
    )
    lines += ["", "## 2. MRP 展开与净算", ""]
    lines += md_table(
        ["层级", "物料", "名称", "需求日", "毛需求", "可用库存", "净需求", "建议数量", "类型", "下达日"],
        [
            [row["level"], row["material_id"], row["material_name"], row["required_date"],
             fmt_qty(row["gross_qty"]), fmt_qty(row["stock_available"]), fmt_qty(row["net_qty"]),
             fmt_qty(row["order_qty"]), ORDER_TYPE_LABEL.get(row["order_type"], row["order_type"]),
             _fmt(row["release_date"])]
            for row in explosion
        ],
    )
    lines += ["", "## 3. 生产订单（MRP 生成时点）", ""]
    lines += md_table(
        ["生产订单", "产品", "计划数量", "需求日", "计划开始", "计划完成", "材料成本", "人工成本", "状态"],
        [
            [o["order_id"], o["material_name"], fmt_qty(o["qty_planned"], o["base_unit"]),
             o["required_date"], _fmt(o["planned_start"]), _fmt(o["planned_finish"]),
             fmt_money(o["estimated_material_cost"]), fmt_money(o["estimated_labor_cost"]), o["status"]]
            for o in orders
        ],
    )
    lines += ["", "## 4. 采购申请", ""]
    lines += md_table(
        ["采购申请", "物料", "名称", "数量", "需求日", "建议下单", "单价", "金额"],
        [
            [p["pr_id"], p["material_id"], p["material_name"], fmt_qty(p["qty"], p["unit"]),
             p["required_date"], _fmt(p["release_date"]), fmt_money(p["unit_cost"]),
             fmt_money(p["qty"] * p["unit_cost"])]
            for p in prs
        ],
    )
    lines += ["", "## 5. 计划异常", ""]
    if exceptions:
        lines += [f"- **{e['severity']} / {e['exception_type']}**：{e['message']}" for e in exceptions]
    else:
        lines.append("- 无")
    lines += ["", "## 6. 执行与 FI 过账", "", "```"]
    lines += execution
    lines += ["```", "", "## 7. 凭证汇总", ""]
    lines += md_table(
        ["科目", "科目名称", "借方", "贷方"],
        [[row["account_code"], row["account_name"], fmt_money(row["debit"]), fmt_money(row["credit"])]
         for row in trial],
    )
    lines += ["", f"业务凭证 {len(vouchers)} 张，另含期初建账 1 张（库存按成本入账）。", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(db_path: str | Path | None = None, quiet: bool = False) -> dict:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    conn = init_and_seed(path, reset=True)
    reporter = Reporter(path=OUT_DIR / "demo_report.txt", quiet=quiet)

    reporter.line("=" * 132)
    reporter.line("云谷鲜米粉 ERP · PP 生产制造模块演示（负责人：郭晨阳）")
    reporter.line("场景：门店要货 -> MRP 展开/净算 -> 生产订单 + 采购申请 -> 有限产能排产 -> 发料/报工/入库")
    reporter.line("=" * 132)

    materials = repo.list_materials(conn)
    counts = Counter(m.material_type for m in materials)
    reporter.line(
        f"主数据：物料 {len(materials)} 个（成品 {counts['FG']} / 半成品 {counts['SF']} / "
        f"原料 {counts['RM']} / 包装 {counts['PK']}）；"
        f"BOM {len(repo.list_bom_products(conn))} 套；工作中心 {len(repo.list_work_centers(conn))} 个"
    )
    for material_id in ("FG-001", "FG-002"):
        material = repo.get_material(conn, material_id)
        if material:
            reporter.line(
                f"  · {material_id} {material.material_name}：标准成本 {fmt_money(material.standard_cost)} / "
                f"{material.base_unit}，售价 {fmt_money(material.list_price or 0)}"
            )
    reporter.line("")

    reporter.line("【1】门店要货单（SD -> PP）")
    requisitions = repo.list_requisitions(conn, status="SUBMITTED")
    for req in requisitions:
        items = "，".join(
            f"{item['material_name']} {fmt_qty(item['qty'], item['base_unit'])}" for item in req["items"]
        )
        reporter.line(f"  {req['requisition_id']}  {req['store_name']}  需求日 {req['required_date']}：{items}")
    reporter.line("")

    reporter.line(f"【2】运行 MRP（运行日期 {RUN_DATE}）")
    run_result = run_mrp(conn, run_date=RUN_DATE)
    run_id = run_result["run_id"]
    reporter.line(
        f"  运行编号 {run_id}：生产订单 {run_result['stats']['production_order_count']} 张，"
        f"采购申请 {run_result['stats']['purchase_requisition_count']} 张，"
        f"异常 {run_result['stats']['exception_count']} 条"
    )
    reporter.line("")

    explosion = _explosion_rows(conn, run_id)
    reporter.line("【3】BOM 展开与库存净算（按低层码逐层计算，含安全库存/批量/有效期）")
    rows = []
    for row in explosion:
        rows.append(
            [
                str(row["level"]),
                row["material_id"],
                row["material_name"],
                row["required_date"],
                fmt_qty(row["gross_qty"]),
                fmt_qty(row["stock_available"]),
                fmt_qty(row["net_qty"]),
                fmt_qty(row["order_qty"]),
                ORDER_TYPE_LABEL.get(row["order_type"], row["order_type"]),
                _fmt(row["release_date"]),
            ]
        )
    reporter.table(
        ["层", "物料", "名称", "需求日", "毛需求", "可用库存", "净需求", "建议数量", "处理", "下达/开工日"],
        rows,
    )
    reporter.line("")

    orders = repo.list_production_orders(conn, run_id=run_id)
    reporter.line("【4】生产订单（PP 输出，含组件需求与工序排产）")
    for order in orders:
        detail = service.get_order_detail(conn, order["order_id"])
        reporter.line(
            f"  {order['order_id']}  {order['material_name']}  "
            f"{fmt_qty(order['qty_planned'], order['base_unit'])}  [{order['status']}]  "
            f"需求 {order['required_date']}  计划 "
            f"{detail['planned_start'] or '-'} -> {detail['planned_finish'] or '-'}"
        )
        reporter.line(
            f"      成本：材料 {fmt_money(order['estimated_material_cost'])} + "
            f"人工 {fmt_money(order['estimated_labor_cost'])} = "
            f"{fmt_money(order['estimated_material_cost'] + order['estimated_labor_cost'])}"
        )
        comps = "；".join(
            f"{c['material_name']} {fmt_qty(c['qty_required'], c['base_unit'])}" for c in detail["components"]
        )
        reporter.line(f"      组件：{comps}")
        for op in detail["operations"]:
            reporter.line(
                f"      工序 {op['op_no']:>2} {op['operation_name']}  {op['work_center_name']}  "
                f"{op['planned_start'] or '-'} -> {op['planned_end'] or '-'}"
            )
    reporter.line("")

    prs = repo.list_purchase_requisitions(conn, run_id=run_id)
    reporter.line("【5】采购申请（PP -> MM，外购件）")
    rows = [
        [
            pr["pr_id"], pr["material_id"], pr["material_name"], fmt_qty(pr["qty"], pr["unit"]),
            pr["required_date"], _fmt(pr["release_date"]), fmt_money(pr["unit_cost"]),
            fmt_money(pr["qty"] * pr["unit_cost"]),
        ]
        for pr in prs
    ]
    reporter.table(["采购申请", "物料", "名称", "数量", "需求日", "建议下单", "单价", "金额"], rows)
    reporter.line("")

    exceptions = repo.list_exceptions(conn, run_id)
    reporter.line("【6】计划异常 / 预警")
    for exc in exceptions:
        reporter.line(f"  [{exc['severity']}] {exc['exception_type']}：{exc['message']}")
    reporter.line("")

    reporter.line("【7】跨模块执行（MM 收货 -> PP 发料/报工/入库 -> FI 过账）")
    opening = stubs.post_opening_balance(conn, RUN_DATE)
    execution: list[str] = [f"[FI 期初] 库存期初建账 {fmt_money(opening['amount'])}"]
    mm_date = "2026-10-25"
    received = stubs.receive_purchase_requisitions(conn, run_id, mm_date)
    received_amount = sum(item["amount"] for item in received)
    execution.append(f"[MM 占位] 采购收货 {len(received)} 张，金额 {fmt_money(received_amount)}（JIT 到货日 {mm_date}）")
    reporter.line(execution[-1])

    def execute(group: list[dict], label: str, action_date: str) -> None:
        for order in group:
            detail = service.release_order(conn, order["order_id"], action_date)
            if detail["shortages"]:
                execution.append(f"[PP] {order['order_id']} 下达缺料：{detail['shortages']}")
            service.issue_components(conn, order["order_id"], action_date)
            issued = service.get_order_detail(conn, order["order_id"])
            service.confirm_order(
                conn, order["order_id"], qty_good=order["qty_planned"], on_date=action_date
            )
            confirmed = service.get_order_detail(conn, order["order_id"])
            gr = service.goods_receipt(
                conn,
                order["order_id"],
                production_date=action_date,
                on_date=action_date,
            )
            message = (
                f"[PP] {label} {order['order_id']} {order['material_name']}："
                f"下达 {detail['availability_status']} -> 发料 {fmt_money(issued['actual_material_cost'])} -> "
                f"报工 {fmt_qty(confirmed['qty_good'], order['base_unit'])} -> "
                f"入库 {fmt_qty(gr['qty'], gr['unit'])}（批次 {gr['batch_no']}，有效期 {gr['expiry_date']}）"
            )
            execution.append(message)
            reporter.line("  " + message)

    execute([o for o in orders if not o["material_id"].startswith("FG")], "半成品", "2026-10-25")
    execute([o for o in orders if o["material_id"].startswith("FG")], "成品", "2026-10-26")
    reporter.line("")

    vouchers = stubs.post_pending_events(conn)
    execution.append(f"[FI] 自动生成凭证 {len(vouchers)} 张")
    reporter.line(
        f"【8】FI 自动过账（事件驱动，业务凭证 {len(vouchers)} 张 + 期初建账 {1 if opening.get('created') else 0} 张）"
    )
    for voucher in vouchers:
        debit = next((line for line in voucher["lines"] if line["debit"]), None)
        credit = next((line for line in voucher["lines"] if line["credit"]), None)
        reporter.line(
            f"  {voucher['voucher_id']}  {voucher['voucher_type']:<20}  {voucher['posting_date']}  "
            f"借 {debit['name']} {fmt_money(debit['debit'])} / 贷 {credit['name']} {fmt_money(credit['credit'])}"
        )
    reporter.line("")

    trial = stubs.trial_balance(conn)
    reporter.line("【9】科目发生额试算（本期）")
    reporter.table(
        ["科目", "名称", "借方", "贷方"],
        [[row["account_code"], row["account_name"], fmt_money(row["debit"]), fmt_money(row["credit"])] for row in trial],
    )
    reporter.line("")

    reporter.line("【10】交付结果：成品库存与毛利")
    for material_id in ("FG-001", "FG-002"):
        material = repo.get_material(conn, material_id)
        order = next(
            (o for o in repo.list_production_orders(conn, material_id=material_id) if o["status"] == "COMPLETED"),
            None,
        )
        stock = repo.total_stock(conn, material_id)
        actual_cost = 0.0
        if order and order["qty_good"]:
            actual_cost = (order["actual_material_cost"] + order["actual_labor_cost"]) / order["qty_good"]
        price = material.list_price or 0
        margin = (price - actual_cost) / price if price else 0
        reporter.line(
            f"  {material_id} {material.material_name}：库存 {fmt_qty(stock, material.base_unit)}，"
            f"实际单位成本 {fmt_money(actual_cost)}，售价 {fmt_money(price)}，毛利率 {margin:.1%}"
        )
    reporter.line("")

    reporter.line("【11】关键物料结存（总库存，含临期批次；括号内为批次/有效期）")
    for material_id in ("SF-001", "SF-002", "SF-003", "RM-001", "RM-004", "RM-010", "PK-001", "PK-002"):
        material = repo.get_material(conn, material_id)
        batches = repo.get_batches(conn, material_id)
        if not material:
            continue
        detail = "；".join(
            f"{fmt_qty(b.qty_on_hand, material.base_unit)}（{b.expiry_date or '无有效期'}）" for b in batches
        ) or "0"
        reporter.line(
            f"  {material_id} {material.material_name}：{fmt_qty(repo.total_stock(conn, material_id), material.base_unit)}"
            f"  [{detail}]"
        )
    reporter.line("")

    reporter.save()

    # CSV / Markdown 交付物
    _write_csv(
        OUT_DIR / "mrp_explosion.csv",
        ["run_id", "level", "parent_material_id", "material_id", "material_name", "required_date",
         "gross_qty", "stock_available", "net_qty", "order_qty", "order_type", "release_date"],
        [
            [row["run_id"], row["level"], row["parent_material_id"], row["material_id"], row["material_name"],
             row["required_date"], row["gross_qty"], row["stock_available"], row["net_qty"],
             row["order_qty"], row["order_type"], row["release_date"]]
            for row in explosion
        ],
    )
    _write_csv(
        OUT_DIR / "production_orders.csv",
        ["order_id", "material_id", "material_name", "qty_planned", "base_unit", "required_date",
         "start_date", "planned_start", "planned_finish", "estimated_material_cost",
         "estimated_labor_cost", "status"],
        [
            [o["order_id"], o["material_id"], o["material_name"], o["qty_planned"], o["base_unit"],
             o["required_date"], o["start_date"], o["planned_start"], o["planned_finish"],
             o["estimated_material_cost"], o["estimated_labor_cost"], o["status"]]
            for o in orders
        ],
    )
    _write_csv(
        OUT_DIR / "purchase_requisitions.csv",
        ["pr_id", "material_id", "material_name", "qty", "unit", "required_date", "release_date",
         "unit_cost", "amount", "status"],
        [
            [p["pr_id"], p["material_id"], p["material_name"], p["qty"], p["unit"], p["required_date"],
             p["release_date"], p["unit_cost"], round_money(p["qty"] * p["unit_cost"]), p["status"]]
            for p in prs
        ],
    )
    _markdown_report(
        OUT_DIR / "MRP运行报告.md",
        run_result,
        requisitions,
        explosion,
        orders,
        prs,
        exceptions,
        execution,
        vouchers,
        trial,
    )

    conn.close()
    if not quiet:
        print()
        print(f"文本报告：{OUT_DIR / 'demo_report.txt'}")
        print(f"Markdown：{OUT_DIR / 'MRP运行报告.md'}")
        print(f"CSV：{OUT_DIR}")
    return {
        "run_id": run_id,
        "orders": len(orders),
        "prs": len(prs),
        "exceptions": len(exceptions),
        "vouchers": len(vouchers),
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    run()


if __name__ == "__main__":
    main()
