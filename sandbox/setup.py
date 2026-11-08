"""Create or explicitly reset the local test database."""
import argparse
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "inventory.sqlite3"


def initialize(path=DEFAULT_DB, reset=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS items (sku TEXT PRIMARY KEY, on_hand INTEGER NOT NULL, threshold INTEGER NOT NULL, target INTEGER NOT NULL, price INTEGER NOT NULL, available INTEGER NOT NULL, pending INTEGER NOT NULL DEFAULT 0)")
        connection.execute("CREATE TABLE IF NOT EXISTS orders (request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, total INTEGER NOT NULL)")
        if reset:
            connection.execute("DELETE FROM orders")
            connection.execute("DELETE FROM items")
        connection.executemany("INSERT OR IGNORE INTO items VALUES (?, ?, ?, ?, ?, ?, 0)", [
            ("gloves", 2, 2, 6, 600, 100),
            ("tubes", 0, 0, 1, 2000, 50),
            ("masks", 10, 3, 12, 300, 100),
        ])
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize the sandbox; --reset clears test orders.")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    initialize(reset=args.reset)
    print(f"Sandbox ready at {DEFAULT_DB}")
