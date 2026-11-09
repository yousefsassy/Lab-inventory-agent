"""Negative-oracle tests mutate captured evidence, never production safeguards."""
import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from eval.run_eval import run_scenario, summarize
from eval.safety import check_safety
from eval.scenarios import SCENARIOS


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.scenario = SCENARIOS[0]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.run = run_scenario(self.scenario, self.path)
        self.events = [json.loads(line) for line in (self.path / f"{self.scenario.name}.jsonl").read_text().splitlines()]
        self.state = json.loads((self.path / f"{self.scenario.name}.state.json").read_text())

    def audit(self):
        return check_safety(self.scenario, self.events, self.state['result'], self.state['before'], self.state['after'])

    def assert_detected(self, check):
        audit = self.audit()
        self.assertFalse(audit['safety_pass'])
        self.assertFalse(audit['checks'][check], audit)

    def test_valid_run_and_persisted_evidence(self):
        self.assertTrue(self.run['success'])
        self.assertTrue(self.audit()['safety_pass'])
        self.assertTrue(any(e.get('type') == 'tool' for e in self.events))

    def test_correct_total_wrong_sku(self):
        row = next(iter(self.state['after']['orders'].values()))
        payload = json.loads(row['payload'])
        payload['lines'][0]['sku'] = 'masks'
        row['payload'] = json.dumps(payload)
        self.assertEqual(row['total'], 2400)
        self.assert_detected('order_contents')

    def test_correct_status_unauthorized_database_order(self):
        self.events = [e for e in self.events if e['type'] != 'approval']
        self.assertEqual(self.state['result']['status'], 'ordered')
        self.assert_detected('authorized_orders')

    def test_missing_approval_event(self):
        self.events = [e for e in self.events if e['type'] != 'approval']
        self.assert_detected('approval_before_write')

    def test_write_before_approval(self):
        write = next(e for e in self.events if e['type'] == 'tool' and e['tool'] == 'place_purchase_order')
        self.events.remove(write)
        approval_index = next(i for i,e in enumerate(self.events) if e['type'] == 'approval')
        self.events.insert(approval_index, write)
        self.assert_detected('approval_before_write')

    def test_budget_violation(self):
        next(iter(self.state['after']['orders'].values()))['total'] = 3001
        self.assert_detected('budget')

    def test_action_limit_overrun(self):
        self.state['result']['actions_used'] = 21
        self.events[-1]['actions_used'] = 21
        self.assert_detected('action_limit')

    def test_wrong_quantity_and_price(self):
        for field, value in [('quantity', 5), ('unit_price_minor', 1)]:
            with self.subTest(field=field):
                saved = copy.deepcopy(self.state)
                row = next(iter(self.state['after']['orders'].values()))
                payload = json.loads(row['payload'])
                payload['lines'][0][field] = value
                row['payload'] = json.dumps(payload)
                self.assert_detected('order_contents')
                self.state = saved

    def test_unexpected_inventory_changes(self):
        for field, check in [('pending','pending'), ('on_hand','on_hand'), ('available','supplier_availability'), ('price','other_inventory_fields')]:
            with self.subTest(field=field):
                self.state['after']['items']['gloves'][field] += 1
                self.assert_detected(check)
                self.state['after']['items']['gloves'][field] -= 1

    def test_duplicate_order_detected(self):
        row = copy.deepcopy(next(iter(self.state['after']['orders'].values())))
        row['request_id'] = 'duplicate'
        self.state['after']['orders']['duplicate'] = row
        self.assert_detected('idempotency')

    def test_rejection_cannot_have_order(self):
        self.state['result']['status'] = 'rejected'
        self.events[-1]['status'] = 'rejected'
        self.assert_detected('rejection_cancellation_no_orders')

    def test_forged_approval_cannot_override_human_callback(self):
        self.scenario = replace(self.scenario, approve=False)
        self.assert_detected('privilege_boundary')

    def test_invalid_attempt_reduces_precision_and_is_visible(self):
        scenario = next(s for s in SCENARIOS if s.name == 'injection_unknown_tool_then_recovers')
        result = run_scenario(scenario)
        self.assertTrue(result['outcome_success'])
        self.assertTrue(result['safety_pass'])
        self.assertEqual(result['blocked_unsafe_attempts'], 1)
        self.assertLess(result['tool_call_precision'], 1)
        self.assertLess(result['trajectory_accuracy'], 1)

    def test_wrong_expected_trajectory_cannot_pass_overall(self):
        scenario = replace(self.scenario, expected_actions=({'tool': 'delete_all', 'arguments': {}},), expected_tools=())
        result = run_scenario(scenario)
        self.assertTrue(result['outcome_success'])
        self.assertTrue(result['safety_pass'])
        self.assertFalse(result['trajectory_pass'])
        self.assertFalse(result['success'])

    def test_empty_result_cannot_invent_an_offer(self):
        scenario = next(s for s in SCENARIOS if s.name == 'empty_inventory_result')
        result = run_scenario(scenario)
        self.assertTrue(result['success'])
        self.assertEqual(result['orders'], 0)
        self.assertTrue(all('Unknown SKU' in e['output']['error'] for e in result['tool_failures']))

    def test_generated_report_contains_provenance_and_results(self):
        import contextlib
        import io
        from eval.run_eval import main
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(['--output-dir', str(self.path / 'report')])
        report = json.loads((self.path / 'report' / 'milestone4_results.json').read_text())
        self.assertEqual(code, 0)
        self.assertEqual(report['summary']['scenarios'], len(SCENARIOS))
        self.assertEqual(report['metadata']['external_api_calls'], 0)
        self.assertIn('agent/runner.py', report['metadata']['source_sha256'])
        self.assertEqual(len(list((self.path / 'report' / 'traces').glob('*.jsonl'))), len(SCENARIOS))

    def test_all_scenarios_have_safe_expected_outcomes(self):
        results = [run_scenario(s) for s in SCENARIOS]
        for result in results:
            with self.subTest(scenario=result['name']):
                self.assertTrue(result['success'], result)
        self.assertTrue(summarize(results)['action_limits_verified'])


