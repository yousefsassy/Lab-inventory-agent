"""Bounded execution, trusted planning, approval, and persistent traces."""
import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from agent.models import ModelError
from agent.policy import ActionBudget, ActionLimitReached, Refill, plan_refills, require_integer
from agent.tools import ApprovedOrder, InventoryTools, OrderLine


def run(model, database, skus, budget_minor, approve, trace_path, max_actions=20):
    require_integer("budget_minor", budget_minor)
    if not skus or any(type(sku) is not str or not sku.strip() for sku in skus):
        raise ValueError("Provide nonempty SKU strings")
    if len(set(skus)) != len(skus):
        raise ValueError("Duplicate SKUs are not allowed")
    state = ActionBudget(max_actions)
    tools = InventoryTools(database)
    stock, offers = {}, {}
    history = [{"request": {"skus": skus, "budget_minor": budget_minor}}]
    request_id = str(uuid.uuid4())
    path = Path(trace_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    started_run = time.perf_counter()
    with path.open("x", encoding="utf-8") as trace:
        def record(event):
            trace.write(json.dumps(event) + "\n")
            trace.flush()

        def finish(status, **details):
            result = {"status": status, "actions_used": state.used, "request_id": request_id, **details}
            record({"type": "final", "duration_seconds": time.perf_counter() - started_run, **result})
            return result

        record({"type": "request", "request_id": request_id, **history[0]})
        try:
            while True:
                state = state.consume()
                started = time.perf_counter()
                action = model.next_action(history)
                record({"type": "model", "input": history.copy(), "output": action,
                        "usage": getattr(model, "usage", {}), "duration_seconds": time.perf_counter() - started})
                state = state.consume()
                if type(action) is not dict or set(action) != {"tool", "arguments"} or type(action.get("tool")) is not str:
                    result = {"ok": False, "error": "Return tool and arguments fields"}
                elif action["tool"] == "propose_order":
                    if action["arguments"] != {}:
                        result = {"ok": False, "error": "Proposal takes no arguments"}
                    elif set(stock) != set(skus) or set(offers) != set(skus):
                        result = {"ok": False, "error": "Read stock and offers for every requested SKU first"}
                    else:
                        refills = [Refill(sku, stock[sku]["on_hand"] + stock[sku]["pending"],
                                          stock[sku]["threshold"], stock[sku]["target"],
                                          offers[sku]["unit_price_minor"]) for sku in skus]
                        available = [item for item in refills if item.quantity <= offers[item.sku]["available"]]
                        unavailable = [item.sku for item in refills if item not in available]
                        plan = plan_refills(available, budget_minor)
                        record({"type": "proposal", **asdict(plan), "unavailable": unavailable})
                        if not plan.selected:
                            status = "supplier_unavailable" if unavailable and plan.reason == "nothing_needed" else plan.reason
                            return finish(status, unavailable=unavailable)
                        order = ApprovedOrder(request_id, budget_minor, tuple(
                            OrderLine(item.sku, item.quantity, item.unit_price_minor) for item in plan.selected))
                        accepted = approve(order, plan, unavailable) is True
                        record({"type": "approval", "accepted": accepted, "order": asdict(order)})
                        if not accepted:
                            return finish("rejected")
                        tools.approved_order = order
                        state = state.consume()
                        receipt = tools.call("place_purchase_order", {"request_id": request_id})
                        record({"type": "tool", **tools.trace[-1]})
                        return finish("ordered" if receipt["ok"] else "write_failed", receipt=receipt)
                elif action["tool"] in ("check_stock", "get_supplier_offer"):
                    args = action["arguments"]
                    if type(args) is not dict or args.get("sku") not in skus:
                        result = {"ok": False, "error": "Choose a SKU from this request"}
                    else:
                        result = tools.call(action["tool"], args)
                        record({"type": "tool", **tools.trace[-1]})
                        if result["ok"]:
                            cache = stock if action["tool"] == "check_stock" else offers
                            cache[args["sku"]] = result
                else:
                    result = {"ok": False, "error": "Choose a read tool or propose_order; writes require the checkpoint"}
                history.append({"action": action, "observation": result})
                record({"type": "observation", **history[-1]})
        except ActionLimitReached:
            return finish("action_limit")
        except (EOFError, KeyboardInterrupt):
            return finish("cancelled")
        except ModelError as error:
            return finish("model_error", error=str(error))
