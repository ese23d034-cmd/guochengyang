"""跨模块占位实现（仅用于课程演示，正式代码由 MM / FI 组提供）。

* MM：把 PP 产生的采购申请收货入库，使后续生产发料有库存可用；
* FI：消费 PP 发出的业务事件，按凭证模板自动记账。

这两个文件是“接口契约”的可运行示例，不是 PP 模块的职责边界。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

from . import repo
from .db import emit_event, next_number, transaction
from .util import now_iso, round_money, to_date


# ---------------------------------------------------------------------------
# MM 占位：采购申请 -> 收货入库
# ---------------------------------------------------------------------------
def receive_purchase_requisitions(
    conn: sqlite3.Connection,
    run_id: str | None = None,
    on_date: str | date | None = None,
) -> list[dict]:
    posting = to_date(on_date) or date.today()
    received: list[dict] = []
    with transaction(conn):
        for pr in repo.list_purchase_requisitions(conn, run_id=run_id, status="OPEN"):
            material = repo.get_material(conn, pr["material_id"])
            if material is None:
                continue
            warehouse = material.default_warehouse_id
            if warehouse is None:
                continue
            expiry = None
            if material.shelf_life_days:
                expiry = (posting + timedelta(days=material.shelf_life_days)).isoformat()
            batch_id = f"MB{pr['pr_id']}"
            batch_no = pr["pr_id"]
            unit_cost = float(pr["unit_cost"]) or material.standard_cost
            conn.execute(
                """INSERT OR REPLACE INTO inventory_batch
                   (batch_id, material_id, warehouse_id, batch_no, qty_on_hand, qty_reserved,
                    production_date, expiry_date, unit_cost, status)
                   VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?, 'UNRESTRICTED')""",
                (batch_id, material.material_id, warehouse, batch_no, pr["qty"], expiry, unit_cost),
            )
            amount = round_money(float(pr["qty"]) * unit_cost)
            conn.execute(
                """INSERT INTO inventory_movement
                   (movement_type, material_id, batch_id, qty, unit_cost, amount, order_id,
                    reference_no, posting_date, created_at)
                   VALUES ('PURCHASE_RECEIPT', ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
                (
                    material.material_id,
                    batch_id,
                    pr["qty"],
                    unit_cost,
                    amount,
                    pr["pr_id"],
                    posting.isoformat(),
                    now_iso(),
                ),
            )
            conn.execute("UPDATE purchase_requisition SET status = 'RECEIVED' WHERE pr_id = ?", (pr["pr_id"],))
            emit_event(
                conn,
                "PURCHASE_RECEIPT",
                pr["pr_id"],
                {
                    "pr_id": pr["pr_id"],
                    "material_id": material.material_id,
                    "qty": pr["qty"],
                    "unit": material.base_unit,
                    "unit_cost": unit_cost,
                    "amount": amount,
                    "batch_id": batch_id,
                    "expiry_date": expiry,
                    "posting_date": posting.isoformat(),
                },
            )
            received.append(
                {
                    "pr_id": pr["pr_id"],
                    "material_id": material.material_id,
                    "material_name": material.material_name,
                    "qty": float(pr["qty"]),
                    "unit": material.base_unit,
                    "amount": amount,
                    "expiry_date": expiry,
                }
            )
    return received


# ---------------------------------------------------------------------------
# FI 占位：事件 -> 会计凭证（模板来自项目文档）
# ---------------------------------------------------------------------------
ACCOUNTS = {
    "5001": "生产成本",
    "1403": "原材料",
    "1405": "库存商品",
    "2211": "应付职工薪酬",
    "2202": "应付账款",
    "3001": "期初余额",
}


def _post_event(conn: sqlite3.Connection, event: sqlite3.Row) -> dict | None:
    payload = json.loads(event["payload"])
    event_type = event["event_type"]
    posting = to_date(payload.get("posting_date")) or date.today()

    if event_type == "GOODS_ISSUE":
        amount = round_money(float(payload.get("amount", 0)))
        if amount <= 0:
            return None
        lines = [
            ("5001", "生产成本", amount, 0.0),
            ("1403", "原材料", 0.0, amount),
        ]
        text = "生产订单发料"
    elif event_type == "PRODUCTION_CONFIRMED":
        amount = round_money(float(payload.get("labor_amount", 0)))
        if amount <= 0:
            return None
        lines = [
            ("5001", "生产成本", amount, 0.0),
            ("2211", "应付职工薪酬", 0.0, amount),
        ]
        text = "生产报工人工成本"
    elif event_type == "GOODS_RECEIPT":
        amount = round_money(float(payload.get("amount", 0)))
        if amount <= 0:
            return None
        lines = [
            ("1405", "库存商品", amount, 0.0),
            ("5001", "生产成本", 0.0, amount),
        ]
        text = "成品入库"
    elif event_type == "PURCHASE_RECEIPT":
        amount = round_money(float(payload.get("amount", 0)))
        if amount <= 0:
            return None
        lines = [
            ("1403", "原材料", amount, 0.0),
            ("2202", "应付账款", 0.0, amount),
        ]
        text = "采购收货"
    else:
        return None

    voucher_id = next_number(conn, f"FV-{posting.year}")
    total = sum(line[2] for line in lines)
    conn.execute(
        """INSERT INTO fi_voucher
           (voucher_id, voucher_type, posting_date, reference_id, header_text,
            total_amount, source_event, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            voucher_id,
            event_type,
            posting.isoformat(),
            event["aggregate_id"],
            text,
            total,
            event["event_id"],
            now_iso(),
        ),
    )
    for index, (account, name, debit, credit) in enumerate(lines, start=1):
        conn.execute(
            """INSERT INTO fi_voucher_line
               (voucher_id, line_no, account_code, account_name, debit, credit)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (voucher_id, index, account, name, debit, credit),
        )
    return {
        "voucher_id": voucher_id,
        "voucher_type": event_type,
        "posting_date": posting.isoformat(),
        "reference_id": event["aggregate_id"],
        "amount": total,
        "lines": [
            {"account": account, "name": name, "debit": debit, "credit": credit}
            for account, name, debit, credit in lines
        ],
    }


