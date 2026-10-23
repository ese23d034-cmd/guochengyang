"""生产订单生命周期与门店要货接口。

状态机：
    PLANNED --release--> RELEASED --issue_components--> IN_PROCESS
            --confirm--> CONFIRMED --goods_receipt--> COMPLETED

业务规则：
  * 下达（release）按 FEFO 预留组件库存，允许部分预留并返回缺口；
  * 发料（issue）优先消耗本订单预留，再消耗自由库存；库存不足整单回滚；
  * 报工（confirm）登记良品/废品与人工成本；
  * 入库（goods_receipt）按批次、生产日期、保质期生成成品库存，
    并向 FI 发出 GOODS_ISSUE / PRODUCTION_CONFIRMED / GOODS_RECEIPT 事件。
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Sequence

from . import repo
from .db import emit_event, next_number, transaction
from .util import QTY_EPS, fmt_qty, now_iso, round_money, round_qty, to_date


class ServiceError(Exception):
    pass


class NotFoundError(ServiceError):
    pass


class ValidationError(ServiceError):
    pass


class OrderStateError(ServiceError):
    pass


class InsufficientStockError(ServiceError):
    def __init__(self, shortages: list[dict]) -> None:
        self.shortages = shortages
        detail = "；".join(
            f"{s['material_name']} 缺 {fmt_qty(s['shortage'], s['unit'])}" for s in shortages
        )
        super().__init__(f"组件库存不足：{detail}")


# ---------------------------------------------------------------------------
# SD -> PP：门店要货单
# ---------------------------------------------------------------------------
def submit_requisition(
    conn: sqlite3.Connection,
    store_id: str,
    items: Sequence[tuple[str, float]],
    required_date: str | date,
    request_date: str | date | None = None,
    remark: str | None = None,
) -> str:
    req_date = to_date(request_date) or date.today()
    need_date = to_date(required_date)
    if need_date is None:
        raise ValidationError("required_date 不能为空")
    if need_date < req_date:
        raise ValidationError("需求日期不能早于要货日期")
    if not items:
        raise ValidationError("要货单至少需要一行明细")

    store = conn.execute("SELECT * FROM store WHERE store_id = ?", (store_id,)).fetchone()
    if store is None:
        raise ValidationError(f"门店不存在: {store_id}")

    normalized: list[tuple[str, float]] = []
    for product_id, qty in items:
        material = repo.get_material(conn, product_id)
        if material is None or not material.is_active:
            raise ValidationError(f"物料不存在或已停用: {product_id}")
        if material.material_type != "FG":
            raise ValidationError(f"{product_id} 不是成品，门店要货只能选择成品")
        if qty <= 0:
            raise ValidationError(f"{product_id} 要货数量必须大于 0")
        normalized.append((product_id, round_qty(qty)))

    with transaction(conn):
        requisition_id = next_number(conn, f"REQ-{req_date:%Y%m%d}")
        repo.insert_requisition(
            conn,
            requisition_id,
            store_id,
            req_date.isoformat(),
            need_date.isoformat(),
            normalized,
            remark,
        )
        emit_event(
            conn,
            "STORE_REQUISITION_SUBMITTED",
            requisition_id,
            {
                "requisition_id": requisition_id,
                "store_id": store_id,
                "required_date": need_date.isoformat(),
                "items": [{"product_id": p, "qty": q} for p, q in normalized],
            },
        )
    return requisition_id


# ---------------------------------------------------------------------------
# 订单查询
# ---------------------------------------------------------------------------
def get_order_detail(conn: sqlite3.Connection, order_id: str) -> dict:
    order = repo.get_production_order(conn, order_id)
    if order is None:
        raise NotFoundError(f"生产订单不存在: {order_id}")
    order["components"] = repo.get_order_components(conn, order_id)
    order["operations"] = repo.get_order_operations(conn, order_id)
    order["confirmations"] = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM production_confirmation WHERE order_id = ? ORDER BY confirmation_id",
            (order_id,),
        )
    ]
    return order


# ---------------------------------------------------------------------------
# 下达 / 预留
# ---------------------------------------------------------------------------
def release_order(conn: sqlite3.Connection, order_id: str, on_date: str | date | None = None) -> dict:
    with transaction(conn):
        order = get_order_detail(conn, order_id)
        if order["status"] != "PLANNED":
            raise OrderStateError(
                f"生产订单 {order_id} 当前状态为 {order['status']}，只有 PLANNED 可以下达"
            )
        posting = to_date(on_date) or date.today()
        shortages: list[dict] = []

        for comp in order["components"]:
            remaining = round_qty(comp["qty_required"] - comp["qty_issued"])
            if remaining <= QTY_EPS:
                continue
            # 幂等：先释放旧的未清预留
            _cancel_open_reservations(conn, order_id, comp["material_id"])
            batches = repo.get_batches(conn, comp["material_id"])
            reserved = 0.0
            for batch in batches:
                if remaining <= QTY_EPS:
                    break
                if not batch.available_on(posting):
                    continue
                take = min(batch.free_qty, remaining)
                take = round_qty(take)
                if take <= QTY_EPS:
                    continue
                conn.execute(
                    """INSERT INTO inventory_reservation
                       (order_id, material_id, batch_id, qty, status, created_at)
                       VALUES (?, ?, ?, ?, 'OPEN', ?)""",
                    (order_id, comp["material_id"], batch.batch_id, round_qty(take), now_iso()),
                )
                conn.execute(
                    "UPDATE inventory_batch SET qty_reserved = qty_reserved + ? WHERE batch_id = ?",
                    (take, batch.batch_id),
                )
                reserved += take
                remaining -= take

            conn.execute(
                """UPDATE production_order_component
                   SET qty_reserved = ? WHERE order_id = ? AND line_no = ?""",
                (round_qty(reserved), order_id, comp["line_no"]),
            )
            if remaining > QTY_EPS:
                shortages.append(
                    {
                        "material_id": comp["material_id"],
                        "material_name": comp["material_name"],
                        "unit": comp["base_unit"],
                        "shortage": round_qty(remaining),
                    }
                )

        status = "RELEASED"
        availability = "PARTIAL" if shortages else "FULL"
        conn.execute(
            "UPDATE production_order SET status = ?, availability_status = ? WHERE order_id = ?",
            (status, availability, order_id),
        )
        emit_event(
            conn,
            "PRODUCTION_ORDER_RELEASED",
            order_id,
            {
                "order_id": order_id,
                "material_id": order["material_id"],
                "shortages": shortages,
                "availability": availability,
                "posting_date": posting.isoformat(),
            },
        )
        result = get_order_detail(conn, order_id)
        result["shortages"] = shortages
        return result


# ---------------------------------------------------------------------------
# 发料
# ---------------------------------------------------------------------------
def issue_components(conn: sqlite3.Connection, order_id: str, on_date: str | date | None = None) -> dict:
    with transaction(conn):
        order = get_order_detail(conn, order_id)
        if order["status"] not in ("RELEASED", "IN_PROCESS"):
            raise OrderStateError(
                f"生产订单 {order_id} 当前状态为 {order['status']}，只有 RELEASED / IN_PROCESS 可以发料"
            )
        posting = to_date(on_date) or date.today()
        shortages: list[dict] = []
        total_cost = 0.0
        lines: list[dict] = []

        for comp in order["components"]:
            remaining = round_qty(comp["qty_required"] - comp["qty_issued"])
            if remaining <= QTY_EPS:
                continue
            consumed, cost = _consume_component(
                conn, order_id, comp["material_id"], remaining, posting
            )
            total_cost += cost
            if consumed + QTY_EPS < remaining:
                shortages.append(
                    {
                        "material_id": comp["material_id"],
                        "material_name": comp["material_name"],
                        "unit": comp["base_unit"],
                        "shortage": round_qty(remaining - consumed),
                    }
                )
                continue  # 先继续算其他组件，最后统一抛错回滚
            conn.execute(
                """UPDATE production_order_component
                   SET qty_issued = qty_required, qty_reserved = 0
                   WHERE order_id = ? AND line_no = ?""",
                (order_id, comp["line_no"]),
            )
            lines.append(
                {
                    "material_id": comp["material_id"],
                    "qty": round_qty(consumed),
                    "unit": comp["base_unit"],
                    "amount": round_money(cost),
                }
            )

        if shortages:
            raise InsufficientStockError(shortages)

        conn.execute(
            """UPDATE production_order
               SET status = 'IN_PROCESS',
                   actual_start = COALESCE(actual_start, ?),
                   actual_material_cost = actual_material_cost + ?
               WHERE order_id = ?""",
            (now_iso(), round_money(total_cost), order_id),
        )
        emit_event(
            conn,
            "GOODS_ISSUE",
            order_id,
            {
                "order_id": order_id,
                "material_id": order["material_id"],
                "posting_date": posting.isoformat(),
                "amount": round_money(total_cost),
                "lines": lines,
            },
        )
        return get_order_detail(conn, order_id)


# ---------------------------------------------------------------------------
# 报工
# ---------------------------------------------------------------------------
def confirm_order(
    conn: sqlite3.Connection,
    order_id: str,
    qty_good: float | None = None,
    qty_scrap: float = 0.0,
    confirmed_by: str = "郭晨阳",
    on_date: str | date | None = None,
) -> dict:
    with transaction(conn):
        order = get_order_detail(conn, order_id)
        if order["status"] not in ("RELEASED", "IN_PROCESS"):
            raise OrderStateError(
                f"生产订单 {order_id} 当前状态为 {order['status']}，只有 RELEASED / IN_PROCESS 可以报工"
            )
        posting = to_date(on_date) or date.today()
        planned = float(order["qty_planned"])
        good = planned - qty_scrap if qty_good is None else float(qty_good)
        if qty_scrap < 0 or good < 0:
            raise ValidationError("良品数量和废品数量不能为负数")
        if good + qty_scrap > planned + 1e-6:
            raise ValidationError(
                f"良品 {good} + 废品 {qty_scrap} 超过订单计划数量 {planned}"
            )
        if good + qty_scrap <= QTY_EPS:
            raise ValidationError("良品与废品数量不能同时为 0")

        labor = round_money(order["estimated_labor_cost"])
        conn.execute(
            """UPDATE production_order
               SET status = 'CONFIRMED', qty_good = ?, qty_scrap = ?,
                   actual_finish = ?, actual_labor_cost = actual_labor_cost + ?
               WHERE order_id = ?""",
            (round_qty(good), round_qty(qty_scrap), now_iso(), labor, order_id),
        )
        conn.execute(
            """INSERT INTO production_confirmation
               (order_id, qty_good, qty_scrap, labor_cost, confirmed_at, confirmed_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (order_id, round_qty(good), round_qty(qty_scrap), labor, now_iso(), confirmed_by),
        )
        emit_event(
            conn,
            "PRODUCTION_CONFIRMED",
            order_id,
            {
                "order_id": order_id,
                "material_id": order["material_id"],
                "qty_good": round_qty(good),
                "qty_scrap": round_qty(qty_scrap),
                "labor_amount": labor,
                "posting_date": posting.isoformat(),
            },
        )
        return get_order_detail(conn, order_id)


