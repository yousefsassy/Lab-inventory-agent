"""Deterministic purchasing and execution limits, independent of the model."""

from dataclasses import dataclass


def require_integer(name: str, value: int, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


@dataclass(frozen=True)
class Refill:
    sku: str
    on_hand: int
    threshold: int
    target: int
    unit_price_minor: int

    def __post_init__(self) -> None:
        if not isinstance(self.sku, str) or not self.sku.strip():
            raise ValueError("sku must be a nonempty string")
        for name in ("on_hand", "threshold", "target"):
            require_integer(name, getattr(self, name))
        require_integer("unit_price_minor", self.unit_price_minor, 1)
        if self.target <= self.threshold:
            raise ValueError("target must exceed threshold")

    @property
    def quantity(self) -> int:
        if self.on_hand <= self.threshold:
            return self.target - self.on_hand
        return 0


@dataclass(frozen=True)
class PurchasePlan:
    selected: tuple[Refill, ...]
    deferred: tuple[Refill, ...]
    total_minor: int
    remaining_minor: int
    reason: str


def plan_refills(items: list[Refill], budget_minor: int) -> PurchasePlan:
    """Consider each unique SKU once, in the supplied priority order."""
    require_integer("budget_minor", budget_minor)
    if len({item.sku for item in items}) != len(items):
        raise ValueError("duplicate SKUs are not allowed")
    selected = []
    deferred = []
    remaining = budget_minor
    for item in items:
        if item.quantity == 0:
            continue
        cost = item.quantity * item.unit_price_minor
        if cost <= remaining:
            selected.append(item)
            remaining -= cost
        else:
            deferred.append(item)
    reason = "ready_for_approval"
    if not selected:
        reason = "budget_blocked" if deferred else "nothing_needed"
    return PurchasePlan(tuple(selected), tuple(deferred), budget_minor - remaining,
                        remaining, reason)


class ActionLimitReached(RuntimeError):
    """The request has consumed its permitted actions."""


@dataclass(frozen=True)
class ActionBudget:
    """Persist this state across approval. Consume before every attempted action."""
    limit: int = 20
    used: int = 0

    def __post_init__(self) -> None:
        require_integer("limit", self.limit, 1)
        require_integer("used", self.used)
        if self.used > self.limit:
            raise ValueError("used cannot exceed limit")

    def consume(self) -> "ActionBudget":
        if self.used == self.limit:
            raise ActionLimitReached("Action limit reached; stop this request")
        return ActionBudget(self.limit, self.used + 1)
