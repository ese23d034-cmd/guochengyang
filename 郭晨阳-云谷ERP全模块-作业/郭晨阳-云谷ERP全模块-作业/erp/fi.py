"""FI 财务会计：总账科目、事件驱动凭证、期初建账、试算平衡与利润表。"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

from pp.db import next_number, transaction
from pp.util import now_iso, round_money, to_date

from .inventory import inventory_value, store_inventory_value

ACCOUNTS = {
    "1002": "银行存款",
    "1122": "应收账款",
    "1403": "原材料",
    "1405": "库存商品",
    "1406": "门店库存商品",
    "2202": "应付账款",
    "2211": "应付职工薪酬",
    "2241": "其他应付款",
    "3001": "期初余额",
    "5001": "生产成本",
    "6001": "主营业务收入",
    "6401": "主营业务成本",
    "6601": "销售费用",
}


class FiError(Exception):
    pass


def _voucher(
    conn: sqlite3.Connection,
    voucher_type: str,
    posting_date: str,
    reference_id: str,
    header_text: str,
    lines: list[tuple[str, str, float, float]],
    source_event: int | None = None,
    voucher_id: str | None = None,
) -> dict:
    debit_total = round_money(sum(line[2] for line in lines))
    credit_total = round_money(sum(line[3] for line in lines))
    if abs(debit_total - credit_total) > 0.01:
        raise FiError(f"凭证借贷不平: 借 {debit_total} / 贷 {credit_total}（{header_text}）")
    if voucher_id is None:
        voucher_id = next_number(conn, f"FV-{posting_date[:4]}")
    conn.execute(
        """INSERT INTO fi_voucher
           (voucher_id, voucher_type, posting_date, reference_id, header_text,
            total_amount, source_event, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (voucher_id, voucher_type, posting_date, reference_id, header_text,
         debit_total, source_event, now_iso()),
    )
    for index, (account, name, debit, credit) in enumerate(lines, start=1):
        conn.execute(
            """INSERT INTO fi_voucher_line
               (voucher_id, line_no, account_code, account_name, debit, credit)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (voucher_id, index, account, name, round_money(debit), round_money(credit)),
        )
    return {
        "voucher_id": voucher_id,
        "voucher_type": voucher_type,
        "posting_date": posting_date,
        "reference_id": reference_id,
        "header_text": header_text,
        "amount": debit_total,
        "lines": [
            {"account": account, "name": name, "debit": round_money(debit), "credit": round_money(credit)}
            for account, name, debit, credit in lines
        ],
    }


def _post_event(conn: sqlite3.Connection, event: sqlite3.Row) -> dict | None:
    payload = json.loads(event["payload"])
    event_type = event["event_type"]
    posting = (to_date(payload.get("posting_date")) or date.today()).isoformat()
    reference = event["aggregate_id"] or ""

    if event_type == "PURCHASE_RECEIPT":
        amount = round_money(float(payload.get("amount", 0)))
        if amount <= 0:
            return None
        lines = [
            ("1403", ACCOUNTS["1403"], amount, 0),
            ("2202", ACCOUNTS["2202"], 0, amount),
        ]
        text = f"采购收货 {payload.get('po_id', '')}"
    elif event_type == "GOODS_ISSUE":
        amount = round_money(float(payload.get("amount", 0)))
        if amount <= 0:
            return None
        lines = [
            ("5001", ACCOUNTS["5001"], amount, 0),
            ("1403", ACCOUNTS["1403"], 0, amount),
        ]
        text = f"生产发料 {payload.get('order_id', '')}"
    elif event_type == "PRODUCTION_CONFIRMED":
        amount = round_money(float(payload.get("labor_amount", 0)))
        if amount <= 0:
            return None
        lines = [
            ("5001", ACCOUNTS["5001"], amount, 0),
            ("2211", ACCOUNTS["2211"], 0, amount),
        ]
        text = f"生产报工人工 {payload.get('order_id', '')}"
    elif event_type == "GOODS_RECEIPT":
        amount = round_money(float(payload.get("amount", 0)))
        if amount <= 0:
            return None
        lines = [
            ("1405", ACCOUNTS["1405"], amount, 0),
            ("5001", ACCOUNTS["5001"], 0, amount),
        ]
        text = f"成品入库 {payload.get('order_id', '')}"
    elif event_type == "STORE_DELIVERY":
        amount = round_money(float(payload.get("amount", 0)))
        if amount <= 0:
            return None
        lines = [
            ("1406", ACCOUNTS["1406"], amount, 0),
            ("1405", ACCOUNTS["1405"], 0, amount),
        ]
        text = f"配送到店 {payload.get('delivery_id', '')}"
    elif event_type == "POS_SALE":
        amount = round_money(float(payload.get("amount", 0)))
        cost = round_money(float(payload.get("cost_amount", 0)))
        if amount <= 0:
            return None
        lines = [
            ("1002", ACCOUNTS["1002"], amount, 0),
            ("6001", ACCOUNTS["6001"], 0, amount),
            ("6401", ACCOUNTS["6401"], cost, 0),
            ("1406", ACCOUNTS["1406"], 0, cost),
        ]
        text = f"POS 零售 {payload.get('store_id', '')}"
    elif event_type == "SALES_DELIVERY":
        amount = round_money(float(payload.get("amount", 0)))
        cost = round_money(float(payload.get("cost_amount", 0)))
        if amount <= 0:
            return None
        lines = [
            ("1122", ACCOUNTS["1122"], amount, 0),
            ("6001", ACCOUNTS["6001"], 0, amount),
            ("6401", ACCOUNTS["6401"], cost, 0),
            ("1405", ACCOUNTS["1405"], 0, cost),
        ]
        text = f"团餐配送 {payload.get('order_id', '')}"
    else:
        return None

    return _voucher(
        conn, event_type, posting, reference, text, lines, source_event=event["event_id"]
    )


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


def post_opening_balances(conn: sqlite3.Connection, posting_date: str | date) -> dict:
    day = (to_date(posting_date) or date.today()).isoformat()
    raw = round_money(
        inventory_value(conn, "RM") + inventory_value(conn, "PK") + inventory_value(conn, "SF")
    )
    fg = round_money(
        conn.execute(
            """SELECT COALESCE(SUM(b.qty_on_hand * b.unit_cost), 0) AS v
               FROM inventory_batch b JOIN material m ON m.material_id = b.material_id
               WHERE m.material_type = 'FG'"""
        ).fetchone()["v"]
    )
    store = round_money(store_inventory_value(conn))
    total = round_money(raw + fg + store)
    voucher_id = f"FV-OPEN-{day[:4]}"
    existing = conn.execute(
        "SELECT 1 FROM fi_voucher WHERE voucher_id = ?", (voucher_id,)
    ).fetchone()
    if existing:
        return {"voucher_id": voucher_id, "amount": total, "created": False}
    lines: list[tuple[str, str, float, float]] = []
    if raw > 0:
        lines.append(("1403", ACCOUNTS["1403"], raw, 0))
    if fg > 0:
        lines.append(("1405", ACCOUNTS["1405"], fg, 0))
    if store > 0:
        lines.append(("1406", ACCOUNTS["1406"], store, 0))
    if total <= 0:
        return {"voucher_id": None, "amount": 0, "created": False}
    lines.append(("3001", ACCOUNTS["3001"], 0, total))
    with transaction(conn):
        voucher = _voucher(
            conn, "OPENING_BALANCE", day, "OPENING", "期初库存建账", lines, voucher_id=voucher_id
        )
    return {"voucher_id": voucher["voucher_id"], "amount": total, "created": True}


def post_store_fixed_costs(
    conn: sqlite3.Connection, period_start: str | date, period_end: str | date
) -> list[dict]:
    start = (to_date(period_start) or date.today()).isoformat()
    end = (to_date(period_end) or date.today()).isoformat()
    rows = conn.execute(
        """SELECT * FROM store_fixed_cost
           WHERE period_start = ? AND period_end = ?
           ORDER BY store_id""",
        (start, end),
    ).fetchall()
    vouchers: list[dict] = []
    with transaction(conn):
        for row in rows:
            reference = f"FIXED-{row['store_id']}-{start}-{end}"
            exists = conn.execute(
                "SELECT 1 FROM fi_voucher WHERE reference_id = ? AND voucher_type = 'PERIOD_EXPENSE'",
                (reference,),
            ).fetchone()
            if exists:
                continue
            total = round_money(
                float(row["rent"]) + float(row["labor"]) + float(row["utilities"]) + float(row["other"])
            )
            if total <= 0:
                continue
            lines = [
                ("6601", ACCOUNTS["6601"], float(row["rent"]), 0),
                ("6601", ACCOUNTS["6601"], float(row["labor"]), 0),
                ("6601", ACCOUNTS["6601"], float(row["utilities"]), 0),
                ("6601", ACCOUNTS["6601"], float(row["other"]), 0),
                ("2241", ACCOUNTS["2241"], 0, total),
            ]
            vouchers.append(
                _voucher(
                    conn,
                    "PERIOD_EXPENSE",
                    end,
                    reference,
                    f"{row['store_id']} 门店期间费用",
                    lines,
                )
            )
    return vouchers


def vouchers(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    result = []
    for row in conn.execute(
        "SELECT * FROM fi_voucher ORDER BY posting_date, voucher_id LIMIT ?", (limit,)
    ):
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


def income_statement(conn: sqlite3.Connection) -> dict:
    balances = {
        row["account_code"]: float(row["debit"]) - float(row["credit"])
        for row in conn.execute(
            "SELECT account_code, SUM(debit) AS debit, SUM(credit) AS credit "
            "FROM fi_voucher_line GROUP BY account_code"
        )
    }
    revenue = round_money(-balances.get("6001", 0.0))
    cogs = round_money(balances.get("6401", 0.0))
    expenses = round_money(balances.get("6601", 0.0) + balances.get("6602", 0.0))
    gross = round_money(revenue - cogs)
    net = round_money(gross - expenses)
    return {
        "revenue": revenue,
        "cogs": cogs,
        "gross_profit": gross,
        "gross_margin": gross / revenue if revenue else 0.0,
        "operating_expenses": expenses,
        "net_profit": net,
        "net_margin": net / revenue if revenue else 0.0,
    }
