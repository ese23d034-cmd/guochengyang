"""HCM 人力资源：员工主数据、门店/工厂排班、班次覆盖检查、权限访问控制。"""
from __future__ import annotations

import sqlite3
from datetime import date

from pp.db import next_number, transaction
from pp.util import to_date


def employees(conn: sqlite3.Connection, department: str | None = None) -> list[dict]:
    sql = """SELECT e.*, s.store_name
             FROM employee e LEFT JOIN store s ON s.store_id = e.store_id
             WHERE 1 = 1"""
    args: list = []
    if department:
        sql += " AND e.department = ?"
        args.append(department)
    sql += " ORDER BY e.department, e.employee_id"
    return [dict(row) for row in conn.execute(sql, args)]


def shift_roster(
    conn: sqlite3.Connection,
    work_date: str | date,
    location: str | None = None,
) -> list[dict]:
    day = (to_date(work_date) or date.today()).isoformat()
    sql = """SELECT sh.*, e.employee_name, e.position, e.department
             FROM shift sh JOIN employee e ON e.employee_id = sh.employee_id
             WHERE sh.work_date = ?"""
    args: list = [day]
    if location:
        sql += " AND sh.location = ?"
        args.append(location)
    sql += " ORDER BY sh.location, sh.shift_type, sh.employee_id"
    return [dict(row) for row in conn.execute(sql, args)]


def coverage(conn: sqlite3.Connection, work_date: str | date) -> list[dict]:
    day = (to_date(work_date) or date.today()).isoformat()
    rows = conn.execute(
        """SELECT r.location, r.shift_type, r.min_staff,
                  COUNT(sh.shift_id) AS scheduled
           FROM shift_requirement r
           LEFT JOIN shift sh
             ON sh.location = r.location AND sh.shift_type = r.shift_type AND sh.work_date = ?
           GROUP BY r.location, r.shift_type, r.min_staff
           ORDER BY r.location, r.shift_type""",
        (day,),
    )
    result = []
    for row in rows:
        scheduled = int(row["scheduled"])
        required = int(row["min_staff"])
        result.append(
            {
                "location": row["location"],
                "shift_type": row["shift_type"],
                "required": required,
                "scheduled": scheduled,
                "gap": max(0, required - scheduled),
                "status": "OK" if scheduled >= required else "UNDERSTAFFED",
            }
        )
    return result


def can_access(
    conn: sqlite3.Connection, user_id: str, module_code: str, edit: bool = False
) -> bool:
    row = conn.execute(
        """SELECT p.can_view, p.can_edit
           FROM app_user u JOIN role_permission p ON p.role_code = u.role_code
           WHERE u.user_id = ? AND p.module_code = ?""",
        (user_id, module_code.upper()),
    ).fetchone()
    if row is None:
        return False
    return bool(row["can_edit"] if edit else row["can_view"])


def user_profile(conn: sqlite3.Connection, user_id: str) -> dict | None:
    row = conn.execute(
        """SELECT u.*, e.employee_name, e.position, e.department
           FROM app_user u LEFT JOIN employee e ON e.employee_id = u.employee_id
           WHERE u.user_id = ?""",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def access_matrix(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT p.*, u.user_id, u.display_name
           FROM role_permission p
           LEFT JOIN app_user u ON u.role_code = p.role_code
           ORDER BY p.role_code, p.module_code"""
    )
    return [dict(row) for row in rows]


def schedule_shift(
    conn: sqlite3.Connection,
    employee_id: str,
    work_date: str | date,
    shift_type: str,
    start_time: str,
    end_time: str,
    location: str,
) -> dict:
    day = (to_date(work_date) or date.today()).isoformat()
    employee = conn.execute(
        "SELECT * FROM employee WHERE employee_id = ?", (employee_id,)
    ).fetchone()
    if employee is None:
        raise ValueError(f"员工不存在: {employee_id}")
    with transaction(conn):
        shift_id = next_number(conn, f"SH-{day.replace('-', '')}")
        conn.execute(
            """INSERT INTO shift(shift_id, employee_id, work_date, shift_type,
                                 start_time, end_time, location)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (shift_id, employee_id, day, shift_type, start_time, end_time, location),
        )
    return {
        "shift_id": shift_id,
        "employee_id": employee_id,
        "employee_name": employee["employee_name"],
        "work_date": day,
        "shift_type": shift_type,
        "start_time": start_time,
        "end_time": end_time,
        "location": location,
    }
