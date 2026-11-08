import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from agent.models import OpenAIModel, ScriptedModel, demo_actions, ModelError
from agent.runner import run
from sandbox.setup import initialize


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "test.sqlite3"
        initialize(self.db)

    def execute(self, approve, actions=None, limit=20, budget=3000):
        model = ScriptedModel(demo_actions(["gloves", "tubes"]) if actions is None else actions)
        return run(model, self.db, ["gloves", "tubes"], budget, approve,
                   self.root / "trace.jsonl", limit)

    def count(self):
        with closing(sqlite3.connect(self.db)) as db:
            return db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

    def test_approved_flow_and_trace(self):
        result = self.execute(lambda *args: True)
        self.assertEqual(result["status"], "ordered")
        self.assertEqual(result["receipt"]["total_minor"], 2400)
        self.assertEqual(self.count(), 1)
        events = [json.loads(line) for line in (self.root / "trace.jsonl").read_text().splitlines()]
        self.assertEqual(events[-3]["type"], "approval")
        self.assertEqual(events[-2]["tool"], "place_purchase_order")
        self.assertEqual(result["actions_used"], 11)

    def test_rejection(self):
        self.assertEqual(self.execute(lambda *args: False)["status"], "rejected")
        self.assertEqual(self.count(), 0)

    def test_approval_does_not_reset_limit(self):
        self.assertEqual(self.execute(lambda *args: True, limit=10)["status"], "action_limit")
        self.assertEqual(self.count(), 0)

    def test_repeating_model_stops(self):
        actions = [{"tool": "check_stock", "arguments": {"sku": "gloves"}}] * 50
        self.assertEqual(self.execute(lambda *args: True, actions, 6)["actions_used"], 6)
        self.assertEqual(self.count(), 0)

    def test_price_changes_during_approval(self):
        def approve(*args):
            with closing(sqlite3.connect(self.db)) as db, db:
                db.execute("UPDATE items SET price = 700 WHERE sku = 'gloves'")
            return True
        self.assertEqual(self.execute(approve)["status"], "write_failed")
        self.assertEqual(self.count(), 0)

    def test_model_cannot_bypass_checkpoint(self):
        actions = [{"tool": "place_purchase_order", "arguments": {"request_id": "fake"}}] * 10
        self.assertEqual(self.execute(lambda *args: True, actions, 4)["status"], "action_limit")
        self.assertEqual(self.count(), 0)

    def test_zero_budget(self):
        def unexpected(*args):
            self.fail("Zero-budget request should not ask for approval")
        self.assertEqual(self.execute(unexpected, budget=0)["status"], "budget_blocked")
        self.assertEqual(self.count(), 0)

    def test_cancellation(self):
        def cancelled(*args):
            raise EOFError()
        self.assertEqual(self.execute(cancelled)["status"], "cancelled")
        self.assertEqual(self.count(), 0)

    def test_malformed_action_recovers(self):
        actions = [None, {"tool": "check_stock", "arguments": {"sku": []}}] + list(demo_actions(["gloves", "tubes"]))
        self.assertEqual(self.execute(lambda *args: True, actions)["status"], "ordered")

    def test_openai_adapter(self):
        response = {"output": [{"type": "function_call", "name": "check_stock", "arguments": '{"sku":"gloves"}'}], "usage": {"input_tokens": 10}}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-placeholder"}):
            model = OpenAIModel("test-model")
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as send:
            self.assertEqual(model.next_action([])["arguments"], {"sku": "gloves"})
            self.assertEqual(send.call_args.kwargs["timeout"], 30)
            payload = json.loads(send.call_args.args[0].data)
            self.assertFalse(payload["parallel_tool_calls"])
            self.assertFalse(payload["store"])
        with patch("urllib.request.urlopen", side_effect=TimeoutError()):
            with self.assertRaises(ModelError):
                model.next_action([])

    def test_model_failure_recorded(self):
        model = ScriptedModel([])
        with patch.object(model, "next_action", side_effect=ModelError("Timed out")):
            result = run(model, self.db, ["gloves"], 3000, lambda *args: True, self.root / "error.jsonl")
        self.assertEqual(result["status"], "model_error")
        self.assertEqual(self.count(), 0)
