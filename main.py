#!/usr/bin/env python3
"""
main.py — CLI entry point for Costco Order Lookup.

Usage:
  python main.py --inject-token "eyJ..."         # Save token from Chrome DevTools
  python main.py --inject-token                  # Interactive token prompt
  python main.py --item 1900477                  # Look up orders for item number
  python main.py --item 1900477 --output json
  python main.py --item 1900477 --output csv
  python main.py --item 1900477 --years 10       # Search further back (default: 5)
  python main.py --item 1900477 --debug          # Verbose logging to console
  python main.py --description "kirkland olive"  # Search by product description

Logs are always written to costco_lookup.log (DEBUG level).
Pass --debug to also print DEBUG messages to the terminal.
"""

import argparse
import logging
import sys

import requests
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
)

from costco_lookup.logger import setup_logging
from costco_lookup import config as cfg
from costco_lookup import auth
from costco_lookup.client import GraphQLClient
from costco_lookup import orders as ord_mod
from costco_lookup import display

log = logging.getLogger(__name__)


def cmd_lookup(item_number: str, output_format: str, search_years: int, debug: bool, download: bool = False) -> None:
    setup_logging(debug)
    log.info("Item lookup started: item=%s format=%s years=%d download=%s", item_number, output_format, search_years, download)

    try:
        config = cfg.load_config()
    except (FileNotFoundError, ValueError) as exc:
        log.error("Config load failed: %s", exc)
        print(f"[error] {exc}")
        sys.exit(1)

    try:
        token = auth.get_valid_token()
    except RuntimeError as exc:
        log.exception("Failed to obtain auth token")
        print(f"[error] {exc}")
        sys.exit(1)

    session = _make_session()
    client = GraphQLClient(session, config, token)

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TextColumn("{task.percentage:>3.0f}%"),
            transient=True,
        ) as progress:
            task = progress.add_task("Searching...", total=None)

            def on_progress(current, total, message):
                progress.update(task, completed=current, total=total, description=message)

            order_list = ord_mod.find_orders_by_item(
                client,
                item_number=item_number,
                warehouse_number=config["warehouse_number"],
                search_years=search_years,
                on_progress=on_progress,
            )
    except Exception as exc:
        log.exception("Order lookup failed")
        print(f"[error] {exc}")
        print(f"  Check costco_lookup.log for details.")
        sys.exit(1)

    log.info("Lookup complete: %d result(s) for item %s", len(order_list), item_number)

    if download:
        from costco_lookup import downloader
        saved = downloader.download_documents(order_list, client, item_number)

    if output_format == "json":
        display.print_json(order_list)
    elif output_format == "csv":
        display.print_csv(order_list)
    else:
        display.print_table(order_list)

    if download:
        if saved:
            import webbrowser
            print(f"\nDownloaded {len(saved)} file(s) to: {downloader.INVOICES_DIR}")
            for p in saved:
                print(f"  {p.name}")
                webbrowser.open(p.as_uri())


def cmd_lookup_by_description(
    description_query: str,
    output_format: str,
    search_years: int,
    debug: bool,
    download: bool = False,
) -> None:
    setup_logging(debug)
    log.info("Description lookup started: query=%r format=%s years=%d download=%s",
             description_query, output_format, search_years, download)

    try:
        config = cfg.load_config()
    except (FileNotFoundError, ValueError) as exc:
        log.error("Config load failed: %s", exc)
        print(f"[error] {exc}")
        sys.exit(1)

    try:
        token = auth.get_valid_token()
    except RuntimeError as exc:
        log.exception("Failed to obtain auth token")
        print(f"[error] {exc}")
        sys.exit(1)

    session = _make_session()
    client = GraphQLClient(session, config, token)

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TextColumn("{task.percentage:>3.0f}%"),
            transient=True,
        ) as progress:
            task = progress.add_task("Searching...", total=None)

            def on_progress(current, total, message):
                progress.update(task, completed=current, total=total, description=message)

            order_list = ord_mod.find_orders_by_description(
                client,
                description_query=description_query,
                warehouse_number=config["warehouse_number"],
                search_years=search_years,
                on_progress=on_progress,
            )
    except Exception as exc:
        log.exception("Description lookup failed")
        print(f"[error] {exc}")
        print(f"  Check costco_lookup.log for details.")
        sys.exit(1)

    log.info("Description lookup complete: %d result(s) for %r", len(order_list), description_query)

    if download:
        from costco_lookup import downloader
        saved = downloader.download_documents(order_list, client, description_query)

    if output_format == "json":
        display.print_json(order_list)
    elif output_format == "csv":
        display.print_csv(order_list)
    else:
        display.print_table(order_list)

    if download:
        if saved:
            import webbrowser
            print(f"\nDownloaded {len(saved)} file(s) to: {downloader.INVOICES_DIR}")
            for p in saved:
                print(f"  {p.name}")
                webbrowser.open(p.as_uri())


