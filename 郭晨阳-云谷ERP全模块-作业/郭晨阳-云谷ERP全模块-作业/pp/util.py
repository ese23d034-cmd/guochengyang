"""通用小工具：日期、数量、金额、JSON。"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
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


def to_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def d(value: str | date) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)[:10]


def add_days(value: str | date, days: int) -> str:
    base = to_date(value)
    return (base + timedelta(days=days)).isoformat()  # type: ignore[arg-type]


def sub_days(value: str | date, days: int) -> str:
    return add_days(value, -days)


def round_qty(value: float) -> float:
    return round(float(value) + 0.0, 3)


def round_money(value: float) -> float:
    return round(float(value) + 0.0, 2)


def ceil_to(value: float, step: float) -> float:
    """向上取整到 step 的整数倍（step<=0 时原样返回）。"""
    if step is None or step <= 0:
        return value
    # 先消除浮点误差，避免 100.0000000001 -> 125
    n = int(value / step - 1e-9)
    if n * step < value - 1e-9:
        n += 1
    return n * step


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def fmt_qty(value: float, unit: str = "") -> str:
    text = f"{value:,.3f}".rstrip("0").rstrip(".")
    return f"{text} {unit}".strip()


def fmt_money(value: float) -> str:
    return f"¥{value:,.2f}"
