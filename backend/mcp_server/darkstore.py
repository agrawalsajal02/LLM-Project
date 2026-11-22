"""Dark-store ops MCP server: inventory, expiry and ops tickets.

Run standalone to try it with any MCP client:
    python -m mcp_server.darkstore                 # stdio (how the backend uses it)
    python -m mcp_server.darkstore --http          # streamable HTTP on :8765

Data lives in a SQLite file (DARKSTORE_DB, default ./data/darkstore.db), seeded with
sample SKUs on first run. Expiry dates are relative to today so the demo always works.
If JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN and JIRA_PROJECT are set, tickets go to Jira
(free plan works); otherwise they're stored locally.
"""

import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
from mcp.server.mcpserver import MCPServer

DB_PATH = Path(os.environ.get("DARKSTORE_DB", "./data/darkstore.db"))

mcp = MCPServer("darkstore-tools")

# (sku_id, name, category, stock, reorder_level, location, days_to_expiry)
SEED = [
    ("SKU-1001", "Amul Taaza Toned Milk 500ml", "dairy", 42, 30, "Cold-A1", 2),
    ("SKU-1002", "Mother Dairy Curd 400g", "dairy", 18, 20, "Cold-A2", 4),
    ("SKU-1003", "Amul Butter 100g", "dairy", 55, 25, "Cold-A3", 45),
    ("SKU-1004", "Paneer Fresh 200g", "dairy", 9, 15, "Cold-A4", 1),
    ("SKU-1005", "Greek Yogurt Blueberry 100g", "dairy", 24, 10, "Cold-A5", 6),
    ("SKU-2001", "Brown Bread 400g", "bakery", 15, 20, "Dry-B1", 3),
    ("SKU-2002", "Multigrain Bread 400g", "bakery", 22, 15, "Dry-B1", 5),
    ("SKU-2003", "Chocolate Muffin 2pc", "bakery", 6, 10, "Dry-B2", 2),
    ("SKU-3001", "Banana Robusta 1kg", "fruits", 30, 25, "Fresh-C1", 3),
    ("SKU-3002", "Apple Shimla 1kg", "fruits", 48, 20, "Fresh-C2", 14),
    ("SKU-3003", "Spinach 250g", "vegetables", 12, 15, "Fresh-C3", 1),
    ("SKU-3004", "Tomato Hybrid 1kg", "vegetables", 60, 30, "Fresh-C4", 7),
    ("SKU-4001", "Basmati Rice 5kg", "staples", 35, 10, "Dry-D1", 300),
    ("SKU-4002", "Toor Dal 1kg", "staples", 8, 12, "Dry-D2", 240),
    ("SKU-5001", "Frozen Green Peas 500g", "frozen", 27, 10, "Freezer-E1", 120),
    ("SKU-5002", "Chicken Nuggets 450g", "frozen", 14, 8, "Freezer-E2", 9),
]


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS skus (
            sku_id TEXT PRIMARY KEY, name TEXT, category TEXT, stock INTEGER,
            reorder_level INTEGER, location TEXT, expiry_date TEXT);
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT,
            priority TEXT, status TEXT DEFAULT 'open', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        """
    )
    if conn.execute("SELECT COUNT(*) FROM skus").fetchone()[0] == 0:
        today = date.today()
        conn.executemany(
            "INSERT INTO skus VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(s, n, c, st, r, loc, (today + timedelta(days=d)).isoformat()) for s, n, c, st, r, loc, d in SEED],
        )
        conn.commit()
    return conn


def _rows(cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]


@mcp.tool()
def search_skus(query: str = "", category: str | None = None) -> list[dict]:
    """Find SKUs by (part of) name or SKU id, optionally within a category
    (dairy, bakery, fruits, vegetables, staples, frozen). Empty query lists all."""
    sql, args = "SELECT * FROM skus WHERE (name LIKE ? OR sku_id LIKE ?)", [f"%{query}%", f"%{query}%"]
    if category:
        sql += " AND category = ?"
        args.append(category.lower())
    with connect() as conn:
        return _rows(conn.execute(sql + " ORDER BY sku_id", args))


@mcp.tool()
def get_stock(sku_id: str) -> dict:
    """Current stock, reorder level, shelf location and expiry date for one SKU id (e.g. SKU-1001)."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM skus WHERE sku_id = ?", [sku_id.upper()]).fetchone()
    return dict(row) if row else {"error": f"Unknown SKU {sku_id}"}


@mcp.tool()
def get_expiring_skus(days: int = 7, category: str | None = None) -> list[dict]:
    """SKUs whose expiry date is within the next `days` days (default 7), soonest first,
    optionally filtered by category. Includes days_left for each SKU."""
    limit = (date.today() + timedelta(days=days)).isoformat()
    sql = "SELECT *, CAST(julianday(expiry_date) - julianday('now', 'localtime', 'start of day') AS INTEGER) AS days_left FROM skus WHERE expiry_date <= ?"
    args: list = [limit]
    if category:
        sql += " AND category = ?"
        args.append(category.lower())
    with connect() as conn:
        return _rows(conn.execute(sql + " ORDER BY expiry_date", args))


@mcp.tool()
def get_low_stock(category: str | None = None) -> list[dict]:
    """SKUs whose stock is below their reorder level, i.e. items that need restocking."""
    sql, args = "SELECT *, reorder_level - stock AS shortfall FROM skus WHERE stock < reorder_level", []
    if category:
        sql += " AND category = ?"
        args.append(category.lower())
    with connect() as conn:
        return _rows(conn.execute(sql + " ORDER BY shortfall DESC", args))


@mcp.tool()
def create_ticket(title: str, description: str, priority: str = "medium") -> dict:
    """Create an ops ticket (e.g. 'Restock paneer', 'Remove expired curd'). Priority: low, medium or high.
    This is a WRITE action: only call it when the user explicitly asks to create a ticket."""
    jira = {k: os.environ.get(f"JIRA_{k}") for k in ("URL", "EMAIL", "API_TOKEN", "PROJECT")}
    if all(jira.values()):
        response = httpx.post(
            f"{jira['URL'].rstrip('/')}/rest/api/3/issue",
            auth=(jira["EMAIL"], jira["API_TOKEN"]),
            json={
                "fields": {
                    "project": {"key": jira["PROJECT"]},
                    "summary": title,
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}],
                    },
                    "issuetype": {"name": "Task"},
                }
            },
            timeout=20,
        )
        response.raise_for_status()
        return {"ticket": response.json()["key"], "system": "jira", "title": title}
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO tickets (title, description, priority) VALUES (?, ?, ?)", [title, description, priority]
        )
        return {"ticket": f"OPS-{cursor.lastrowid}", "system": "local", "title": title, "priority": priority}


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http", port=8765)
    else:
        mcp.run()
