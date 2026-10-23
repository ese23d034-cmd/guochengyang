"""全模块 ERP 测试（标准库 unittest）。"""
from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from erp import co, fi, hcm, mm, sd
from erp.db import init_full_db
from pp import repo, service
from pp.mrp import run_mrp

TEST_DIR = Path(__file__).resolve().parent
TMP_DIR = TEST_DIR / "_tmp"
TMP_DIR.mkdir(exist_ok=True)


def _remove_db(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()


class FullErpCase(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = TMP_DIR / f"full_{uuid.uuid4().hex}.db"
        self.conn = init_full_db(self.db_path, reset=True)

    def tearDown(self) -> None:
        self.conn.close()
        _remove_db(self.db_path)

    def prepare_demands(self):
        pos1 = sd.record_pos_sale(self.conn, "ST-B", "2026-10-23", [("FG-001", 40, 18.0)])
        pos2 = sd.record_pos_sale(
            self.conn, "ST-C", "2026-10-23", [("FG-001", 40, 18.0), ("FG-002", 40, 16.0)]
        )
        reqs = sd.check_reorder(self.conn, "2026-10-23", "2026-10-26")
        corporate = sd.create_corporate_order(
            self.conn, "CUS-001", [("FG-001", 30, 15.0)], "2026-10-26", "2026-10-23"
        )
        extra = [
            {"product_id": "FG-001", "qty": 30, "required_date": "2026-10-26",
             "source_id": corporate["order_id"]}
        ]
        mrp = run_mrp(self.conn, run_date="2026-10-23", extra_demands=extra)
        return pos1, pos2, reqs, corporate, mrp

    def test_seed_scale(self) -> None:
        stores = self.conn.execute(
            "SELECT COUNT(*) AS n FROM store WHERE store_type = 'STORE'"
        ).fetchone()["n"]
        self.assertEqual(stores, 42)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM supplier").fetchone()["n"], 4
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM employee").fetchone()["n"], 17
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM store_inventory").fetchone()["n"], 3
        )

    def test_hcm_shifts_and_access(self) -> None:
        coverage = hcm.coverage(self.conn, "2026-10-24")
        self.assertTrue(coverage)
        self.assertTrue(all(row["status"] == "OK" for row in coverage))
        self.assertTrue(hcm.can_access(self.conn, "guo.chenyang", "PP", edit=True))
        self.assertFalse(hcm.can_access(self.conn, "guo.chenyang", "FI", edit=True))
        self.assertTrue(hcm.can_access(self.conn, "liu.haoyan", "SD", edit=True))
        self.assertEqual(len(hcm.shift_roster(self.conn, "2026-10-24")), 12)

    def test_pos_reduces_store_stock_and_triggers_reorder(self) -> None:
        self.prepare_demands()
        b = sd.store_inventory_rows(self.conn, "ST-B")[0]
        c = {row["product_id"]: row for row in sd.store_inventory_rows(self.conn, "ST-C")}
        self.assertAlmostEqual(b["qty_on_hand"], 200)
        self.assertAlmostEqual(c["FG-001"]["qty_on_hand"], 80)
        self.assertAlmostEqual(c["FG-002"]["qty_on_hand"], 40)
        reqs = repo.list_requisitions(self.conn)
        self.assertEqual(len(reqs), 2)
        items = {
            req["store_id"]: {item["product_id"]: item["qty"] for item in req["items"]}
            for req in reqs
        }
        self.assertAlmostEqual(items["ST-B"]["FG-001"], 200)
        self.assertAlmostEqual(items["ST-C"]["FG-001"], 80)
        self.assertAlmostEqual(items["ST-C"]["FG-002"], 120)

    def test_mrp_with_corporate_demand(self) -> None:
        _, _, _, _, mrp = self.prepare_demands()
        self.assertEqual(mrp["stats"]["requisition_count"], 2)
        self.assertEqual(mrp["stats"]["extra_demand_count"], 1)
        self.assertEqual(mrp["stats"]["production_order_count"], 5)
        self.assertEqual(mrp["stats"]["purchase_requisition_count"], 8)
        orders = {
            row["material_id"]: row
            for row in repo.list_production_orders(self.conn, run_id=mrp["run_id"])
        }
        self.assertAlmostEqual(orders["FG-001"]["qty_planned"], 310)
        self.assertAlmostEqual(orders["FG-002"]["qty_planned"], 120)
        self.assertAlmostEqual(orders["SF-001"]["qty_planned"], 105)
        prs = {
            row["material_id"]: row
            for row in repo.list_purchase_requisitions(self.conn, run_id=mrp["run_id"])
        }
        self.assertAlmostEqual(prs["RM-001"]["qty"], 100)
        self.assertAlmostEqual(prs["RM-010"]["qty"], 25)

    def test_mm_purchase_and_receipt(self) -> None:
        _, _, _, _, mrp = self.prepare_demands()
        pos = mm.create_purchase_orders(self.conn, mrp["run_id"], "2026-10-23")
        self.assertEqual(len(pos), 8)
        receipts = mm.receive_purchase_orders(
            self.conn, run_id=mrp["run_id"], receipt_date="2026-10-25"
        )
        self.assertEqual(len(receipts), 8)
        self.assertAlmostEqual(sum(row["amount"] for row in receipts), 4868.0)
        open_prs = repo.list_purchase_requisitions(self.conn, run_id=mrp["run_id"], status="OPEN")
        self.assertEqual(open_prs, [])

    def test_full_flow_finance_and_co(self) -> None:
        _, _, reqs, corporate, mrp = self.prepare_demands()
        fi.post_opening_balances(self.conn, "2026-10-23")
        mm.create_purchase_orders(self.conn, mrp["run_id"], "2026-10-23")
        mm.receive_purchase_orders(self.conn, run_id=mrp["run_id"], receipt_date="2026-10-25")

        orders = {
            row["material_id"]: row
            for row in repo.list_production_orders(self.conn, run_id=mrp["run_id"])
        }

        def produce(material_id: str, action_date: str) -> None:
            order = orders[material_id]
            service.release_order(self.conn, order["order_id"], action_date)
            service.issue_components(self.conn, order["order_id"], action_date)
            service.confirm_order(
                self.conn, order["order_id"], qty_good=order["qty_planned"], on_date=action_date
            )
            service.goods_receipt(
                self.conn, order["order_id"], production_date=action_date, on_date=action_date
            )

        for material_id in ("SF-001", "SF-002", "SF-003"):
            produce(material_id, "2026-10-25")
        for material_id in ("FG-001", "FG-002"):
            produce(material_id, "2026-10-26")

        store_deliveries = []
        for req in repo.list_requisitions(self.conn, status="PLANNED"):
            store_deliveries.append(
                sd.deliver_store_requisition(self.conn, req["requisition_id"], "2026-10-26")
            )
        self.assertEqual(len(store_deliveries), 2)
        corporate_delivery = sd.deliver_corporate_order(
            self.conn, corporate["order_id"], "2026-10-26"
        )
        self.assertAlmostEqual(corporate_delivery["revenue"], 450)

        sd.record_pos_sale(self.conn, "ST-B", "2026-10-26", [("FG-001", 120, 18.0)])
        sd.record_pos_sale(
            self.conn, "ST-C", "2026-10-26", [("FG-001", 50, 18.0), ("FG-002", 70, 16.0)]
        )

        fi.post_store_fixed_costs(self.conn, "2026-10-23", "2026-10-26")
        vouchers = fi.post_pending_events(self.conn)
        self.assertEqual(len(vouchers), 30)
        trial = fi.trial_balance(self.conn)
        self.assertAlmostEqual(
            sum(row["debit"] for row in trial), sum(row["credit"] for row in trial), places=2
        )
        pnl = fi.income_statement(self.conn)
        self.assertAlmostEqual(pnl["revenue"], 6710.0)
        self.assertAlmostEqual(pnl["net_profit"], 595.23)

        store_pnl = co.store_profitability(self.conn, "2026-10-23", "2026-10-26")
        self.assertAlmostEqual(sum(row["operating_profit"] for row in store_pnl), pnl["net_profit"])
        product = co.product_margin(self.conn, "2026-10-23", "2026-10-26")
        self.assertAlmostEqual(sum(row["gross_profit"] for row in product), pnl["gross_profit"])

        b = sd.store_inventory_rows(self.conn, "ST-B")[0]
        self.assertAlmostEqual(b["qty_on_hand"], 280)
        self.assertAlmostEqual(repo.total_stock(self.conn, "FG-001"), 0)
        self.assertAlmostEqual(repo.total_stock(self.conn, "FG-002"), 0)


if __name__ == "__main__":
    unittest.main()
