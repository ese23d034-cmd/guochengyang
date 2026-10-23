"""MM 采购与库存模块测试。"""
from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from mm import inventory, master_data, procurement, repo
from mm.seed import MRP_RUN_ID, init_and_seed

TEST_DIR = Path(__file__).resolve().parent
TMP_DIR = TEST_DIR / "_tmp"
TMP_DIR.mkdir(exist_ok=True)


def _remove_db(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()


class MmCase(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = TMP_DIR / f"mm_{uuid.uuid4().hex}.db"
        self.conn = init_and_seed(self.db_path, reset=True)

    def tearDown(self) -> None:
        self.conn.close()
        _remove_db(self.db_path)

    def _receive(self):
        procurement.create_purchase_orders(self.conn, MRP_RUN_ID, "2026-10-23")
        return procurement.receive_purchase_orders(
            self.conn, run_id=MRP_RUN_ID, receipt_date="2026-10-25"
        )

    def test_master_data_and_bom(self) -> None:
        summary = master_data.master_summary(self.conn)
        self.assertEqual(summary["materials"], 20)
        self.assertEqual(summary["boms"], 5)
        self.assertEqual(summary["suppliers"], 4)
        bom = master_data.query_bom(self.conn, "FG-001")
        self.assertEqual(bom["bom_id"], "BOM-FG-001")
        self.assertEqual(len(bom["components"]), 6)
        ids = {c["component_id"] for c in bom["components"]}
        self.assertIn("SF-001", ids)
        self.assertIn("PK-001", ids)
        self.assertGreater(bom["estimated_material_cost"], 0)

    def test_purchase_orders_and_receipts(self) -> None:
        prs = repo.list_requisitions(self.conn, status="OPEN", run_id=MRP_RUN_ID)
        self.assertEqual(len(prs), 8)
        pos = procurement.create_purchase_orders(self.conn, MRP_RUN_ID, "2026-10-23")
        self.assertEqual(len(pos), 8)
        self.assertAlmostEqual(sum(p["amount"] for p in pos), 4868.0)
        self.assertEqual(
            next(p["supplier_name"] for p in pos if p["material_id"] == "RM-004"),
            "鲜联肉食品",
        )
        receipts = procurement.receive_purchase_orders(
            self.conn, run_id=MRP_RUN_ID, receipt_date="2026-10-25"
        )
        self.assertEqual(len(receipts), 8)
        beef = next(r for r in receipts if r["material_id"] == "RM-004")
        self.assertEqual(beef["expiry_date"], "2026-10-28")
        self.assertEqual(beef["warehouse_id"], "WH-COLD")
        self.assertTrue(
            all(p["status"] == "RECEIVED" for p in repo.list_purchase_orders(self.conn))
        )
        self.assertTrue(
            all(p["status"] == "RECEIVED" for p in repo.list_requisitions(self.conn))
        )

    def test_shelf_life_and_expired(self) -> None:
        self._receive()
        alerts = inventory.shelf_life_alerts(self.conn, "2026-10-25", 7)
        names = {a["material_name"] for a in alerts}
        self.assertIn("牛腩", names)
        self.assertIn("青菜", names)
        expired = inventory.expired_stock(self.conn, "2026-10-25")
        expired_names = {e["material_name"] for e in expired}
        self.assertIn("牛腩", expired_names)
        self.assertIn("青菜", expired_names)
        scrap = inventory.scrap_expired(self.conn, "2026-10-25")
        self.assertAlmostEqual(sum(x["value"] for x in scrap), 234.0)
        self.assertAlmostEqual(repo.available_qty(self.conn, "RM-004", "2026-10-25"), 30.0)

    def test_issue_fefo_and_expiry_exclusion(self) -> None:
        self._receive()
        result = inventory.issue_to_production(
            self.conn, "WO-TEST-BEEF", [("RM-004", 22.248)], "2026-10-25"
        )
        self.assertAlmostEqual(result["total_amount"], 1512.86, places=2)
        batches = {b.batch_no: b for b in repo.get_batches(self.conn, "RM-004")}
        self.assertAlmostEqual(batches["BEEF-20261021"].qty_on_hand, 3.0)  # 过期批次未消耗
        self.assertAlmostEqual(batches["POPUR-20261023-0002"].qty_on_hand, 7.752, places=3)
        self.assertIn("POPUR-20261023-0002", result["lines"][0]["batch_no"])

    def test_issue_rice_consumes_oldest_first(self) -> None:
        self._receive()
        inventory.issue_to_production(self.conn, "WO-TEST-RICE", [("RM-001", 110.801)], "2026-10-25")
        batches = {b.batch_no: b for b in repo.get_batches(self.conn, "RM-001")}
        self.assertNotIn("RICE-20261001", batches)  # 80kg 旧批次先被消耗
        self.assertAlmostEqual(batches["POPUR-20261023-0001"].qty_on_hand, 69.199, places=3)

    def test_insufficient_stock_rolls_back(self) -> None:
        self._receive()
        before = repo.available_qty(self.conn, "RM-001", "2026-10-25")
        with self.assertRaises(inventory.InventoryError):
            inventory.issue_to_production(self.conn, "WO-TEST-SHORT", [("RM-001", 1000)], "2026-10-25")
        self.assertAlmostEqual(repo.available_qty(self.conn, "RM-001", "2026-10-25"), before)
        self.assertEqual(repo.list_issues(self.conn), [])


if __name__ == "__main__":
    unittest.main()
