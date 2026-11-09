"""Run every trajectory scenario and report path-level metrics.

Usage:
    python -m eval.run_eval

Each scenario starts from a fresh sandbox database, so writes never leak
between runs. The model is scripted, which makes the harness deterministic and
keeps it offline; point the same harness at a live model later.
"""

import json
import sqlite3
import statistics
import tempfile
import time
from contextlib import closing
from pathlib import Path

from agent.models import ScriptedModel
from agent.runner import run
from sandbox.setup import initialize

from eval.scenarios import SCENARIOS


def _events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _action_matches(expected, actual):
    return expected == actual


def _tool_matches(expected, actual):
    if expected["tool"] != actual.get("tool"):
        return False
    if expected.get("input") is None:
        return True
    return expected["input"] == actual.get("input")


def run_scenario(scenario):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        database = root / "eval.sqlite3"
        trace = root / "trace.jsonl"
        initialize(database)
        started = time.perf_counter()
        result = run(
            ScriptedModel(scenario.actions),
            database,
            list(scenario.skus),
            scenario.budget_minor,
            lambda *args: scenario.approve,
            trace,
            scenario.max_actions,
        )
        elapsed = time.perf_counter() - started
        events = _events(trace)
        with closing(sqlite3.connect(database)) as connection:
            orders, total = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(total), 0) FROM orders"
            ).fetchone()

    actions = [event["output"] for event in events if event["type"] == "model"]
    tool_calls = [event for event in events if event["type"] == "tool"]

    matched_actions = sum(
        1 for expected, actual in zip(scenario.expected_actions, actions)
        if _action_matches(expected, actual)
    )
    matched_tools = sum(
        1 for expected, actual in zip(scenario.expected_tools, tool_calls)
        if _tool_matches(expected, actual)
    )
    step_accuracy = matched_actions / max(len(scenario.expected_actions), len(actions))
    tool_precision = matched_tools / len(tool_calls) if tool_calls else 1.0
    success = (
        result["status"] == scenario.expected_status
        and orders == scenario.expected_orders
        and total == scenario.expected_total_minor
    )
    return {
        "name": scenario.name,
        "status": result["status"],
        "success": success,
        "step_accuracy": step_accuracy,
        "tool_call_precision": tool_precision,
        "wasted_steps": max(0, len(actions) - len(scenario.expected_actions)),
        "actions_used": result["actions_used"],
        "orders": orders,
        "total_minor": total,
        "duration_seconds": elapsed,
    }


def main():
    runs = [run_scenario(scenario) for scenario in SCENARIOS]
    durations = sorted(run["duration_seconds"] for run in runs)
    p50 = statistics.median(durations)
    p95 = durations[min(len(durations) - 1, int(0.95 * len(durations)))]

    print(f"{'scenario':38} {'status':16} {'ok':2} {'step':6} {'tool':6} {'waste':5} {'ms':6}")
    for run in runs:
        print(
            f"{run['name']:38} {run['status']:16} "
            f"{'yes' if run['success'] else 'NO ':2} "
            f"{run['step_accuracy']:6.2f} {run['tool_call_precision']:6.2f} "
            f"{run['wasted_steps']:5} {run['duration_seconds'] * 1000:6.1f}"
        )

    summary = {
        "scenarios": len(runs),
        "task_success_rate": sum(run["success"] for run in runs) / len(runs),
        "mean_step_accuracy": sum(run["step_accuracy"] for run in runs) / len(runs),
        "mean_tool_call_precision": sum(run["tool_call_precision"] for run in runs) / len(runs),
        "total_wasted_steps": sum(run["wasted_steps"] for run in runs),
        "p50_seconds": p50,
        "p95_seconds": p95,
    }
    print()
    print(json.dumps(summary, indent=2))
    return 0 if all(run["success"] for run in runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
