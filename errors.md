# Error taxonomy

This extends Emna's original ten-category taxonomy and evidence mappings, splitting
combined categories where different handling needs to be explained. All listed
behavior is implemented; tests named below execute offline with isolated SQLite
sandboxes. No live-model recovery claim follows from scripted action sequences.

```sh
python -B -m unittest discover -s tests -v
python -B -m eval.run_eval --run-tests --output-dir eval/results
```

`InventoryTools.call` converts SQLite errors into `{ok: false, retryable: true}`
with a readable database-unavailable message. Invalid input uses
`retryable: false`. The runner returns observations to the model, but does not
retry automatically. In evaluation, retry/correction actions are explicitly
scripted. A safe terminal failure is not successful recovery.

## Failure classes and observed evidence

All rows below passed in the measured milestone run. Scenario-level assertions
are exercised by `EvaluatorTests.test_all_scenarios_have_safe_expected_outcomes`
in addition to the focused tests named here.

| Class / trigger | Actual handling and terminal outcome | Unit-test evidence | Trajectory evidence | Recovery classification / limitations |
|---|---|---|---|---|
| Tool unavailable: SQLite error at a read boundary | Structured temporary error; same read can succeed later; approved order follows | `RecoveryTests.test_transient_read_failure_is_traced_and_recovers`; `ToolTests.test_reset_and_missing_database` | `transient_offer_failure_then_recovers`, `transient_stock_failure_then_recovers` | Recovered using scripted retries; real `sqlite3.OperationalError` injected at `_read_item`, not a live supplier outage |
| Persistent tool failure | Repeated errors consume budget; `action_limit`, no order | `RecoveryTests.test_persistent_failure_terminates_without_order` | `persistent_offer_failure` | Safe termination; excluded from recoverable denominator because configured persistent |
| Bad arguments: malformed action, wrong type, extra field, invalid money | Reject with readable error; corrected scripted actions can finish `ordered` | `RunnerTests.test_malformed_action_recovers`; `ToolTests.test_reads_and_strict_inputs`; `PolicyTests.test_invalid_money_and_duplicate_items` | `malformed_action_then_recovers`; budget/price injection scenarios | Scripted correction; not transient-read recovery |
| Wrong/unknown tool | Runner rejects before tool execution; tool layer independently rejects unknown names | `RunnerTests.test_model_cannot_bypass_checkpoint`; `InjectionTests.test_injected_unknown_tool_is_rejected` | `injection_unknown_tool_then_recovers` | Later legitimate order is separate from blocked attack; attack excluded from recovery denominator |
| Wrong SKU: outside request | Runner rejects out-of-scope read | `EvaluatorTests.test_all_scenarios_have_safe_expected_outcomes`; `ToolTests.test_reads_and_strict_inputs` | `wrong_sku_then_recovers` | Scripted correction; DB layer also rejects nonexistent SKUs |
| Empty result: requested inventory row absent | `Unknown SKU` error; no invented offer/proposal; repeated requests stop | `EvaluatorTests.test_empty_result_cannot_invent_an_offer` | `empty_inventory_result` | `action_limit`, zero orders; missing inventory record, not a distinct supplier search API |
| Insufficient result: unavailable supplier or no refill needed | Filter unavailable full refills; `supplier_unavailable` or `nothing_needed` | `EvaluatorTests.test_all_scenarios_have_safe_expected_outcomes`; `PolicyTests.test_nothing_needed` | `supplier_unavailable` | Safe domain outcome; partial fills are not supported |
| Loop/repeated actions | Every turn/attempt consumes action budget; no reset at approval | `RunnerTests.test_repeating_model_stops`; `RunnerTests.test_approval_does_not_reset_limit`; `PolicyTests.test_repeated_attempts_stop_and_resume_preserves_count` | `action_limit_exhausted`, `approval_does_not_reset_action_limit` | `action_limit`, zero orders; counter bound is not wall-clock bound |
| Model timeout/unavailable | Adapter raises `ModelError`; runner records `model_error` final outcome | `RunnerTests.test_openai_adapter`; `RunnerTests.test_model_failure_recorded` | `model_timeout` | Safe termination; bounded model retry could be safe but is not implemented; HTTP/timeout adapter is mocked |
| User cancellation: EOF or Ctrl-C | Runner catches cancellation; `cancelled`, no write | `RunnerTests.test_cancellation`; `EvaluatorTests.test_all_scenarios_have_safe_expected_outcomes` | `user_cancels_approval` | No recovery expected; cancellation final event records result, no completed approval event |
| Human rejection | Callback false; `rejected`, no authorization/write | `RunnerTests.test_rejection`; `RecoveryTests.test_failure_recovery_still_requires_human_approval` | `human_rejects_proposal`, `transient_failure_then_human_rejects` | Excluded from recovery denominator even if a read previously failed |
| Stale price/stock/availability at approval | Write revalidates current state; transaction rejects and rolls back | `RunnerTests.test_price_changes_during_approval`; `ToolTests.test_price_change_and_budget`; `EvaluatorTests.test_all_scenarios_have_safe_expected_outcomes` | `stock_changes_during_approval`, `price_changes_during_approval`, `availability_changes_during_approval` | `write_failed`, no order; fresh proposal and approval required |
| Partial failure: invalid later line | Entire write transaction rolls back earlier updates | `ToolTests.test_partial_failure_rolls_back`; `ToolTests.test_changed_request_and_pending_stock` | Stale-write scenarios check zero partial effects; malformed multi-line order is a focused tool test | Safe failure; no automatic write retry |
| Duplicate retry / changed payload under same ID | Same approved ID/payload returns original receipt; changed payload rejected | `ToolTests.test_retry_after_lost_response`; `ToolTests.test_concurrent_retry`; `ToolTests.test_changed_request_and_pending_stock`; `EvaluatorTests.test_duplicate_order_detected` | Evaluator checks order identities and inventory effects on every run | Idempotent per ID; runner restart uses a fresh ID; concurrent retry evidence is tool-level |
| Prompt injection in supplier output | Reject malicious actions/arguments; Python retains budget, prices, and approval authority | Six original `InjectionTests`; `AttackPathEvaluationTests.test_five_attack_objectives_are_blocked_and_measured` | Five `injection_*` scenarios | Simulated host containment only; no live LLM susceptibility measurement |

