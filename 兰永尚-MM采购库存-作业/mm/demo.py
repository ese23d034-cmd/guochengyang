"""MM 采购与库存模块端到端演示。

流程：
  物料/BOM 主数据 → PP 采购申请 → 供应商采购订单 → JIT 收货/批次建档
  → 效期预警 → 生产发料（FEFO）→ 过期报损 → 库存/事件报告。
"""
from __future__ import annotations

import csv
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import inventory, master_data, procurement, repo
from .seed import DEFAULT_DB_PATH, MRP_RUN_ID, init_and_seed
from .util import fmt_money, fmt_qty, round_money

PROJECT_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_DIR / "out"

ISSUE_ORDERS = [
    ("WO-SF-001（鲜湿米粉 105kg）", [
        ("RM-001", 110.801), ("RM-002", 2.1), ("RM-003", 0.525),
    ]),
    ("WO-SF-002（牛肉臊子 30kg）", [
        ("RM-004", 22.248), ("RM-005", 0.606), ("RM-006", 1.5), ("RM-007", 0.3),
    ]),
    ("WO-SF-003（肥肠臊子 15kg）", [
        ("RM-008", 11.025), ("RM-009", 1.53), ("RM-005", 0.303),
        ("RM-006", 0.6), ("RM-013", 0.45),
    ]),
]


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
        widths = [max(disp_width(headers[i]), *(disp_width(row[i]) for row in rows))
                  for i in range(len(headers))]
        border = "+-" + "-+-".join("-" * w for w in widths) + "-+"
        self.line(border)
        self.line("| " + " | ".join(pad(headers[i], widths[i]) for i in range(len(headers))) + " |")
        self.line(border)
        for row in rows:
            self.line("| " + " | ".join(pad(row[i], widths[i]) for i in range(len(headers))) + " |")
        self.line(border)

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def _csv(path: Path, headers: list[str], rows: list[list[object]]) -> None:
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
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    conn = init_and_seed(path, reset=True)
    r = Reporter(path=OUT_DIR / "MM运行报告.txt", quiet=quiet)
    r.line("=" * 132)
    r.line("云谷鲜米粉 ERP · MM 采购与库存模块演示（负责人：兰永尚）")
    r.line("物料主数据 / BOM / 供应商 / 采购申请 / 采购订单 / 冷链收货 / 批次效期 / 生产发料")
    r.line("=" * 132)
    summary = master_data.master_summary(conn)
    r.line(
        f"主数据：物料 {summary['materials']} 个（成品 {summary['counts']['FG']} / "
        f"半成品 {summary['counts']['SF']} / 原料 {summary['counts']['RM']} / 包装 {summary['counts']['PK']}）；"
        f"BOM {summary['boms']} 套；供应商 {summary['suppliers']} 家；仓库 {summary['warehouses']} 个"
    )
    r.line("")

    # -- 1. BOM 查询 -------------------------------------------------------
    r.line("【1】物料主数据 / BOM 配方查询（按产品 ID）")
    bom = master_data.query_bom(conn, "FG-001")
    r.line(
        f"  {bom['product_id']} {bom['product_name']}  {bom['bom_id']} {bom['version']}  "
        f"基准量 {fmt_qty(bom['base_qty'], '碗')}，估算材料成本 {fmt_money(bom['estimated_material_cost'])}"
    )
    r.table(
        ["行", "组件", "名称", "单位用量", "损耗率", "标准成本"],
        [[str(c["line_no"]), c["component_id"], c["component_name"], fmt_qty(c["qty_per_unit"], c["unit"] or ""),
          f"{c['scrap_rate']:.1%}", fmt_money(c["standard_cost"])] for c in bom["components"]],
    )
    r.line("")

    # -- 2. 采购申请 -------------------------------------------------------
    prs = repo.list_requisitions(conn, status="OPEN", run_id=MRP_RUN_ID)
    r.line(f"【2】PP → MM 采购申请（MRP 运行号 {MRP_RUN_ID}）")
    r.table(
        ["采购申请", "物料", "名称", "数量", "需求日", "建议下单", "单价", "金额"],
        [[p["pr_id"], p["material_id"], p["material_name"], fmt_qty(p["qty"], p["unit"]),
          p["required_date"], p["release_date"], fmt_money(p["unit_cost"]),
          fmt_money(p["qty"] * p["unit_cost"])] for p in prs],
    )
    r.line("")

    # -- 3. 采购订单 -------------------------------------------------------
    r.line("【3】MM 采购订单（按物料默认供应商拆单）")
    created = procurement.create_purchase_orders(conn, run_id=MRP_RUN_ID, order_date="2026-10-23")
    r.table(
        ["采购订单", "供应商", "物料", "数量", "单价", "金额", "预计到货"],
        [[p["po_id"], p["supplier_name"], p["material_name"], fmt_qty(p["qty"], p["unit"]),
          fmt_money(p["unit_cost"]), fmt_money(p["amount"]), p["expected_date"]] for p in created],
    )
    r.line(f"  合计 {len(created)} 张采购订单，金额 {fmt_money(sum(p['amount'] for p in created))}")
    r.line("")

    # -- 4. 收货 / 批次 ---------------------------------------------------
    r.line("【4】JIT 收货与冷链批次建档（2026-10-25）")
    receipts = procurement.receive_purchase_orders(conn, run_id=MRP_RUN_ID, receipt_date="2026-10-25")
    r.table(
        ["收货单", "采购订单", "供应商", "物料", "数量", "金额", "批次", "到期日"],
        [[x["receipt_id"], x["po_id"], x["supplier_name"], x["material_name"],
          fmt_qty(x["qty"], x["unit"]), fmt_money(x["amount"]), x["batch_id"],
          x["expiry_date"] or "-"] for x in receipts],
    )
    r.line(
        f"  合计 {len(receipts)} 张收货单，金额 {fmt_money(sum(x['amount'] for x in receipts))}；"
        f"采购申请状态已更新为 RECEIVED"
    )
    r.line("")

    # -- 5. 效期预警 -------------------------------------------------------
    r.line("【5】鲜食效期预警（7 天内到期）")
    alerts = inventory.shelf_life_alerts(conn, "2026-10-25", within_days=7)
    r.table(
        ["物料", "批次", "仓库", "数量", "到期日", "剩余天数", "库存金额"],
        [[a["material_name"], a["batch_no"], a["warehouse_name"], fmt_qty(a["qty"], a["unit"]),
          a["expiry_date"], f"{a['days_to_expiry']} 天", fmt_money(a["value"])] for a in alerts],
    )
    expired = inventory.expired_stock(conn, "2026-10-25")
    r.line("  已过期批次（不参与发料，需报损）：")
    r.table(
        ["物料", "批次", "数量", "到期日", "损失金额"],
        [[e["material_name"], e["batch_no"], fmt_qty(e["qty"], e["unit"]),
          e["expiry_date"], fmt_money(e["value"])] for e in expired],
    )
    r.line("")

    # -- 6. 生产发料 -------------------------------------------------------
    r.line("【6】生产发料（FEFO：先到期先出，过期批次自动排除）")
    issue_results = []
    for order_id, lines in ISSUE_ORDERS:
        result = inventory.issue_to_production(conn, order_id, lines, "2026-10-25")
        issue_results.append(result)
        r.line(
            f"  {order_id}：{len(result['lines'])} 个批次行，金额 {fmt_money(result['total_amount'])}"
        )
    r.line(f"  发料合计 {fmt_money(sum(x['total_amount'] for x in issue_results))}")
    beef_issue = next(
        (line for result in issue_results for line in result["lines"]
         if line["material_id"] == "RM-004"), None
    )
    if beef_issue:
        r.line(
            f"  示例：牛腩发料使用批次 {beef_issue['batch_no']}（到期 {beef_issue['expiry_date']}），"
            f"3kg 过期批次未被消耗"
        )
    r.line("")

    # -- 7. 报损 -----------------------------------------------------------
    r.line("【7】过期报损（EXPIRED_SCRAP）")
    scrap = inventory.scrap_expired(conn, "2026-10-25")
    r.table(
        ["物料", "批次", "报损数量", "到期日", "损失金额"],
        [[x["material_name"], x["batch_no"], fmt_qty(x["qty"], x["unit"]),
          x["expiry_date"], fmt_money(x["value"])] for x in scrap],
    )
    r.line(f"  合计损失 {fmt_money(sum(x['value'] for x in scrap))}")
    r.line("")

    # -- 8. 库存结果 -------------------------------------------------------
    r.line("【8】期末库存与仓库分布")
    stock = inventory.stock_summary(conn)
    r.table(
        ["物料", "名称", "数量", "库存金额", "最早到期"],
        [[s["material_id"], s["material_name"], fmt_qty(s["qty"], s["base_unit"]),
          fmt_money(s["value"]), s["earliest_expiry"] or "-"] for s in stock],
    )
    r.line(f"  库存总值 {fmt_money(sum(s['value'] for s in stock))}")
    r.table(
        ["仓库", "类型", "物料数", "库存金额"],
        [[w["warehouse_id"], w["warehouse_type"], str(w["material_count"]),
          fmt_money(w["value"] or 0)] for w in inventory.warehouse_stock(conn)],
    )
    r.line("")

    # -- 9. 集成事件 -------------------------------------------------------
    r.line("【9】MM → PP/FI 集成事件（Outbox）")
    events = repo.list_events(conn)
    counts: dict[str, int] = {}
    for event in events:
        counts[event["event_type"]] = counts.get(event["event_type"], 0) + 1
    r.line("  " + "；".join(f"{k} {v} 条" for k, v in sorted(counts.items())))
    r.line("  FI 可据此记账：采购收货 借 1403 原材料 / 贷 2202 应付账款；")
    r.line("                 生产发料 借 5001 生产成本 / 贷 1403 原材料；")
    r.line("                 过期报损 由 FI/CO 按报损科目处理。")
    r.line("")
    r.line("  结论：MM 完成从采购申请到批次库存、效期预警、FEFO 发料和报损的闭环；")
    r.line("        鲜食批次效期参与发料校验，可避免把过期原料投入生产。")
    r.save()

    # -- CSV / Markdown ----------------------------------------------------
    _csv(
        OUT_DIR / "mm_purchase_orders.csv",
        ["po_id", "supplier_name", "material_id", "material_name", "qty", "unit", "unit_cost",
         "amount", "expected_date", "status"],
        [[p["po_id"], p["supplier_name"], p["material_id"], p["material_name"], p["qty"],
          p["unit"], p["unit_cost"], round_money(p["qty"] * p["unit_cost"]),
          p["expected_date"], p["status"]] for p in repo.list_purchase_orders(conn)],
    )
    _csv(
        OUT_DIR / "mm_receipts.csv",
        ["receipt_id", "po_id", "supplier_name", "material_name", "qty", "unit", "amount",
         "batch_id", "expiry_date"],
        [[x["receipt_id"], x["po_id"], x["supplier_name"], x["material_name"], x["qty"],
          x["unit"], x["amount"], x["batch_id"], x["expiry_date"]] for x in receipts],
    )
    _csv(
        OUT_DIR / "mm_inventory.csv",
        ["material_id", "material_name", "warehouse_id", "batch_no", "qty_on_hand",
         "unit_cost", "expiry_date"],
        [[b["material_id"], b["material_name"], b["warehouse_id"], b["batch_no"],
          b["qty_on_hand"], b["unit_cost"], b["expiry_date"]] for b in repo.stock_rows(conn)],
    )
    _csv(
        OUT_DIR / "mm_shelf_life.csv",
        ["material_name", "batch_no", "warehouse_name", "qty", "unit", "expiry_date", "days_to_expiry"],
        [[a["material_name"], a["batch_no"], a["warehouse_name"], a["qty"], a["unit"],
          a["expiry_date"], a["days_to_expiry"]] for a in alerts],
    )
    _csv(
        OUT_DIR / "mm_goods_issues.csv",
        ["issue_id", "order_id", "issue_date", "material_id", "batch_id", "qty", "unit_cost", "amount"],
        [[line["issue_id"], line["order_id"], line["issue_date"], line["material_id"],
          line["batch_id"], line["qty"], line["unit_cost"], line["amount"]]
         for result in issue_results for line in result["lines"]],
    )
    _csv(
        OUT_DIR / "mm_events.csv",
        ["event_id", "event_type", "aggregate_id", "status", "created_at"],
        [[e["event_id"], e["event_type"], e["aggregate_id"], e["status"], e["created_at"]]
         for e in events],
    )

    _markdown(
        OUT_DIR / "MM运行报告.md",
        summary,
        bom,
        prs,
        created,
        receipts,
        alerts,
        expired,
        issue_results,
        scrap,
        stock,
        events,
    )

    conn.close()
    if not quiet:
        print()
        print(f"文本报告：{OUT_DIR / 'MM运行报告.txt'}")
        print(f"Markdown：{OUT_DIR / 'MM运行报告.md'}")
        print(f"CSV：{OUT_DIR}")
    return {
        "purchase_orders": len(created),
        "receipts": len(receipts),
        "issues": len(issue_results),
        "scrap_value": round_money(sum(x["value"] for x in scrap)),
        "inventory_value": round_money(sum(s["value"] for s in stock)),
    }