def cmd_inject_token(token_arg, debug: bool) -> None:
    """
    Manually inject a bearer token copied from Chrome DevTools.

    Accepts the token either as a command-line argument:
        python main.py --inject-token "eyJ..."
    or interactively (prompts for paste) when no value is given:
        python main.py --inject-token

    Optionally also prompts for a refresh_token to enable auto-renewal.

    How to get the token:
      1. Open Chrome → costco.com (must be logged in)
      2. DevTools (F12) → Network tab → navigate to Order History
      3. Click any request to ecom-api.costco.com
      4. Headers → find 'costco-x-authorization: Bearer eyJ...'
      5. Copy everything AFTER 'Bearer ' (the eyJ... part)
    """
    setup_logging(debug)

    if token_arg:
        token = token_arg.strip()
    else:
        print("Paste the Bearer token from Chrome DevTools (costco-x-authorization header).")
        print("Press Enter twice when done.\n")
        lines = []
        while True:
            line = input()
            if not line:
                break
            lines.append(line.strip())
        token = "".join(lines).strip()

    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    if not token:
        print("[error] No token provided.")
        sys.exit(1)

    # Optionally collect a refresh_token for auto-renewal
    print("\n[optional] Paste a refresh_token to enable automatic token renewal.")
    print("Press Enter to skip, or paste and press Enter twice.\n")
    rt_lines = []
    while True:
        line = input()
        if not line:
            break
        rt_lines.append(line.strip())
    refresh_token = "".join(rt_lines).strip() or None

    auth.inject_token(token, refresh_token=refresh_token)
    log.info("Token injected, length=%d, refresh_token=%s", len(token), "present" if refresh_token else "absent")
    if refresh_token:
        print("[auth] Token + refresh_token saved. Token will auto-renew on expiry.")
    else:
        print("[auth] Token saved (expires in ~1 hour). Run: python main.py --item <ITEM_NUMBER>")


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        )
    })
    return s


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="costco-lookup",
        description="Look up past Costco orders and warehouse receipts by item number or description.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--item",
        metavar="ITEM_NUMBER",
        help="Costco item number to search for.",
    )
    group.add_argument(
        "--description",
        metavar="TEXT",
        help="Search orders by product description (partial match).",
    )
    group.add_argument(
        "--inject-token",
        nargs="?",
        const="__prompt__",
        metavar="TOKEN",
        dest="inject_token",
        help="Save a Bearer token from Chrome DevTools. Pass inline or omit to be prompted.",
    )
    parser.add_argument(
        "--output",
        choices=["table", "json", "csv"],
        default="table",
        help="Output format (default: table).",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=ord_mod.DEFAULT_SEARCH_YEARS,
        metavar="N",
        help=f"How many years back to search (default: {ord_mod.DEFAULT_SEARCH_YEARS}).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print DEBUG-level log messages to the terminal (always written to costco_lookup.log).",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download HTML invoices/receipts to invoices/ folder.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.item:
        cmd_lookup(args.item, args.output, args.years, args.debug, args.download)
    elif args.description:
        cmd_lookup_by_description(args.description, args.output, args.years, args.debug, args.download)
    elif args.inject_token is not None:
        token_val = None if args.inject_token == "__prompt__" else args.inject_token
        cmd_inject_token(token_val, args.debug)


if __name__ == "__main__":
    main()
