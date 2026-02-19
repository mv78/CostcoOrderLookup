"""
config.py — Load/save API endpoint configuration from config.json.

All B2C / API values are pre-populated from the HAR analysis.
The only field a user must supply is `warehouse_number` (via --setup).
"""

import json

from costco_lookup.paths import BASE_DIR

CONFIG_FILE = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    # Costco ecom API
    "graphql_endpoint": "https://ecom-api.costco.com/ebusiness/order/v1/orders/graphql",
    # Azure AD B2C / MSAL — discovered from HAR
    "b2c_tenant": "e0714dd4-784d-46d6-a278-3e29553483eb",
    "b2c_policy": "B2C_1A_SSO_WCS_signup_signin_201",
    "b2c_client_id": "a3a5186b-7c89-4b4c-93a8-dd604e930757",
    "redirect_uri": "https://www.costco.com/myaccount/",
    # Static Costco client identifiers — required request headers
    "client_identifier": "481b1aec-aa3b-454b-b81b-48187e28f205",
    "wcs_client_id": "4900eb1f-0c10-4bd9-99c3-c59e6c1ecebf",
    "token_header_name": "costco-x-authorization",
    # User-specific — collected during --setup
    "warehouse_number": "",
}

REQUIRED_KEYS = ["warehouse_number"]


def load_config() -> dict:
    """
    Load config.json, merging with defaults.
    Raises ValueError if user-required fields are not set.
    """
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"config.json not found at {CONFIG_FILE}. "
            "Run with --setup to configure."
        )
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # Merge defaults so new keys are always present
    merged = {**DEFAULT_CONFIG, **data}

    for key in REQUIRED_KEYS:
        if not merged.get(key):
            raise ValueError(
                f"config.json is missing required field '{key}'. "
                "Run with --setup to configure."
            )
    return merged


def save_config(data: dict) -> None:
    """Write config dict to config.json (merges with defaults)."""
    existing = {}
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            existing = json.load(f)
    merged = {**DEFAULT_CONFIG, **existing, **data}
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
    print(f"[config] Saved to {CONFIG_FILE}")
