"""Independent numeric/database expectations for the six new policy trajectories."""
import json
import tempfile
import unittest
from pathlib import Path

from eval.run_eval import run_scenario
from eval.scenarios import SCENARIOS


class PolicyScenarioTests(unittest.TestCase):
    def verify(self, name, expected_lines, expected_total, deltas):
        scenario = next(s for s in SCENARIOS if s.name == name)
        with tempfile.TemporaryDirectory() as directory:
            result = run_scenario(scenario, directory)
            state = json.loads((Path(directory) / f'{name}.state.json').read_text())
            events = [json.loads(line) for line in (Path(directory) / f'{name}.jsonl').read_text().splitlines()]
        self.assertTrue(result['success'], result)
        self.assertEqual(result['total_minor'], expected_total)
        actual = [line for order in state['after']['orders'].values() for line in json.loads(order['payload'])['lines']]
        self.assertEqual(actual, expected_lines)
        self.assertEqual(result['trajectory_accuracy'], 1)
        self.assertEqual(result['tool_call_precision'], 1)
        self.assertEqual(result['wasted_steps'], 0)
        self.assertTrue(scenario.purpose)
        approvals = [e for e in events if e['type'] == 'approval']
        self.assertEqual(len(approvals), int(bool(expected_lines)))
        for sku, before in state['before']['items'].items():
            after = state['after']['items'][sku]
            quantity = deltas.get(sku, 0)
            self.assertEqual(after['on_hand'], before['on_hand'])
            self.assertEqual(after['pending'] - before['pending'], quantity)
            self.assertEqual(before['available'] - after['available'], quantity)
        return state

    def test_exact_budget(self):
        self.verify('exact_budget_boundary', [{'sku':'gloves','quantity':4,'unit_price_minor':600}], 2400, {'gloves':4})

    def test_reversed_priority(self):
        self.verify('reversed_sku_priority', [{'sku':'tubes','quantity':1,'unit_price_minor':2000}], 2000, {'tubes':1})

    def test_pending_stock(self):
        state = self.verify('pending_delivery_prevents_refill', [], 0, {})
        self.assertEqual(state['before']['items']['gloves']['pending'], 4)
        self.assertEqual(state['after']['items']['gloves']['on_hand'], 2)

    def test_multiple_affordable_items(self):
        self.verify('multiple_affordable_refills', [{'sku':'gloves','quantity':4,'unit_price_minor':600},
                    {'sku':'tubes','quantity':1,'unit_price_minor':2000}], 4400, {'gloves':4,'tubes':1})

    def test_partial_availability(self):
        state = self.verify('partial_supplier_availability', [{'sku':'tubes','quantity':1,'unit_price_minor':2000}], 2000, {'tubes':1})
        self.assertEqual(state['after']['items']['gloves']['available'], 3)

    def test_nothing_needed(self):
        self.verify('no_replenishment_needed', [], 0, {})


if __name__ == '__main__':
    unittest.main()