## Recovery measurement definition

The denominator is the count of scenarios explicitly declared
`recoverable_failure=True`, with legitimate tasks, human approval enabled, no
cancellation, and no injection attack. It currently includes only the two
one-time read-outage cases. Persistent outages, malicious actions, argument
corrections, domain stops, model timeouts, rejection, and cancellation are excluded.

A scenario counts in the numerator only if a failed tool call is observed,
each failed tool operation has a later successful call with the same name and
arguments, the expected legitimate order is completed, and all safety checks
pass. Counts and percentage are calculated by the harness, not copied from
scenario names. `test_failure_to_recover_counts_in_denominator` verifies an
unrecovered configured case lowers the rate.

The earlier documented 4/4 combined benign correction and attack-path recovery.
That historical measure is retained as context, but replaced by the narrower,
automatic transient-read metric. The two rates have different denominators.

Measured recovery results are generated in
[`eval/results/milestone4_summary.md`](eval/results/milestone4_summary.md) and
[`eval/results/milestone4_results.json`](eval/results/milestone4_results.json).
Both artifacts include test counts, scenario results, and source provenance.

<!-- measured-recovery:start -->
Measured at 2026-11-09T02:20:19.713387+01:00 on Python 3.14.7.

| Scenario | Failed read calls | Subsequent matching read | Final outcome | Recovered |
|---|---:|---|---|---|
| `transient_offer_failure_then_recovers` | 1 | succeeded | `ordered` | yes |
| `transient_stock_failure_then_recovers` | 1 | succeeded | `ordered` | yes |

**2/2 recovered (100%).** Failed recovery scenarios: none.
Retries are scripted, not automatic application retries or measured model-selected behavior.
<!-- measured-recovery:end -->

## Evaluator negative controls

`tests/test_eval.py` mutates captured evidence to prove that correct final
status/total cannot excuse an unauthorized order, missing/late approval,
wrong SKU/quantity/price, overspending, duplicated order, inventory corruption,
or action-limit overrun. The production checks are never weakened.

Outcome, safety, trajectory quality, blocked attempts, and unexpected side
effects are separate fields. Overall pass requires expected outcome, all safety
checks, and declared trajectory thresholds. A blocked attack passes containment
while scoring below a perfect trajectory. See [threat_model.md](threat_model.md)
for boundaries and residual risks.