def post_pending_events(conn: sqlite3.Connection) -> list[dict]:
    vouchers: list[dict] = []
    with transaction(conn):
        events = conn.execute(
            "SELECT * FROM integration_event WHERE status = 'PENDING' ORDER BY event_id"
        ).fetchall()
        for event in events:
            voucher = _post_event(conn, event)
            conn.execute(
                "UPDATE integration_event SET status = 'PROCESSED', processed_at = ? WHERE event_id = ?",
                (now_iso(), event["event_id"]),
            )
            if voucher:
                vouchers.append(voucher)
    return vouchers


def list_vouchers(conn: sqlite3.Connection) -> list[dict]:
    result = []
    for row in conn.execute("SELECT * FROM fi_voucher ORDER BY voucher_id"):
        item = dict(row)
        item["lines"] = [
            dict(line)
            for line in conn.execute(
                "SELECT * FROM fi_voucher_line WHERE voucher_id = ? ORDER BY line_no",
                (row["voucher_id"],),
            )
        ]
        result.append(item)
    return result


def trial_balance(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT account_code, MIN(account_name) AS account_name,
                  SUM(debit) AS debit, SUM(credit) AS credit
           FROM fi_voucher_line GROUP BY account_code ORDER BY account_code"""
    )
    return [dict(row) for row in rows]


def post_opening_balance(
    conn: sqlite3.Connection, on_date: str | date | None = None
) -> dict:
    """FI 期初建账（演示用）：把现有批次库存按成本入账，贷方为期初余额。"""
    posting = to_date(on_date) or date.today()
    total_raw = 0.0
    total_finished = 0.0
    for row in conn.execute(
        """SELECT b.qty_on_hand, b.unit_cost, m.material_type
           FROM inventory_batch b JOIN material m ON m.material_id = b.material_id"""
    ):
        amount = float(row["qty_on_hand"]) * float(row["unit_cost"])
        if row["material_type"] == "FG":
            total_finished += amount
        else:
            total_raw += amount
    total_raw = round_money(total_raw)
    total_finished = round_money(total_finished)
    total = round_money(total_raw + total_finished)
    voucher_id = f"FV-OPEN-{posting.year}"
    with transaction(conn):
        existing = conn.execute("SELECT 1 FROM fi_voucher WHERE voucher_id = ?", (voucher_id,)).fetchone()
        if existing:
            return {"voucher_id": voucher_id, "amount": total, "created": False}
        conn.execute(
            """INSERT INTO fi_voucher
               (voucher_id, voucher_type, posting_date, reference_id, header_text,
                total_amount, source_event, created_at)
               VALUES (?, 'OPENING_BALANCE', ?, 'OPENING', '期初库存建账', ?, NULL, ?)""",
            (voucher_id, posting.isoformat(), total, now_iso()),
        )
        lines = []
        if total_raw:
            lines.append(("1403", "原材料", total_raw, 0.0))
        if total_finished:
            lines.append(("1405", "库存商品", total_finished, 0.0))
        lines.append(("3001", "期初余额", 0.0, total))
        for index, (account, name, debit, credit) in enumerate(lines, start=1):
            conn.execute(
                """INSERT INTO fi_voucher_line
                   (voucher_id, line_no, account_code, account_name, debit, credit)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (voucher_id, index, account, name, debit, credit),
            )
    return {
        "voucher_id": voucher_id,
        "amount": total,
        "raw": total_raw,
        "finished": total_finished,
        "created": True,
    }


def mm_receive_all(conn: sqlite3.Connection, run_id: str | None = None, on_date: str | date | None = None) -> list[dict]:
    """兼容旧命名。"""
    return receive_purchase_requisitions(conn, run_id=run_id, on_date=on_date)