# ---------------------------------------------------------------------------
# 成品入库
# ---------------------------------------------------------------------------
def goods_receipt(
    conn: sqlite3.Connection,
    order_id: str,
    qty: float | None = None,
    warehouse_id: str | None = None,
    production_date: str | date | None = None,
    on_date: str | date | None = None,
) -> dict:
    with transaction(conn):
        order = get_order_detail(conn, order_id)
        if order["status"] != "CONFIRMED":
            raise OrderStateError(
                f"生产订单 {order_id} 当前状态为 {order['status']}，只有 CONFIRMED 可以入库"
            )
        good = float(order["qty_good"])
        if good <= QTY_EPS:
            raise ValidationError("良品数量为 0，无法入库")
        remaining = round_qty(good - float(order["qty_received"]))
        receive = remaining if qty is None else round_qty(qty)
        if receive <= QTY_EPS:
            raise ValidationError("入库数量必须大于 0")
        if receive > remaining + QTY_EPS:
            raise ValidationError(f"入库数量 {receive} 超过待入库数量 {remaining}")

        material = repo.get_material(conn, order["material_id"])
        assert material is not None
        prod_date = to_date(production_date) or date.today()
        expiry = None
        if material.shelf_life_days:
            expiry = (prod_date + timedelta(days=material.shelf_life_days)).isoformat()
        wh = warehouse_id or material.default_warehouse_id
        if wh is None:
            raise ValidationError(f"{material.material_id} 未指定默认仓库")
        if conn.execute("SELECT 1 FROM warehouse WHERE warehouse_id = ?", (wh,)).fetchone() is None:
            raise ValidationError(f"仓库不存在: {wh}")

        total_cost = float(order["actual_material_cost"]) + float(order["actual_labor_cost"])
        unit_cost = total_cost / good if good > 0 else 0.0
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM inventory_batch WHERE batch_id LIKE ?",
            (f"B{order_id}-%",),
        ).fetchone()
        seq = int(row["n"]) + 1
        batch_id = f"B{order_id}-{seq:02d}"
        batch_no = f"{order_id}-{seq:02d}"
        conn.execute(
            """INSERT INTO inventory_batch
               (batch_id, material_id, warehouse_id, batch_no, qty_on_hand, qty_reserved,
                production_date, expiry_date, unit_cost, status)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, 'UNRESTRICTED')""",
            (batch_id, material.material_id, wh, batch_no, receive, prod_date.isoformat(), expiry, unit_cost),
        )
        amount = round_money(receive * unit_cost)
        conn.execute(
            """INSERT INTO inventory_movement
               (movement_type, material_id, batch_id, qty, unit_cost, amount, order_id,
                reference_no, posting_date, created_at)
               VALUES ('PRODUCTION_OUTPUT', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                material.material_id,
                batch_id,
                receive,
                unit_cost,
                amount,
                order_id,
                order_id,
                (to_date(on_date) or date.today()).isoformat(),
                now_iso(),
            ),
        )
        received = round_qty(float(order["qty_received"]) + receive)
        status = "COMPLETED" if received >= good - QTY_EPS else "CONFIRMED"
        conn.execute(
            "UPDATE production_order SET qty_received = ?, status = ? WHERE order_id = ?",
            (received, status, order_id),
        )
        emit_event(
            conn,
            "GOODS_RECEIPT",
            order_id,
            {
                "order_id": order_id,
                "material_id": material.material_id,
                "batch_id": batch_id,
                "batch_no": batch_no,
                "qty": receive,
                "unit": material.base_unit,
                "unit_cost": round(unit_cost, 6),
                "amount": amount,
                "production_date": prod_date.isoformat(),
                "expiry_date": expiry,
                "warehouse_id": wh,
                "posting_date": (to_date(on_date) or date.today()).isoformat(),
            },
        )
        return {
            "order_id": order_id,
            "batch_id": batch_id,
            "batch_no": batch_no,
            "qty": receive,
            "unit": material.base_unit,
            "expiry_date": expiry,
            "unit_cost": round(unit_cost, 6),
            "amount": amount,
            "status": status,
        }


# ---------------------------------------------------------------------------
# 内部：库存消耗与预留
# ---------------------------------------------------------------------------
def _cancel_open_reservations(
    conn: sqlite3.Connection, order_id: str, material_id: str
) -> None:
    rows = conn.execute(
        """SELECT * FROM inventory_reservation
           WHERE order_id = ? AND material_id = ? AND status = 'OPEN'""",
        (order_id, material_id),
    ).fetchall()
    for row in rows:
        outstanding = float(row["qty"]) - float(row["qty_consumed"])
        if outstanding > 0:
            conn.execute(
                "UPDATE inventory_batch SET qty_reserved = MAX(0, qty_reserved - ?) WHERE batch_id = ?",
                (outstanding, row["batch_id"]),
            )
        conn.execute(
            "UPDATE inventory_reservation SET status = 'CANCELLED' WHERE reservation_id = ?",
            (row["reservation_id"],),
        )


def _consume_component(
    conn: sqlite3.Connection,
    order_id: str,
    material_id: str,
    required: float,
    posting: date,
) -> tuple[float, float]:
    """先消耗订单预留，再消耗自由库存；返回 (消耗数量, 金额)。"""
    remaining = required
    total_cost = 0.0

    reservations = conn.execute(
        """SELECT r.*, b.unit_cost, b.qty_on_hand
           FROM inventory_reservation r
           JOIN inventory_batch b ON b.batch_id = r.batch_id
           WHERE r.order_id = ? AND r.material_id = ? AND r.status = 'OPEN'
           ORDER BY CASE WHEN b.expiry_date IS NULL THEN 1 ELSE 0 END, b.expiry_date""",
        (order_id, material_id),
    ).fetchall()
    for res in reservations:
        if remaining <= QTY_EPS:
            break
        outstanding = float(res["qty"]) - float(res["qty_consumed"])
        take = round_qty(min(outstanding, remaining, float(res["qty_on_hand"])))
        if take <= QTY_EPS:
            continue
        _consume_batch(conn, res["batch_id"], material_id, take, order_id, posting, from_reservation=True)
        conn.execute(
            """UPDATE inventory_reservation
               SET qty_consumed = qty_consumed + ?,
                   status = CASE WHEN qty_consumed + ? >= qty - 0.000001 THEN 'CLOSED' ELSE 'OPEN' END
               WHERE reservation_id = ?""",
            (take, take, res["reservation_id"]),
        )
        remaining -= take
        total_cost += take * float(res["unit_cost"])

    if remaining > QTY_EPS:
        for batch in repo.get_batches(conn, material_id):
            if remaining <= QTY_EPS:
                break
            if not batch.available_on(posting):
                continue
            take = min(batch.free_qty, remaining)
            take = round_qty(take)
            if take <= QTY_EPS:
                continue
            _consume_batch(conn, batch.batch_id, material_id, take, order_id, posting, from_reservation=False)
            remaining -= take
            total_cost += take * batch.unit_cost

    return round_qty(required - remaining), round_money(total_cost)


def _consume_batch(
    conn: sqlite3.Connection,
    batch_id: str,
    material_id: str,
    take: float,
    order_id: str,
    posting: date,
    from_reservation: bool,
) -> None:
    row = conn.execute(
        "SELECT qty_on_hand, qty_reserved, unit_cost FROM inventory_batch WHERE batch_id = ?",
        (batch_id,),
    ).fetchone()
    if row is None:
        raise InsufficientStockError(
            [{"material_id": material_id, "material_name": material_id, "unit": "", "shortage": take}]
        )
    take = min(take, float(row["qty_on_hand"]))
    if from_reservation:
        conn.execute(
            """UPDATE inventory_batch
               SET qty_on_hand = qty_on_hand - ?,
                   qty_reserved = MAX(0, qty_reserved - ?)
               WHERE batch_id = ?""",
            (take, take, batch_id),
        )
    else:
        conn.execute(
            "UPDATE inventory_batch SET qty_on_hand = qty_on_hand - ? WHERE batch_id = ?",
            (take, batch_id),
        )
    conn.execute(
        """INSERT INTO inventory_movement
           (movement_type, material_id, batch_id, qty, unit_cost, amount, order_id,
            reference_no, posting_date, created_at)
           VALUES ('GOODS_ISSUE', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            material_id,
            batch_id,
            -take,
            float(row["unit_cost"]),
            round_money(take * float(row["unit_cost"])),
            order_id,
            order_id,
            posting.isoformat(),
            now_iso(),
        ),
    )