def _markdown(
    path: Path,
    summary: dict,
    bom: dict,
    prs: list[dict],
    created: list[dict],
    receipts: list[dict],
    alerts: list[dict],
    expired: list[dict],
    issue_results: list[dict],
    scrap: list[dict],
    stock: list[dict],
    events: list[dict],
) -> None:
    lines = [
        "# 云谷鲜米粉 MM 采购与库存模块运行报告",
        "",
        f"- 负责人：兰永尚",
        f"- 物料 {summary['materials']} 个；BOM {summary['boms']} 套；供应商 {summary['suppliers']} 家；仓库 {summary['warehouses']} 个",
        f"- 采购订单 {len(created)} 张；收货 {len(receipts)} 张；发料 {len(issue_results)} 张生产订单；",
        f"- 过期报损 {fmt_money(sum(x['value'] for x in scrap))}；期末库存 {fmt_money(sum(s['value'] for s in stock))}",
        "",
        "## 1. BOM 配方查询",
        "",
        f"{bom['product_id']} {bom['product_name']}（{bom['bom_id']}，基准 {bom['base_qty']} 碗）",
        "",
    ]
    lines += _md_table(
        ["行", "组件", "名称", "单位用量", "损耗率", "标准成本"],
        [[c["line_no"], c["component_id"], c["component_name"], c["qty_per_unit"],
          f"{c['scrap_rate']:.1%}", fmt_money(c["standard_cost"])] for c in bom["components"]],
    )
    lines += ["", "## 2. PP → MM 采购申请", ""]
    lines += _md_table(
        ["采购申请", "物料", "数量", "需求日", "建议下单", "金额"],
        [[p["pr_id"], p["material_name"], fmt_qty(p["qty"], p["unit"]), p["required_date"],
          p["release_date"], fmt_money(p["qty"] * p["unit_cost"])] for p in prs],
    )
    lines += ["", "## 3. MM 采购订单", ""]
    lines += _md_table(
        ["采购订单", "供应商", "物料", "数量", "金额", "预计到货"],
        [[p["po_id"], p["supplier_name"], p["material_name"], fmt_qty(p["qty"], p["unit"]),
          fmt_money(p["amount"]), p["expected_date"]] for p in created],
    )
    lines += ["", "## 4. 收货批次", ""]
    lines += _md_table(
        ["收货单", "物料", "数量", "批次", "到期日"],
        [[x["receipt_id"], x["material_name"], fmt_qty(x["qty"], x["unit"]), x["batch_id"],
          x["expiry_date"] or "-"] for x in receipts],
    )
    lines += ["", "## 5. 效期预警", ""]
    lines += _md_table(
        ["物料", "批次", "数量", "到期日", "剩余天数"],
        [[a["material_name"], a["batch_no"], fmt_qty(a["qty"], a["unit"]), a["expiry_date"],
          a["days_to_expiry"]] for a in alerts],
    )
    lines += ["", "已过期批次：", ""]
    lines += _md_table(
        ["物料", "批次", "数量", "到期日", "损失"],
        [[e["material_name"], e["batch_no"], fmt_qty(e["qty"], e["unit"]), e["expiry_date"],
          fmt_money(e["value"])] for e in expired],
    )
    lines += ["", "## 6. 生产发料（FEFO）", ""]
    for result in issue_results:
        lines.append(f"### {result['order_id']}（{fmt_money(result['total_amount'])}）")
        lines.append("")
        lines += _md_table(
            ["物料", "批次", "数量", "单价", "金额", "到期日"],
            [[line["material_id"], line["batch_no"], fmt_qty(line["qty"], line["unit"]),
              fmt_money(line["unit_cost"]), fmt_money(line["amount"]), line["expiry_date"]]
             for line in result["lines"]],
        )
        lines.append("")
    lines += ["## 7. 过期报损", ""]
    lines += _md_table(
        ["物料", "批次", "数量", "损失金额"],
        [[x["material_name"], x["batch_no"], fmt_qty(x["qty"], x["unit"]), fmt_money(x["value"])]
         for x in scrap],
    )
    lines += ["", "## 8. 期末库存", ""]
    lines += _md_table(
        ["物料", "名称", "数量", "金额", "最早到期"],
        [[s["material_id"], s["material_name"], fmt_qty(s["qty"], s["base_unit"]),
          fmt_money(s["value"]), s["earliest_expiry"] or "-"] for s in stock],
    )
    lines += [
        "",
        "## 9. 集成事件",
        "",
        f"共 {len(events)} 条事件，FI 可据此生成采购收货/生产发料凭证。",
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
