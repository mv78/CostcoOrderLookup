"""
auth.py — Token cache management for Costco Order Lookup.

Authentication is done exclusively via --inject-token: the user copies the
Bearer token from Chrome DevTools and the app caches it locally.

Token cache stores id_token with expiry. On next run, the cached token is
reused if still valid (typically ~1 hour from injection time).
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from costco_lookup.paths import BASE_DIR

log = logging.getLogger(__name__)

TOKEN_CACHE_FILE = BASE_DIR / ".token_cache.json"

EXPIRY_BUFFER_SECONDS = 300
ID_TOKEN_TTL_SECONDS = 3600


# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------

def load_token_cache() -> Optional[dict]:
    """
    Return cached token dict if id_token is still valid, else None.
    """
    if not TOKEN_CACHE_FILE.exists():
        log.debug("Token cache file not found")
        return None
    try:
        with TOKEN_CACHE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        expires_at = datetime.fromisoformat(data["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        remaining = (expires_at - now).total_seconds()
        if remaining <= EXPIRY_BUFFER_SECONDS:
            log.debug("Cached id_token expired or expiring soon (%.0f s remaining)", remaining)
            return None
        log.debug("Cached id_token valid for %.0f more seconds", remaining)
        return data
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        log.warning("Could not read token cache: %s", exc)
        return None


def save_token_cache(id_token: str, id_token_ttl: int = ID_TOKEN_TTL_SECONDS) -> None:
    """Persist id_token to .token_cache.json."""
    now = datetime.now(timezone.utc)
    data = {
        "token": id_token,
        "token_type": "Bearer",
        "expires_at": (now + timedelta(seconds=id_token_ttl)).isoformat(),
    }
    with TOKEN_CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    log.debug("Token cache written (id_token TTL=%ds)", id_token_ttl)


def clear_token_cache() -> None:
    """Delete the cached token file."""
    if TOKEN_CACHE_FILE.exists():
        TOKEN_CACHE_FILE.unlink()
        log.info("Token cache cleared")


def inject_token(id_token: str) -> None:
    """
    Manually save an id_token obtained from Chrome DevTools.
    The token is assumed to be valid for 1 hour from now.
    """
    save_token_cache(id_token, id_token_ttl=ID_TOKEN_TTL_SECONDS)
    log.info("Token injected manually (TTL=%ds, length=%d)", ID_TOKEN_TTL_SECONDS, len(id_token))


# ---------------------------------------------------------------------------
# High-level: get a valid token from cache
# ---------------------------------------------------------------------------

def get_valid_token() -> str:
    """
    Return a valid id_token from cache.
    Raises RuntimeError if no valid token is found — user must run --inject-token.
    """
    cached = load_token_cache()
    if cached:
        log.info("Using cached id_token")
        return cached["token"]

    raise RuntimeError(
        "No valid token found. Run --inject-token to save a fresh token from Chrome DevTools.\n"
        "\n"
        "  How to get the token:\n"
        "    1. Open Chrome → costco.com (must be logged in)\n"
        "    2. DevTools (F12) → Network tab → navigate to Order History\n"
        "    3. Click any request to ecom-api.costco.com\n"
        "    4. Headers → find 'costco-x-authorization: Bearer eyJ...'\n"
        "    5. Copy everything AFTER 'Bearer ' (the eyJ... part)\n"
        "    6. Run:  python main.py --inject-token \"eyJ...\""
    )
