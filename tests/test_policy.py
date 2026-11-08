import unittest
from dataclasses import asdict

from agent.policy import ActionBudget, ActionLimitReached, Refill, plan_refills


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.gloves = Refill("gloves", 2, 2, 6, 600)
        self.tubes = Refill("tubes", 0, 0, 1, 2000)

    def test_priority_and_budget(self):
        plan = plan_refills([self.gloves, self.tubes], 3000)
        self.assertEqual(plan.selected, (self.gloves,))
        self.assertEqual(plan.deferred, (self.tubes,))
        self.assertEqual((plan.total_minor, plan.remaining_minor), (2400, 600))
        self.assertEqual(plan.reason, "ready_for_approval")

    def test_exact_budget(self):
        self.assertEqual(plan_refills([self.gloves], 2400).remaining_minor, 0)

    def test_zero_and_insufficient_budget(self):
        for budget in (0, 2399):
            plan = plan_refills([self.gloves], budget)
            self.assertEqual(plan.reason, "budget_blocked")
            self.assertEqual(plan.total_minor, 0)

    def test_nothing_needed(self):
        stocked = Refill("gloves", 7, 2, 6, 600)
        for items in ([], [stocked]):
            self.assertEqual(plan_refills(items, 3000).reason, "nothing_needed")

    def test_later_cheaper_item_can_fit(self):
        self.assertEqual(plan_refills([self.gloves, self.tubes], 2000).selected,
                         (self.tubes,))

    def test_invalid_money_and_duplicate_items(self):
        for budget in (-1, True, 2.5, "3000"):
            with self.assertRaises(ValueError):
                plan_refills([self.gloves], budget)
        with self.assertRaises(ValueError):
            plan_refills([self.gloves, self.gloves], 10000)
        with self.assertRaises(ValueError):
            Refill("gloves", 0, 2, 2, 600)
        with self.assertRaises(ValueError):
            Refill("gloves", 0, 2, 6, 0)

    def test_repeated_attempts_stop_and_resume_preserves_count(self):
        state = ActionBudget(limit=3)
        state = state.consume()
        state = ActionBudget(**asdict(state))  # Simulate saved approval state.
        state = state.consume()
        state = state.consume()
        for _ in range(5):
            with self.assertRaises(ActionLimitReached):
                state.consume()
        self.assertEqual(state.used, 3)

    def test_invalid_action_state(self):
        for values in ({"limit": 0}, {"used": -1}, {"limit": 2, "used": 3}):
            with self.assertRaises(ValueError):
                ActionBudget(**values)


if __name__ == "__main__":
    unittest.main()
