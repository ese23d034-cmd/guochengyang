"""通用工具：日期、数量、金额、JSON。"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

QTY_EPS = 1e-6


def today() -> date:
    return date.today()


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def to_date(value: str | date | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def d(value: str | date) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)[:10]


def round_qty(value: float) -> float:
    return round(float(value) + 0.0, 3)


def round_money(value: float) -> float:
    return round(float(value) + 0.0, 2)


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def fmt_qty(value: float, unit: str = "") -> str:
    text = f"{value:,.3f}".rstrip("0").rstrip(".")
    return f"{text} {unit}".strip()


def fmt_money(value: float) -> str:
    return f"¥{value:,.2f}"