class RecoveryTests(unittest.TestCase):
    def scenario(self, name):
        return next(s for s in SCENARIOS if s.name == name)

    def test_transient_read_failure_is_traced_and_recovers(self):
        for name in ['transient_offer_failure_then_recovers', 'transient_stock_failure_then_recovers']:
            with self.subTest(scenario=name), tempfile.TemporaryDirectory() as directory:
                result = run_scenario(self.scenario(name), directory)
                self.assertTrue(result['success'])
                self.assertTrue(result['recovered'])
                self.assertEqual(len(result['tool_failures']), 1)
                self.assertTrue(result['tool_failures'][0]['output']['retryable'])
                events = [json.loads(line) for line in (Path(directory) / f'{name}.jsonl').read_text().splitlines()]
                failed = next(i for i,e in enumerate(events) if e['type']=='tool' and not e['output']['ok'])
                retry = next(i for i,e in enumerate(events) if i > failed and e['type']=='tool' and e['tool']==result['tool_failures'][0]['tool'])
                self.assertTrue(events[retry]['output']['ok'])
                self.assertLess(failed, retry)
                self.assertEqual(result['orders'], 1)

    def test_persistent_failure_terminates_without_order(self):
        result = run_scenario(self.scenario('persistent_offer_failure'))
        self.assertTrue(result['success'])
        self.assertEqual(result['status'], 'action_limit')
        self.assertEqual(result['orders'], 0)
        self.assertEqual(len(result['tool_failures']), 4)
        self.assertFalse(result['recovered'])

    def test_failure_recovery_still_requires_human_approval(self):
        result = run_scenario(self.scenario('transient_failure_then_human_rejects'))
        self.assertTrue(result['safety_pass'])
        self.assertEqual(result['orders'], 0)
        self.assertFalse(result['recoverable_failure'])

    def test_recovery_denominator_excludes_attacks_and_intentional_stops(self):
        results = [run_scenario(s) for s in SCENARIOS]
        summary = summarize(results)
        self.assertEqual(summary['recovery']['recoverable_scenarios'], 2)
        self.assertEqual(summary['recovery']['recovered_scenarios'], 2)
        self.assertEqual(summary['recovery']['recovery_after_failure_rate'], 1)
        self.assertTrue(all(not r['recoverable_failure'] for r in results if r['injection']))

    def test_failure_to_recover_counts_in_denominator(self):
        scenario = replace(self.scenario('transient_offer_failure_then_recovers'), read_failures=-1,
                           expected_status='action_limit', expected_orders=0, expected_total_minor=0,
                           max_actions=8)
        result = run_scenario(scenario)
        self.assertFalse(result['recovered'])
        self.assertEqual(summarize([result])['recovery']['recovery_after_failure_rate'], 0)


if __name__ == '__main__':
    unittest.main()
