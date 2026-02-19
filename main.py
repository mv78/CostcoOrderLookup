#!/usr/bin/env python3
"""
main.py — CLI entry point for Costco Order Lookup.

Usage:
  python main.py --setup                       # First-time configuration
  python main.py --item 1900477                # Look up orders for item number
  python main.py --item 1900477 --output json
  python main.py --item 1900477 --output csv
  python main.py --item 1900477 --years 10     # Search further back (default: 5)
  python main.py --item 1900477 --debug        # Verbose logging to console
  python main.py --refresh-token               # Force re-authentication

Logs are always written to costco_lookup.log (DEBUG level).
Pass --debug to also print DEBUG messages to the terminal.
"""

import argparse
import getpass
import logging
import sys

import requests

from costco_lookup.logger import setup_logging
from costco_lookup import config as cfg
from costco_lookup import auth
from costco_lookup.client import GraphQLClient
from costco_lookup import orders as ord_mod
from costco_lookup import display

log = logging.getLogger(__name__)


def cmd_setup(debug: bool) -> None:
    setup_logging(debug)
    log.info("=== Setup wizard started ===")
    print("=== Costco Order Lookup — Setup ===\n")
    print("API endpoints are pre-configured. You just need your")
    print("warehouse number and Costco account credentials.\n")

    print("Step 1/2: Warehouse number")
    print("  (Find it on your Costco membership card, or on any receipt.)\n")
    warehouse_number = input("  Enter your home warehouse number: ").strip()
    if not warehouse_number:
        log.error("Warehouse number not provided")
        print("[error] Warehouse number is required.")
        sys.exit(1)

    cfg.save_config({"warehouse_number": warehouse_number})
    log.info("Warehouse number saved: %s", warehouse_number)

    print("\nStep 2/2: Costco account credentials")
    username = input("  Enter Costco email: ").strip()
    if not username:
        log.error("Email not provided")
        print("[error] Email is required.")
        sys.exit(1)
    password = getpass.getpass("  Enter Costco password: ")
    if not password:
        log.error("Password not provided")
        print("[error] Password is required.")
        sys.exit(1)

    auth.setup_credentials(username, password)

    print("\nTesting login…")
    try:
        config = cfg.load_config()
        session = _make_session()
        auth.clear_token_cache()
        auth.get_valid_token(session, config, force_refresh=True)
        log.info("Setup complete")
        print("\n[setup] Setup complete!")
        print("  Run:  python main.py --item <ITEM_NUMBER>")
    except Exception as exc:
        log.exception("Login test failed during setup")
        print(f"\n[setup] Login test failed: {exc}")
        print(f"  Check costco_lookup.log for details.")
        sys.exit(1)


def cmd_lookup(item_number: str, output_format: str, search_years: int, debug: bool) -> None:
    setup_logging(debug)
    log.info("Item lookup started: item=%s format=%s years=%d", item_number, output_format, search_years)

    try:
        config = cfg.load_config()
    except (FileNotFoundError, ValueError) as exc:
        log.error("Config load failed: %s", exc)
        print(f"[error] {exc}")
        sys.exit(1)

    session = _make_session()

    def refresh_token() -> str:
        log.info("Token refresh callback invoked by GraphQL client")
        return auth.get_valid_token(session, config, force_refresh=True)

    try:
        token = auth.get_valid_token(session, config)
    except RuntimeError as exc:
        log.exception("Failed to obtain auth token")
        print(f"[error] {exc}")
        print(f"  Check costco_lookup.log for details.")
        sys.exit(1)

    client = GraphQLClient(session, config, token, on_token_refresh=refresh_token)

    try:
        order_list = ord_mod.find_orders_by_item(
            client,
            item_number=item_number,
            warehouse_number=config["warehouse_number"],
            search_years=search_years,
        )
    except Exception as exc:
        log.exception("Order lookup failed")
        print(f"[error] {exc}")
        print(f"  Check costco_lookup.log for details.")
        sys.exit(1)

    log.info("Lookup complete: %d result(s) for item %s", len(order_list), item_number)

    if output_format == "json":
        display.print_json(order_list)
    elif output_format == "csv":
        display.print_csv(order_list)
    else:
        display.print_table(order_list)


def cmd_inject_token(token_arg, debug: bool) -> None:
    """
    Manually inject a bearer token copied from Chrome DevTools.

    Accepts the token either as a command-line argument:
        python main.py --inject-token "eyJ..."
    or interactively (prompts for paste) when no value is given:
        python main.py --inject-token

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

    auth.inject_token(token)
    log.info("Token injected, length=%d", len(token))
    print("[auth] Token saved (expires in ~1 hour). Run: python main.py --item <ITEM_NUMBER>")


def cmd_refresh_token(debug: bool) -> None:
    setup_logging(debug)
    log.info("Force token refresh requested")

    try:
        config = cfg.load_config()
    except (FileNotFoundError, ValueError) as exc:
        log.error("Config load failed: %s", exc)
        print(f"[error] {exc}")
        sys.exit(1)

    auth.clear_token_cache()
    session = _make_session()
    try:
        auth.get_valid_token(session, config, force_refresh=True)
        log.info("Token refreshed successfully")
        print("[auth] Token refreshed successfully.")
    except RuntimeError as exc:
        log.exception("Token refresh failed")
        print(f"[error] {exc}")
        print(f"  Check costco_lookup.log for details.")
        sys.exit(1)


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
        description="Look up past Costco orders and warehouse receipts by item number.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--setup",
        action="store_true",
        help="First-time setup (warehouse number + credentials).",
    )
    group.add_argument(
        "--item",
        metavar="ITEM_NUMBER",
        help="Costco item number to search for.",
    )
    group.add_argument(
        "--refresh-token",
        action="store_true",
        dest="refresh_token",
        help="Force re-authentication and refresh the cached token.",
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
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.setup:
        cmd_setup(args.debug)
    elif args.item:
        cmd_lookup(args.item, args.output, args.years, args.debug)
    elif args.refresh_token:
        cmd_refresh_token(args.debug)
    elif args.inject_token is not None:
        token_val = None if args.inject_token == "__prompt__" else args.inject_token
        cmd_inject_token(token_val, args.debug)


if __name__ == "__main__":
    main()
