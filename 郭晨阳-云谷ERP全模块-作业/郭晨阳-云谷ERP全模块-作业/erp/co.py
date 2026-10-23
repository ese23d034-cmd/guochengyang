"""CO 管理会计：门店利润中心、门店损益、单品毛利、渠道结构。"""
from __future__ import annotations

import sqlite3
from datetime import date

from pp.util import round_money, to_date


def store_profitability(
    conn: sqlite3.Connection, period_start: str | date, period_end: str | date
) -> list[dict]:
    start = (to_date(period_start) or date.today()).isoformat()
    end = (to_date(period_end) or date.today()).isoformat()
    sales = {
        row["store_id"]: dict(row)
        for row in conn.execute(
            """SELECT store_id, SUM(revenue) AS revenue, SUM(cogs) AS cogs
               FROM sales_fact WHERE sale_date BETWEEN ? AND ?
               GROUP BY store_id""",
            (start, end),
        )
    }
    names = {
        row["store_id"]: row["store_name"]
        for row in conn.execute("SELECT store_id, store_name FROM store")
    }
    costs = {
        row["store_id"]: dict(row)
        for row in conn.execute(
            """SELECT * FROM store_fixed_cost
               WHERE period_start = ? AND period_end = ?""",
            (start, end),
        )
    }
    store_ids = sorted(set(sales) | set(costs))
    result = []
    for store_id in store_ids:
        revenue = float(sales.get(store_id, {}).get("revenue", 0) or 0)
        cogs = float(sales.get(store_id, {}).get("cogs", 0) or 0)
        cost = costs.get(store_id, {})
        rent = float(cost.get("rent", 0) or 0)
        labor = float(cost.get("labor", 0) or 0)
        utilities = float(cost.get("utilities", 0) or 0)
        other = float(cost.get("other", 0) or 0)
        gross = round_money(revenue - cogs)
        opex = round_money(rent + labor + utilities + other)
        operating = round_money(gross - opex)
        result.append(
            {
                "store_id": store_id,
                "store_name": names.get(store_id, store_id),
                "is_corporate": store_id == "ST-CORP",
                "revenue": round_money(revenue),
                "cogs": round_money(cogs),
                "gross_profit": gross,
                "gross_margin": gross / revenue if revenue else 0.0,
                "rent": round_money(rent),
                "labor": round_money(labor),
                "utilities": round_money(utilities),
                "other": round_money(other),
                "opex": opex,
                "operating_profit": operating,
                "net_margin": operating / revenue if revenue else 0.0,
            }
        )
    result.sort(key=lambda row: row["revenue"], reverse=True)
    return result


def product_margin(
    conn: sqlite3.Connection, period_start: str | date, period_end: str | date
) -> list[dict]:
    start = (to_date(period_start) or date.today()).isoformat()
    end = (to_date(period_end) or date.today()).isoformat()
    rows = conn.execute(
        """SELECT sf.product_id, m.material_name, m.base_unit,
                  SUM(sf.qty) AS qty, SUM(sf.revenue) AS revenue, SUM(sf.cogs) AS cogs
           FROM sales_fact sf JOIN material m ON m.material_id = sf.product_id
           WHERE sf.sale_date BETWEEN ? AND ?
           GROUP BY sf.product_id ORDER BY revenue DESC""",
        (start, end),
    )
    result = []
    for row in rows:
        revenue = float(row["revenue"])
        cogs = float(row["cogs"])
        result.append(
            {
                "product_id": row["product_id"],
                "product_name": row["material_name"],
                "unit": row["base_unit"],
                "qty": float(row["qty"]),
                "revenue": round_money(revenue),
                "cogs": round_money(cogs),
                "gross_profit": round_money(revenue - cogs),
                "gross_margin": (revenue - cogs) / revenue if revenue else 0.0,
            }
        )
    return result


def channel_mix(
    conn: sqlite3.Connection, period_start: str | date, period_end: str | date
) -> list[dict]:
    start = (to_date(period_start) or date.today()).isoformat()
    end = (to_date(period_end) or date.today()).isoformat()
    rows = conn.execute(
        """SELECT channel, SUM(qty) AS qty, SUM(revenue) AS revenue, SUM(cogs) AS cogs
           FROM sales_fact WHERE sale_date BETWEEN ? AND ?
           GROUP BY channel""",
        (start, end),
    )
    return [
        {
            "channel": row["channel"],
            "qty": float(row["qty"]),
            "revenue": round_money(float(row["revenue"])),
            "cogs": round_money(float(row["cogs"])),
            "gross_profit": round_money(float(row["revenue"]) - float(row["cogs"])),
        }
        for row in rows
    ]


def store_daily_revenue(
    conn: sqlite3.Connection, period_start: str | date, period_end: str | date
) -> list[dict]:
    start = (to_date(period_start) or date.today()).isoformat()
    end = (to_date(period_end) or date.today()).isoformat()
    rows = conn.execute(
        """SELECT sale_date, store_id, SUM(revenue) AS revenue, SUM(cogs) AS cogs
           FROM sales_fact WHERE sale_date BETWEEN ? AND ?
           GROUP BY sale_date, store_id ORDER BY sale_date, store_id""",
        (start, end),
    )
    return [dict(row) for row in rows]
