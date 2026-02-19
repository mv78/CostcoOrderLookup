"""
display.py — Output formatting for order results (table / JSON / CSV).
"""

import csv
import json
import sys

from rich.console import Console
from rich.table import Table
from rich.text import Text

console = Console()

# Columns shown in table mode — (field_key, header_label, justify)
TABLE_COLUMNS = [
    ("source",        "Source",      "center"),
    ("date",          "Date",        "left"),
    ("order_id",      "Order/Receipt ID", "left"),
    ("item_number",   "Item #",      "right"),
    ("description",   "Description", "left"),
    ("status",        "Status",      "left"),
    ("receipt_total", "Order Total", "right"),
    ("warehouse",     "Warehouse",   "left"),
    ("carrier",       "Carrier",     "left"),
    ("tracking",      "Tracking #",  "left"),
    ("tender",        "Tender",      "left"),
]

# All field keys — used for JSON / CSV
ALL_KEYS = [col[0] for col in TABLE_COLUMNS]


def print_table(orders: list[dict]) -> None:
    """Render orders as a rich terminal table."""
    if not orders:
        console.print("[yellow]No orders found for that item number.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan", show_lines=True)
    for key, label, justify in TABLE_COLUMNS:
        table.add_column(label, justify=justify, overflow="fold", no_wrap=(key == "tracking"))

    for order in orders:
        source = order.get("source", "")
        row_style = "bright_white" if source == "online" else "dim"
        table.add_row(
            *[_fmt_cell(key, order.get(key, "—")) for key, _, _ in TABLE_COLUMNS],
            style=row_style,
        )

    console.print(table)
    online = sum(1 for o in orders if o.get("source") == "online")
    warehouse = sum(1 for o in orders if o.get("source") == "warehouse")
    console.print(
        f"[dim]{len(orders)} result(s): {online} online order(s), {warehouse} warehouse receipt(s).[/dim]"
    )


def print_json(orders: list[dict]) -> None:
    """Print orders as pretty-printed JSON to stdout."""
    print(json.dumps(orders, indent=2, ensure_ascii=False))


def print_csv(orders: list[dict]) -> None:
    """Write orders as CSV to stdout."""
    if not orders:
        return
    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=ALL_KEYS,
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(orders)


def _fmt_cell(key: str, value: str) -> str:
    if value is None:
        return "—"
    return str(value)
