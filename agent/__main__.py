"""Run the inventory agent with an explicit human checkpoint."""
import argparse
import json
import os
import uuid
from pathlib import Path
from agent.models import ModelError, OpenAIModel, ScriptedModel, demo_actions
from agent.runner import run
from sandbox.setup import DEFAULT_DB


def approve(order, plan, unavailable):
    print("\nProposed sandbox order")
    for line in order.lines:
        print(f"  {line.sku}: {line.quantity} units at USD {line.unit_price_minor / 100:.2f} each")
    print(f"Total USD {order.total / 100:.2f}; remaining USD {plan.remaining_minor / 100:.2f}")
    print("Deferred for budget:", [item.sku for item in plan.deferred])
    print("Unavailable:", unavailable)
    return input("Type yes to approve this exact order: ").strip().lower() == "yes"


def load_local_settings(path):
    """Load the two supported KEY=value settings without overriding the shell."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return
    except (OSError, UnicodeError):
        raise ModelError("Could not read the project .env file as UTF-8") from None

    for line in lines:
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or name not in {"OPENAI_API_KEY", "OPENAI_MODEL"}:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] in ("'", '"') and value[-1] == value[0]:
            value = value[1:-1]
        if value:
            os.environ.setdefault(name, value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skus", nargs="+", default=["gloves", "tubes"])
    parser.add_argument("--budget-minor", type=int, default=3000)
    parser.add_argument("--max-actions", type=int, default=20)
    parser.add_argument("--model", help="Override the configured OpenAI model")
    parser.add_argument("--demo", action="store_true", help="Use scripted decisions without API calls")
    args = parser.parse_args()
    if not args.demo:
        try:
            load_local_settings(Path(__file__).resolve().parents[1] / ".env")
        except ModelError as error:
            parser.error(str(error))
        args.model = args.model or os.environ.get("OPENAI_MODEL")
        if not args.model:
            parser.error("Supply --model or set OPENAI_MODEL to a tool-capable model available to your account")
    trace = Path("traces") / f"{uuid.uuid4()}.jsonl"
    try:
        model = ScriptedModel(demo_actions(args.skus)) if args.demo else OpenAIModel(args.model)
        result = run(model, DEFAULT_DB, args.skus, args.budget_minor, approve, trace, args.max_actions)
    except (ValueError, ModelError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))
    print(f"Trace saved to {trace}")


if __name__ == "__main__":
    main()
