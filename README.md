# Lab Inventory Agent

A command-line agent that checks lab inventory, proposes refills within a
purchase budget, and records an order in a local SQLite sandbox after a human
approves the exact proposal. The model chooses which tools to call. Python
calculates quantities and totals, enforces the budget, and controls writes.

## Quick start

Use Python 3.12 from the repository root. The project uses the Python standard
library and SQLite, so there are no packages to install.

```sh
python -m sandbox.setup
python -m agent --demo --skus gloves tubes --budget-minor 3000
```

Demo mode uses scripted tool choices and needs no API key. On a fresh sandbox,
the command proposes four boxes of gloves for **$24**, defers the **$20** tube
refill, and shows **$6** remaining. Type `yes` to create the sandbox order or
`no` to reject it. The sample inventory also includes `masks`, which start above
their reorder threshold. The SKU order sets purchasing priority.

The tube supplier note contains an injection attempt. `get_supplier_offer`
returns it as untrusted text; the host ignores any instructions inside it and
still applies the budget and the human checkpoint.

To clear sandbox orders and restore the original stock and offers, run
`python -m sandbox.setup --reset`.

## Run with OpenAI

Copy [.env.example](.env.example) to `.env` in the repository root. Replace the
key placeholder privately and save the file. The sample model is
`gpt-4.1-mini`. Change it if your API project uses a different Responses API
model with function calling.

```sh
python -m agent --skus gloves tubes --budget-minor 3000
```

`--budget-minor` is the budget in cents. The default action limit is 20 and can
be set with `--max-actions`. Use `--help` to see all options. Shell variables
take precedence over `.env`, and `--model` overrides the configured model.
Keep real credentials in the ignored `.env` file or in your shell environment.

## How it works

1. The model calls `check_stock` and `get_supplier_offer` for the requested SKUs.
   The offer includes a free-text supplier note. That note is data, not an
   instruction: the host never executes anything it says.
2. The planner considers eligible refills in priority order. It uses on-hand
   stock plus pending deliveries, and only proposes full refills that fit
   both supplier availability and the remaining budget.
3. The terminal shows quantities, prices, the total, and any deferred items.
   The runner accepts approval only through this checkpoint.
4. `place_purchase_order` checks the approved order against current stock and
   prices. One SQLite transaction saves the order, reserves supplier stock,
   and records the pending delivery.
5. The run writes a JSON Lines trace under `traces/` with model actions and
   token usage when available, tool calls and timings, the approval decision,
   and the outcome.

The planner considers each requested SKU once and deducts only costs that fit,
so the remaining budget stays nonnegative. Each model turn and tool or proposal
attempt also consumes an action. Approval preserves that count, and the runner
stops before exceeding the limit. The write tool returns the original receipt
when the same approved request ID is retried. It rejects changed orders under
that ID. If the quote or stock changes during approval, start a fresh run to
review a new proposal.

## Project layout

| Path | Purpose |
| --- | --- |
| [agent/policy.py](agent/policy.py) | Refill calculations, purchase budget, and action limit |
| [agent/tools.py](agent/tools.py) | Tool schemas and SQLite reads and writes |
| [agent/runner.py](agent/runner.py) | Tool loop, approval checkpoint, and tracing |
| [agent/models.py](agent/models.py) | OpenAI and scripted model adapters |
| [agent/__main__.py](agent/__main__.py) | Terminal interface and local settings |
| [sandbox/](sandbox/) | Sample inventory and database initialization and reset |
| [eval/](eval/) | Trajectory scenarios and the path-level eval harness |
| [errors.md](errors.md) | Error taxonomy mapped to the scenario and test that proves each class |
| [tests/](tests/) | Policy, database, runner, injection, and configuration tests |

## Tests and project status

```sh
python -m unittest discover -s tests -v
python -m eval.run_eval
```

The current suite covers budget boundaries, action exhaustion, approval and
rejection, stale prices, transaction rollback, duplicate writes, injection
handling, and local configuration. The automated OpenAI adapter test uses a
simulated response. The error classes and their proofs are listed in
[errors.md](errors.md).

The eval harness currently runs fifteen deterministic trajectory scenarios from
a fresh sandbox each time and reports task success, step accuracy, tool-call
precision, wasted steps, and p50/p95 time. The scenarios cover the happy path,
budget and rejection stops, wasted reads, wrong and malformed actions, a model
timeout, action-limit exhaustion, supplier unavailability, stock changing
during approval, and five injection attempts. The remaining course work is
expanding to 30+ scenarios, adding the cost model, and writing the report and
postmortem.

## Contributions

The initial implementation covers the purchasing policy, SQLite sandbox and
tools, terminal workflow, model adapters, tracing, and automated tests.
Individual contribution breakdowns will be added as the course project progresses.
