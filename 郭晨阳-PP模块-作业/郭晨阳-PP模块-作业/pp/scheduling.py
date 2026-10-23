"""有限产能排产：按工序在工作中心日产能上做正排。

简化假设：
  * 每天 08:00 开工，日产能以分钟计；
  * 同一订单的工序按顺序串行，工序之间不重叠；
  * 工序默认不跨天；单道工序本身超过日产能时允许加班完成，并标记告警；
  * 不同订单按需求日期先后占用产能。
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Iterable

from . import repo
from .util import to_date

DAY_START = time(8, 0)


def _minutes_from_start(moment: datetime, day: date) -> float:
    return (moment - datetime.combine(day, DAY_START)).total_seconds() / 60.0


def schedule_orders(
    conn: sqlite3.Connection,
    order_ids: Iterable[str],
    run_date: str | date,
) -> list[dict]:
    """为生产订单排产，写回工序与订单的计划开始/完成时间。"""
    run_day = to_date(run_date)
    assert run_day is not None
    load: dict[tuple[str, date], float] = defaultdict(float)
    warnings: list[dict] = []

    orders = [order for oid in order_ids if (order := repo.get_production_order(conn, oid))]
    orders.sort(key=lambda o: (o["required_date"], o["order_id"]))

    for order in orders:
        qty = float(order["qty_planned"])
        order_start = max(to_date(order["start_date"]) or run_day, run_day)
        ops = repo.get_order_operations(conn, order["order_id"])

        if not ops:
            start_dt = datetime.combine(order_start, DAY_START)
            finish_dt = start_dt + timedelta(hours=8)
            conn.execute(
                "UPDATE production_order SET planned_start = ?, planned_finish = ? WHERE order_id = ?",
                (start_dt.isoformat(sep=" "), finish_dt.isoformat(sep=" "), order["order_id"]),
            )
            warnings.append(
                {
                    "order_id": order["order_id"],
                    "type": "NO_ROUTING",
                    "message": f"{order['material_id']} 未维护工艺路线，按 1 个工作日估算",
                }
            )
            continue

        cursor_day = order_start
        prev_end: datetime | None = None
        first_start: datetime | None = None
        last_end: datetime | None = None

        for op in ops:
            wc = repo.get_work_center(conn, op["work_center_id"])
            if wc is None:
                continue
            duration = float(op["setup_min"]) + float(op["run_min_per_unit"]) * qty
            day = cursor_day
            while True:
                used = load[(wc.work_center_id, day)]
                start_min = used
                if prev_end is not None and prev_end.date() == day:
                    start_min = max(start_min, _minutes_from_start(prev_end, day))
                fits = start_min + duration <= wc.daily_capacity_min + 1e-9
                if fits:
                    break
                if (
                    used <= 1e-9
                    and duration > wc.daily_capacity_min
                    and (prev_end is None or prev_end.date() != day)
                ):
                    # 单工序超日产能，允许加班
                    start_min = 0.0
                    warnings.append(
                        {
                            "order_id": order["order_id"],
                            "type": "CAPACITY_OVERTIME",
                            "message": (
                                f"工序 {op['op_no']} {op['operation_name']} 需 {duration:.0f} 分钟，"
                                f"超过 {wc.work_center_name} 日产能 {wc.daily_capacity_min:.0f} 分钟，按加班排产"
                            ),
                        }
                    )
                    break
                day += 1

            start_dt = datetime.combine(day, DAY_START) + timedelta(minutes=start_min)
            end_dt = start_dt + timedelta(minutes=duration)
            load[(wc.work_center_id, day)] = max(used, start_min + duration)

            conn.execute(
                """UPDATE production_order_operation
                   SET planned_start = ?, planned_end = ?, status = 'SCHEDULED'
                   WHERE order_id = ? AND op_no = ?""",
                (
                    start_dt.isoformat(sep=" "),
                    end_dt.isoformat(sep=" "),
                    order["order_id"],
                    op["op_no"],
                ),
            )
            if first_start is None:
                first_start = start_dt
            last_end = end_dt
            prev_end = end_dt
            cursor_day = end_dt.date()

        if first_start and last_end:
            conn.execute(
                "UPDATE production_order SET planned_start = ?, planned_finish = ? WHERE order_id = ?",
                (
                    first_start.isoformat(sep=" "),
                    last_end.isoformat(sep=" "),
                    order["order_id"],
                ),
            )
            required = to_date(order["required_date"])
            if required and last_end.date() > required:
                warnings.append(
                    {
                        "order_id": order["order_id"],
                        "type": "SCHEDULE_DELAY",
                        "message": (
                            f"排产完成日期 {last_end.date()} 晚于需求日期 {required}，"
                            f"缺口 {(last_end.date() - required).days} 天"
                        ),
                    }
                )

    return warnings
