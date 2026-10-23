"""PP 模块自动化测试（标准库 unittest，无第三方依赖）。

运行：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.request
import uuid
from pathlib import Path

from pp import api, repo, service, stubs
from pp.mrp import NoDemandError, run_mrp
from pp.seed import RUN_DATE, init_and_seed

TEST_DIR = Path(__file__).resolve().parent
TMP_DIR = TEST_DIR / "_tmp"
TMP_DIR.mkdir(exist_ok=True)


def _remove_db(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()


class BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = TMP_DIR / f"pp_test_{uuid.uuid4().hex}.db"
        self.conn = init_and_seed(self.db_path, reset=True)

    def tearDown(self) -> None:
        self.conn.close()
        _remove_db(self.db_path)


class TestMasterDataAndCosting(BaseCase):
    def test_bom_query(self) -> None:
        bom = repo.get_bom(self.conn, "FG-001")
        self.assertIsNotNone(bom)
        assert bom is not None
        self.assertEqual(bom.version, "V1")
        self.assertEqual(len(bom.components), 6)
        component_ids = {item.component_id for item in bom.components}
        self.assertIn("SF-001", component_ids)
        self.assertIn("PK-001", component_ids)

    def test_standard_cost_rollup(self) -> None:
        sf = repo.get_material(self.conn, "SF-001")
        fg = repo.get_material(self.conn, "FG-001")
        self.assertGreater(sf.standard_cost, 5)
        self.assertAlmostEqual(sf.standard_cost, 7.11, places=2)
        self.assertAlmostEqual(fg.standard_cost, 8.97, places=2)

    def test_material_counts(self) -> None:
        materials = repo.list_materials(self.conn)
        self.assertEqual(len(materials), 20)


class TestMrp(BaseCase):
    def setUp(self) -> None:
        super().setUp()
        self.result = run_mrp(self.conn, run_date=RUN_DATE)
        self.run_id = self.result["run_id"]
        self.orders = {
            order["material_id"]: order
            for order in repo.list_production_orders(self.conn, run_id=self.run_id)
        }
        self.prs = {
            pr["material_id"]: pr
            for pr in repo.list_purchase_requisitions(self.conn, run_id=self.run_id)
        }
        self.explosion = {
            (row["material_id"], row["required_date"]): row
            for row in repo.list_explosion(self.conn, self.run_id)
        }

    def test_result_counts(self) -> None:
        self.assertEqual(self.result["stats"]["requisition_count"], 2)
        self.assertEqual(self.result["stats"]["production_order_count"], 5)
        self.assertEqual(self.result["stats"]["purchase_requisition_count"], 8)
        self.assertEqual(self.result["stats"]["exception_count"], 2)

    def test_production_order_quantities(self) -> None:
        self.assertAlmostEqual(self.orders["FG-001"]["qty_planned"], 280)
        self.assertAlmostEqual(self.orders["FG-002"]["qty_planned"], 120)
        self.assertAlmostEqual(self.orders["SF-001"]["qty_planned"], 95)  # 102 - 8 库存，批量 5
        self.assertAlmostEqual(self.orders["SF-002"]["qty_planned"], 30)  # 22.624，批量 10
        self.assertAlmostEqual(self.orders["SF-003"]["qty_planned"], 15)  # 10.908，批量 5

    def test_purchase_requisition_quantities(self) -> None:
        self.assertAlmostEqual(self.prs["RM-001"]["qty"], 75)     # 100.249 - (80-50)，批量 25
        self.assertAlmostEqual(self.prs["RM-004"]["qty"], 30)     # 22.248，且 3kg 到期不可用
        self.assertAlmostEqual(self.prs["RM-005"]["qty"], 2)      # 0.909 汇总，最小起订 1
        self.assertAlmostEqual(self.prs["RM-008"]["qty"], 20)     # 11.025 + 安全库存 5，批量 5
        self.assertAlmostEqual(self.prs["RM-010"]["qty"], 24)     # 20.304，6kg 到期不可用
        self.assertAlmostEqual(self.prs["RM-011"]["qty"], 300)    # 280 - (150-100)，批量 100
        self.assertAlmostEqual(self.prs["RM-012"]["qty"], 150)    # 120 - (60-50)，批量 50
        self.assertAlmostEqual(self.prs["PK-001"]["qty"], 1000)   # 净需求 4，触发 MOQ 1000
        self.assertNotIn("PK-002", self.prs)                      # 自由库存 700 > 需求 400

    def test_dates_and_lead_time(self) -> None:
        sf001 = self.orders["SF-001"]
        self.assertEqual(sf001["required_date"], "2026-10-26")
        self.assertEqual(sf001["start_date"], "2026-10-25")
        self.assertEqual(self.prs["RM-001"]["required_date"], "2026-10-25")
        self.assertEqual(self.prs["RM-001"]["release_date"], "2026-10-24")
        self.assertEqual(self.prs["RM-010"]["required_date"], "2026-10-26")
        self.assertEqual(self.prs["RM-010"]["release_date"], "2026-10-25")

    def test_shelf_life_exceptions(self) -> None:
        exceptions = repo.list_exceptions(self.conn, self.run_id)
        messages = " ".join(row["message"] for row in exceptions)
        self.assertIn("牛腩", messages)
        self.assertIn("青菜", messages)
        self.assertFalse(
            any(row["exception_type"] == "LEAD_TIME_SHORTAGE" for row in exceptions),
            "种子场景不应出现提前期不足",
        )

    def test_multi_level_aggregation(self) -> None:
        # 两张要货单的鲜湿米粉需求必须合并成一张 95kg 生产订单
        sf_orders = [
            order for order in repo.list_production_orders(self.conn, run_id=self.run_id)
            if order["material_id"] == "SF-001"
        ]
        self.assertEqual(len(sf_orders), 1)
        self.assertAlmostEqual(sf_orders[0]["qty_planned"], 95)
        # 下一层大米需求按 95kg 展开，而不是按 102kg
        row = self.explosion[("RM-001", "2026-10-25")]
        self.assertAlmostEqual(row["gross_qty"], 100.249)

    def test_requisition_status_updated(self) -> None:
        for requisition in repo.list_requisitions(self.conn):
            self.assertEqual(requisition["status"], "PLANNED")
            self.assertEqual(requisition["mrp_run_id"], self.run_id)

    def test_second_run_has_no_demand(self) -> None:
        with self.assertRaises(NoDemandError):
            run_mrp(self.conn, run_date=RUN_DATE)


class TestLifecycle(BaseCase):
    def setUp(self) -> None:
        super().setUp()
        self.result = run_mrp(self.conn, run_date=RUN_DATE)
        self.run_id = self.result["run_id"]
        self.orders = {
            order["material_id"]: order
            for order in repo.list_production_orders(self.conn, run_id=self.run_id)
        }

    def _produce(self, material_id: str, action_date: str) -> dict:
        order = self.orders[material_id]
        service.release_order(self.conn, order["order_id"], action_date)
        service.issue_components(self.conn, order["order_id"], action_date)
        service.confirm_order(
            self.conn, order["order_id"], qty_good=order["qty_planned"], on_date=action_date
        )
        return service.goods_receipt(
            self.conn,
            order["order_id"],
            production_date=action_date,
            on_date=action_date,
        )

    def test_full_flow(self) -> None:
        stubs.receive_purchase_requisitions(self.conn, self.run_id, "2026-10-25")
        for material_id in ("SF-001", "SF-002", "SF-003"):
            receipt = self._produce(material_id, "2026-10-25")
            self.assertEqual(receipt["status"], "COMPLETED")

        fg_receipt = self._produce("FG-001", "2026-10-26")
        self.assertEqual(fg_receipt["status"], "COMPLETED")
        self.assertEqual(fg_receipt["expiry_date"], "2026-10-29")
        self.assertAlmostEqual(repo.total_stock(self.conn, "FG-001"), 280)

        # 所有订单完成
        for order in repo.list_production_orders(self.conn, run_id=self.run_id):
            if order["material_id"] == "FG-001":
                self.assertEqual(order["status"], "COMPLETED")

        vouchers = stubs.post_pending_events(self.conn)
        # 8 张采购收货 + 4 张生产订单 × 3 张（发料/报工/入库）
        self.assertEqual(len(vouchers), 8 + 4 * 3)
        trial = stubs.trial_balance(self.conn)
        self.assertAlmostEqual(
            sum(row["debit"] for row in trial), sum(row["credit"] for row in trial), places=2
        )

    def test_insufficient_stock_rolls_back(self) -> None:
        # 不做 MM 收货，直接发料 -> 大米缺口；事务回滚，库存与订单状态不变
        order = self.orders["SF-001"]
        detail = service.release_order(self.conn, order["order_id"], "2026-10-25")
        self.assertEqual(detail["availability_status"], "PARTIAL")
        self.assertEqual(detail["shortages"][0]["material_id"], "RM-001")
        with self.assertRaises(service.InsufficientStockError) as ctx:
            service.issue_components(self.conn, order["order_id"], "2026-10-25")
        self.assertIn("大米", str(ctx.exception))
        self.assertAlmostEqual(repo.total_stock(self.conn, "RM-001"), 80)
        refreshed = service.get_order_detail(self.conn, order["order_id"])
        self.assertEqual(refreshed["status"], "RELEASED")
        self.assertAlmostEqual(refreshed["components"][0]["qty_issued"], 0)

    def test_illegal_state_transition(self) -> None:
        order = self.orders["SF-001"]
        with self.assertRaises(service.OrderStateError):
            service.issue_components(self.conn, order["order_id"], "2026-10-25")
        with self.assertRaises(service.OrderStateError):
            service.goods_receipt(self.conn, order["order_id"], production_date="2026-10-25")

    def test_submit_requisition(self) -> None:
        requisition_id = service.submit_requisition(
            self.conn,
            store_id="ST-B",
            items=[("FG-002", 20)],
            required_date="2026-10-28",
            request_date="2026-10-24",
        )
        requisition = repo.get_requisition(self.conn, requisition_id)
        self.assertEqual(requisition["status"], "SUBMITTED")
        self.assertEqual(requisition["items"][0]["qty"], 20)


class TestApi(BaseCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db_path = TMP_DIR / "api_test.db"
        _remove_db(cls.db_path)
        conn = init_and_seed(cls.db_path, reset=True)
        conn.close()
        api.Handler.db_path = str(cls.db_path)
        cls.server = api.ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        _remove_db(cls.db_path)

    def setUp(self) -> None:  # API 测试使用类级数据库
        pass

    def tearDown(self) -> None:
        pass

    def _get(self, path: str) -> tuple[int, dict]:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def _post(self, path: str, payload: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_health_and_bom(self) -> None:
        status, body = self._get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["owner"], "郭晨阳")

        status, bom = self._get("/api/materials/FG-001/bom")
        self.assertEqual(status, 200)
        self.assertEqual(bom["bom_id"], "BOM-FG-001")
        self.assertEqual(len(bom["components"]), 6)

    def test_mrp_and_order_flow(self) -> None:
        status, result = self._post("/api/mrp/run", {"run_date": RUN_DATE})
        self.assertEqual(status, 201)
        self.assertEqual(result["stats"]["production_order_count"], 5)
        run_id = result["run_id"]

        status, orders = self._get(f"/api/production-orders?run_id={run_id}")
        self.assertEqual(status, 200)
        self.assertEqual(len(orders["items"]), 5)

        status, received = self._post("/api/demo/mm-receive", {"run_id": run_id, "on_date": "2026-10-25"})
        self.assertEqual(status, 201)
        self.assertEqual(received["received"], 8)

        order_id = next(
            item["order_id"] for item in orders["items"] if item["material_id"] == "SF-001"
        )
        status, detail = self._post(f"/api/production-orders/{order_id}/release", {"on_date": "2026-10-25"})
        self.assertEqual(status, 200)
        self.assertEqual(detail["availability_status"], "FULL")

        status, issued = self._post(f"/api/production-orders/{order_id}/issue", {"on_date": "2026-10-25"})
        self.assertEqual(status, 200)
        self.assertEqual(issued["status"], "IN_PROCESS")

        status, confirmed = self._post(
            f"/api/production-orders/{order_id}/confirm",
            {"qty_good": 95, "on_date": "2026-10-25"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(confirmed["status"], "CONFIRMED")

        status, receipt = self._post(
            f"/api/production-orders/{order_id}/goods-receipt",
            {"production_date": "2026-10-25", "on_date": "2026-10-25"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(receipt["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
