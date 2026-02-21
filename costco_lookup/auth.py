"""
auth.py — Token cache management for Costco Order Lookup.

Authentication is done via --inject-token (manual) or automatic refresh
when a refresh_token is present in the cache.

Token cache stores id_token + optional refresh_token with expiry. On each
run, the cached token is reused if still valid. If expired and a refresh_token
is present, the token is silently refreshed via the Azure AD B2C endpoint.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from costco_lookup.paths import BASE_DIR

log = logging.getLogger(__name__)

TOKEN_CACHE_FILE = BASE_DIR / ".token_cache.json"

EXPIRY_BUFFER_SECONDS = 300
ID_TOKEN_TTL_SECONDS = 3600

# Azure AD B2C token endpoint — discovered from Postman collection
_B2C_TOKEN_URL = (
    "https://signin.costco.com"
    "/e0714dd4-784d-46d6-a278-3e29553483eb"
    "/b2c_1a_sso_wcs_signup_signin_201"
    "/oauth2/v2.0/token"
)
_B2C_CLIENT_ID = "a3a5186b-7c89-4b4c-93a8-dd604e930757"


# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------

def load_token_cache() -> Optional[dict]:
    """
    Return cached token dict if id_token is still valid, else None.
    The dict may contain 'refresh_token' if one was previously saved.
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


def _load_cache_raw() -> Optional[dict]:
    """Return raw cache dict regardless of token expiry (for refresh_token extraction)."""
    if not TOKEN_CACHE_FILE.exists():
        return None
    try:
        with TOKEN_CACHE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, json.JSONDecodeError):
        return None


def save_token_cache(
    id_token: str,
    id_token_ttl: int = ID_TOKEN_TTL_SECONDS,
    refresh_token: Optional[str] = None,
) -> None:
    """Persist id_token (and optionally refresh_token) to .token_cache.json."""
    now = datetime.now(timezone.utc)
    data = {
        "token": id_token,
        "token_type": "Bearer",
        "expires_at": (now + timedelta(seconds=id_token_ttl)).isoformat(),
    }
    if refresh_token:
        data["refresh_token"] = refresh_token
    with TOKEN_CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    log.debug(
        "Token cache written (id_token TTL=%ds, refresh_token=%s)",
        id_token_ttl,
        "present" if refresh_token else "absent",
    )


def clear_token_cache() -> None:
    """Delete the cached token file."""
    if TOKEN_CACHE_FILE.exists():
        TOKEN_CACHE_FILE.unlink()
        log.info("Token cache cleared")


def inject_token(id_token: str, refresh_token: Optional[str] = None) -> None:
    """
    Manually save an id_token obtained from Chrome DevTools.
    Optionally also save a refresh_token to enable automatic renewal.
    The id_token is assumed to be valid for 1 hour from now.
    """
    save_token_cache(id_token, id_token_ttl=ID_TOKEN_TTL_SECONDS, refresh_token=refresh_token)
    log.info(
        "Token injected manually (TTL=%ds, length=%d, refresh_token=%s)",
        ID_TOKEN_TTL_SECONDS,
        len(id_token),
        "present" if refresh_token else "absent",
    )


# ---------------------------------------------------------------------------
# Refresh token
# ---------------------------------------------------------------------------

def refresh_access_token() -> str:
    """
    Use the stored refresh_token to obtain a new id_token from Azure AD B2C.
    Saves the new id_token and rotated refresh_token to cache.
    Returns the new id_token.
    Raises RuntimeError if no refresh_token is stored or the request fails.
    """
    raw = _load_cache_raw()
    stored_refresh = (raw or {}).get("refresh_token")
    if not stored_refresh:
        raise RuntimeError(
            "No refresh_token stored. Run --inject-token and provide a refresh token to enable auto-renewal."
        )

    log.info("Refreshing access token via Azure AD B2C...")
    payload = {
        "client_id": _B2C_CLIENT_ID,
        "scope": "openid profile offline_access",
        "grant_type": "refresh_token",
        "client_info": "1",
        "x-client-sku": "msal.js.browser",
        "x-client-ver": "2.32.1",
        "x-ms-lib-capability": "retry-after, h429",
        "client-request-id": str(uuid.uuid4()),
        "refresh_token": stored_refresh,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.costco.com/",
        "Origin": "https://www.costco.com",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        ),
    }
    try:
        resp = requests.post(_B2C_TOKEN_URL, data=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        body = resp.json()
    except requests.HTTPError as exc:
        log.error("Token refresh HTTP error: %s — body: %s", exc, resp.text[:300])
        raise RuntimeError(f"Token refresh failed: {exc}") from exc
    except Exception as exc:
        log.error("Token refresh unexpected error: %s", exc)
        raise RuntimeError(f"Token refresh failed: {exc}") from exc

    new_id_token = body.get("id_token")
    new_refresh_token = body.get("refresh_token")
    expires_in = int(body.get("expires_in", ID_TOKEN_TTL_SECONDS))

    if not new_id_token:
        log.error("Token refresh response missing id_token: %s", body)
        raise RuntimeError("Token refresh response did not contain an id_token.")

    save_token_cache(new_id_token, id_token_ttl=expires_in, refresh_token=new_refresh_token)
    log.info(
        "Token refreshed successfully (TTL=%ds, new_refresh_token=%s)",
        expires_in,
        "present" if new_refresh_token else "absent",
    )
    return new_id_token


# ---------------------------------------------------------------------------
# High-level: get a valid token from cache (auto-refresh if possible)
# ---------------------------------------------------------------------------

def get_valid_token() -> str:
    """
    Return a valid id_token.
    1. Returns cached token if still valid.
    2. If expired, attempts refresh via refresh_token (if stored).
    3. If no refresh_token or refresh fails, raises RuntimeError with instructions.
    """
    cached = load_token_cache()
    if cached:
        log.info("Using cached id_token")
        return cached["token"]

    # Attempt automatic refresh
    raw = _load_cache_raw()
    if raw and raw.get("refresh_token"):
        log.info("id_token expired — attempting auto-refresh")
        try:
            return refresh_access_token()
        except RuntimeError as exc:
            log.warning("Auto-refresh failed: %s", exc)
            # Fall through to manual injection error

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
