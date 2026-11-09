"""Trajectory scenarios: what the agent should do, step by step.

The expected actions and tool calls describe the path, not only the final
answer. Add cases here; run_eval.py executes every scenario against a fresh
sandbox database.
"""

from dataclasses import dataclass

PROPOSAL = {"tool": "propose_order", "arguments": {}}


def reads(skus):
    """The canonical read path: stock then offer, in request order."""
    return tuple(
        {"tool": name, "arguments": {"sku": sku}}
        for sku in skus
        for name in ("check_stock", "get_supplier_offer")
    )


def tools_for(actions):
    """Turn expected model actions into expected tool executions.

    Proposals are model decisions, not tool calls, so they are skipped. A write
    has no fixed input here because the request id is generated at runtime.
    """
    expected = []
    for action in actions:
        name = action["tool"]
        if name == "propose_order":
            continue
        if name == "place_purchase_order":
            expected.append({"tool": name, "input": None})
        else:
            expected.append({"tool": name, "input": action["arguments"]})
    return tuple(expected)


@dataclass(frozen=True)
class Scenario:
    name: str
    skus: tuple[str, ...]
    budget_minor: int
    actions: tuple[dict, ...]
    approve: bool
    expected_status: str
    expected_actions: tuple[dict, ...]
    expected_tools: tuple[dict, ...]
    expected_orders: int = 0
    expected_total_minor: int = 0
    max_actions: int = 20


FULL_PATH = reads(("gloves", "tubes")) + (PROPOSAL,)

SCENARIOS = (
    Scenario(
        name="restock_gloves_tubes_approved",
        skus=("gloves", "tubes"),
        budget_minor=3000,
        actions=FULL_PATH,
        approve=True,
        expected_status="ordered",
        expected_actions=FULL_PATH,
        expected_tools=tools_for(FULL_PATH) + ({"tool": "place_purchase_order", "input": None},),
        expected_orders=1,
        expected_total_minor=2400,
    ),
    Scenario(
        name="budget_blocks_everything",
        skus=("gloves",),
        budget_minor=1000,
        actions=reads(("gloves",)) + (PROPOSAL,),
        approve=False,
        expected_status="budget_blocked",
        expected_actions=reads(("gloves",)) + (PROPOSAL,),
        expected_tools=tools_for(reads(("gloves",))),
        expected_orders=0,
    ),
    Scenario(
        name="human_rejects_proposal",
        skus=("gloves", "tubes"),
        budget_minor=3000,
        actions=FULL_PATH,
        approve=False,
        expected_status="rejected",
        expected_actions=FULL_PATH,
        expected_tools=tools_for(reads(("gloves", "tubes"))),
        expected_orders=0,
    ),
    Scenario(
        name="duplicate_read_is_a_wasted_step",
        skus=("gloves", "tubes"),
        budget_minor=3000,
        actions=(
            {"tool": "check_stock", "arguments": {"sku": "gloves"}},
            {"tool": "get_supplier_offer", "arguments": {"sku": "gloves"}},
            {"tool": "check_stock", "arguments": {"sku": "gloves"}},
            {"tool": "check_stock", "arguments": {"sku": "tubes"}},
            {"tool": "get_supplier_offer", "arguments": {"sku": "tubes"}},
            PROPOSAL,
        ),
        approve=True,
        expected_status="ordered",
        expected_actions=FULL_PATH,
        expected_tools=tools_for(FULL_PATH) + ({"tool": "place_purchase_order", "input": None},),
        expected_orders=1,
        expected_total_minor=2400,
    ),
)
