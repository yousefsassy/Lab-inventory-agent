import sqlite3
from contextlib import closing
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.tools import ApprovedOrder, InventoryTools, OrderLine
from sandbox.setup import initialize


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "lab.sqlite3"
        initialize(self.path)
        self.order = ApprovedOrder("request-1", 3000, (OrderLine("gloves", 4, 600),))
        self.tools = InventoryTools(self.path, self.order)

    def place(self):
        return self.tools.call("place_purchase_order", {"request_id": "request-1"})

    def test_reads_and_strict_inputs(self):
        self.assertEqual(self.tools.call("check_stock", {"sku": "gloves"})["on_hand"], 2)
        self.assertEqual(self.tools.call("get_supplier_offer", {"sku": "gloves"})["unit_price_minor"], 600)
        for args in ({}, {"sku": 1}, {"sku": " "}, {"sku": "missing"}, {"sku": "gloves", "extra": 1}):
            self.assertFalse(self.tools.call("check_stock", args)["ok"])
        self.assertFalse(self.tools.call("delete_all", {})["ok"])
        self.assertIn("duration_seconds", self.tools.trace[0])

    def test_approval_required(self):
        tools = InventoryTools(self.path)
        self.assertFalse(tools.call("place_purchase_order", {"request_id": "request-1"})["ok"])

    def test_retry_after_lost_response(self):
        self.place()  # Simulate losing the successful response after commit.
        receipt = self.place()
        self.assertTrue(receipt["ok"])
        self.assertEqual(self.tools.call("check_stock", {"sku": "gloves"})["pending"], 4)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)

    def test_concurrent_retry(self):
        def submit(_):
            return InventoryTools(self.path, self.order).call("place_purchase_order", {"request_id": "request-1"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertTrue(results[0]["ok"])
        self.assertEqual(self.tools.call("check_stock", {"sku": "gloves"})["pending"], 4)

    def test_partial_failure_rolls_back(self):
        self.tools.approved_order = ApprovedOrder("request-1", 5000, (
            OrderLine("gloves", 4, 600), OrderLine("missing", 1, 100)))
        self.assertFalse(self.place()["ok"])
        self.assertEqual(self.tools.call("check_stock", {"sku": "gloves"})["pending"], 0)
        self.tools.approved_order = self.order
        self.assertTrue(self.place()["ok"])

    def test_price_change_and_budget(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE items SET price = 700 WHERE sku = 'gloves'")
        self.assertFalse(self.place()["ok"])
        with self.assertRaises(ValueError):
            ApprovedOrder("x", 2399, self.order.lines)
        with self.assertRaises(ValueError):
            OrderLine("gloves", True, 600)

    def test_changed_request_and_pending_stock(self):
        self.assertTrue(self.place()["ok"])
        self.tools.approved_order = ApprovedOrder("request-1", 4000, self.order.lines)
        self.assertFalse(self.place()["ok"])
        self.tools.approved_order = ApprovedOrder("request-2", 3000, self.order.lines)
        self.assertFalse(self.tools.call("place_purchase_order", {"request_id": "request-2"})["ok"])

    def test_reset_and_missing_database(self):
        self.place()
        initialize(self.path)
        self.assertEqual(self.tools.call("check_stock", {"sku": "gloves"})["pending"], 4)
        initialize(self.path, reset=True)
        self.assertEqual(self.tools.call("check_stock", {"sku": "gloves"})["pending"], 0)
        result = InventoryTools(self.path.parent / "absent.sqlite3").call("check_stock", {"sku": "gloves"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["retryable"])


if __name__ == "__main__":
    unittest.main()
