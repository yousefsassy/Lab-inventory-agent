"""Strict tool inputs and transactional sandbox operations."""
import json
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict, dataclass

from agent.policy import Refill, require_integer
from sandbox.setup import DEFAULT_DB


@dataclass(frozen=True)
class OrderLine:
    sku: str
    quantity: int
    unit_price_minor: int

    def __post_init__(self):
        if not isinstance(self.sku, str) or not self.sku.strip():
            raise ValueError("sku must be a nonempty string")
        require_integer("quantity", self.quantity, 1)
        require_integer("unit_price_minor", self.unit_price_minor, 1)


@dataclass(frozen=True)
class ApprovedOrder:
    """Trusted application state, supplied only after the human checkpoint."""
    request_id: str
    budget_minor: int
    lines: tuple[OrderLine, ...]

    def __post_init__(self):
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("request_id must be a nonempty string")
        require_integer("budget_minor", self.budget_minor)
        if type(self.lines) is not tuple or not self.lines:
            raise ValueError("lines must be a nonempty tuple")
        if any(type(line) is not OrderLine for line in self.lines):
            raise ValueError("lines must contain OrderLine objects")
        if len({line.sku for line in self.lines}) != len(self.lines):
            raise ValueError("duplicate SKUs are not allowed")
        if self.total > self.budget_minor:
            raise ValueError("order exceeds approved budget")

    @property
    def total(self):
        return sum(line.quantity * line.unit_price_minor for line in self.lines)


# Approval and budget are trusted host state, never model-supplied tool arguments.
TOOL_SCHEMAS = {
    name: {
        "type": "object",
        "properties": {field: {"type": "string", "minLength": 1}},
        "required": [field],
        "additionalProperties": False,
    }
    for name, field in (
        ("check_stock", "sku"),
        ("get_supplier_offer", "sku"),
        ("place_purchase_order", "request_id"),
    )
}


class InventoryTools:
    def __init__(self, database=DEFAULT_DB, approved_order=None):
        self.database = database
        self.approved_order = approved_order
        self.trace = []

    def call(self, name, arguments):
        started = time.perf_counter()
        try:
            if name not in TOOL_SCHEMAS:
                raise ValueError("unknown tool; choose a declared tool")
            field = TOOL_SCHEMAS[name]["required"][0]
            if type(arguments) is not dict or set(arguments) != {field}:
                raise ValueError(f"provide exactly the field {field}")
            value = arguments[field]
            if type(value) is not str or not value.strip():
                raise ValueError(f"{field} must be a nonempty string")
            if name == "place_purchase_order":
                result = self._place_order(value)
            else:
                result = self._read_item(name, value)
        except (ValueError, TypeError) as error:
            result = {"ok": False, "error": str(error), "retryable": False}
        except sqlite3.Error:
            result = {"ok": False, "error": "Database unavailable; retry within the action limit.", "retryable": True}
        self.trace.append({"tool": name, "input": arguments, "output": result,
                           "duration_seconds": time.perf_counter() - started})
        return result

    def _connect(self):
        # Opening an absent sandbox should fail instead of creating an empty file.
        from pathlib import Path
        uri = Path(self.database).resolve().as_uri() + "?mode=rw"
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        return connection

    def _read_item(self, name, sku):
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM items WHERE sku = ?", (sku,)).fetchone()
        if row is None:
            raise ValueError("Unknown SKU; check the inventory identifier")
        if name == "check_stock":
            return {"ok": True, "sku": sku, "on_hand": row["on_hand"],
                    "pending": row["pending"], "threshold": row["threshold"], "target": row["target"]}
        note = row["note"] if "note" in row.keys() else ""
        return {"ok": True, "sku": sku, "unit_price_minor": row["price"],
                "available": row["available"], "currency": "USD", "note": note}

    def _place_order(self, request_id):
        order = self.approved_order
        if order is None or order.request_id != request_id:
            raise ValueError("Human approval required for this request")
        payload = json.dumps(asdict(order), sort_keys=True)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute("SELECT payload FROM orders WHERE request_id = ?", (request_id,)).fetchone()
                if existing:
                    if existing["payload"] != payload:
                        raise ValueError("Request ID already belongs to a different order")
                    return {"ok": True, "order_id": request_id, "total_minor": order.total}
                for line in order.lines:
                    row = connection.execute("SELECT * FROM items WHERE sku = ?", (line.sku,)).fetchone()
                    if row is None:
                        raise ValueError("Unknown SKU in approved order")
                    refill = Refill(line.sku, row["on_hand"] + row["pending"],
                                    row["threshold"], row["target"], row["price"])
                    if line.unit_price_minor != row["price"] or line.quantity != refill.quantity:
                        raise ValueError("Stock or price changed; prepare a new proposal and obtain approval")
                    if line.quantity > row["available"]:
                        raise ValueError("Supplier has insufficient stock; revise the proposal")
                    connection.execute("UPDATE items SET pending = pending + ?, available = available - ? WHERE sku = ?",
                                       (line.quantity, line.quantity, line.sku))
                connection.execute("INSERT INTO orders VALUES (?, ?, ?)", (request_id, payload, order.total))
        return {"ok": True, "order_id": request_id, "total_minor": order.total}
