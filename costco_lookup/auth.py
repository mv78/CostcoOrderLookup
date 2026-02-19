"""
auth.py — Azure AD B2C / MSAL PKCE authentication for Costco.

Auth flow (discovered from HAR):
  1. GET /oauth2/v2.0/authorize  → B2C login page; sets x-ms-cpim-csrf cookie
  2. POST /SelfAsserted           → submit email + password
  3. GET /api/CombinedSigninAndSignup/confirmed → redirect with ?code=...
  4. POST /oauth2/v2.0/token      → exchange code for tokens (id_token, refresh_token)
  5. The id_token is used as:  costco-x-authorization: Bearer <id_token>

Token cache stores both id_token (short-lived, ~1 h) and refresh_token (90 days).
On next run, refresh_token is used to get a new id_token without re-entering credentials.
"""

import hashlib
import base64
import json
import logging
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import keyring
import requests
from bs4 import BeautifulSoup

from costco_lookup.paths import BASE_DIR

log = logging.getLogger(__name__)

SERVICE_NAME = "CostcoOrderLookup"
USERNAME_KEY = "costco_username"
TOKEN_CACHE_FILE = BASE_DIR / ".token_cache.json"

EXPIRY_BUFFER_SECONDS = 300
ID_TOKEN_TTL_SECONDS = 3600
REFRESH_TOKEN_TTL_SECONDS = 7_776_000


# ---------------------------------------------------------------------------
# Credential management (OS keyring)
# ---------------------------------------------------------------------------

def setup_credentials(username: str, password: str) -> None:
    """Store Costco credentials in the OS keyring."""
    keyring.set_password(SERVICE_NAME, USERNAME_KEY, username)
    keyring.set_password(SERVICE_NAME, username, password)
    log.info("Credentials stored in keyring for %s", username)


def get_credentials() -> tuple[str, str]:
    """Retrieve stored credentials. Raises RuntimeError if not found."""
    username = keyring.get_password(SERVICE_NAME, USERNAME_KEY)
    if not username:
        raise RuntimeError("No credentials found. Run with --setup.")
    password = keyring.get_password(SERVICE_NAME, username)
    if not password:
        raise RuntimeError("Password not found in keyring. Run with --setup.")
    log.debug("Credentials retrieved from keyring for %s", username)
    return username, password


# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------

def load_token_cache() -> Optional[dict]:
    """
    Return cached token dict if id_token is still valid, else None.
    Does NOT attempt to use refresh_token here — that is handled by
    get_valid_token() so the caller controls session sharing.
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


def load_refresh_token() -> Optional[str]:
    """Return the stored refresh_token if it is still within its 90-day window."""
    if not TOKEN_CACHE_FILE.exists():
        return None
    try:
        with TOKEN_CACHE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        rt_expires = data.get("refresh_token_expires_at")
        if not rt_expires:
            log.debug("No refresh_token_expires_at in cache")
            return None
        rt_exp_dt = datetime.fromisoformat(rt_expires)
        if rt_exp_dt.tzinfo is None:
            rt_exp_dt = rt_exp_dt.replace(tzinfo=timezone.utc)
        remaining = (rt_exp_dt - datetime.now(timezone.utc)).total_seconds()
        if remaining <= EXPIRY_BUFFER_SECONDS:
            log.debug("Refresh token expired or expiring soon (%.0f s remaining)", remaining)
            return None
        log.debug("Refresh token valid for %.0f more seconds (%.1f days)", remaining, remaining / 86400)
        return data.get("refresh_token")
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        log.warning("Could not read refresh token from cache: %s", exc)
        return None


def save_token_cache(
    id_token: str,
    refresh_token: Optional[str] = None,
    id_token_ttl: int = ID_TOKEN_TTL_SECONDS,
    refresh_token_ttl: int = REFRESH_TOKEN_TTL_SECONDS,
) -> None:
    """Persist tokens to .token_cache.json."""
    now = datetime.now(timezone.utc)
    data: dict = {
        "token": id_token,
        "token_type": "Bearer",
        "expires_at": (now + timedelta(seconds=id_token_ttl)).isoformat(),
    }
    if refresh_token:
        data["refresh_token"] = refresh_token
        data["refresh_token_expires_at"] = (
            now + timedelta(seconds=refresh_token_ttl)
        ).isoformat()

    # Preserve existing refresh_token if we don't have a new one
    if not refresh_token and TOKEN_CACHE_FILE.exists():
        try:
            with TOKEN_CACHE_FILE.open("r", encoding="utf-8") as f:
                existing = json.load(f)
            if existing.get("refresh_token"):
                data["refresh_token"] = existing["refresh_token"]
                data["refresh_token_expires_at"] = existing.get("refresh_token_expires_at", "")
        except (json.JSONDecodeError, KeyError):
            pass

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
    Preserves any existing refresh_token in the cache.
    The token is assumed to be valid for 1 hour from now.
    """
    save_token_cache(id_token, refresh_token=None, id_token_ttl=ID_TOKEN_TTL_SECONDS)
    log.info("Token injected manually (TTL=%ds, length=%d)", ID_TOKEN_TTL_SECONDS, len(id_token))


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    code_verifier = (
        base64.urlsafe_b64encode(secrets.token_bytes(32))
        .rstrip(b"=")
        .decode()
    )
    code_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    return code_verifier, code_challenge


# ---------------------------------------------------------------------------
# Login (full B2C PKCE flow)
# ---------------------------------------------------------------------------

def login(session: requests.Session, config: dict) -> tuple[str, Optional[str]]:
    """
    Perform Azure AD B2C PKCE authentication.

    Returns (id_token, refresh_token).
    The id_token is used as the Bearer value in costco-x-authorization.
    """
    tenant     = config["b2c_tenant"]
    policy     = config["b2c_policy"]
    client_id  = config["b2c_client_id"]
    redirect_uri = config["redirect_uri"]

    base = f"https://signin.costco.com/{tenant}/{policy}"
    code_verifier, code_challenge = _generate_pkce()
    nonce = secrets.token_urlsafe(16)
    state = secrets.token_urlsafe(16)

    # ------------------------------------------------------------------
    # Step 1 — GET authorize endpoint
    # ------------------------------------------------------------------
    auth_params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "openid profile offline_access",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "nonce": nonce,
        "state": state,
        "p": policy,
    }
    auth_url = f"{base}/oauth2/v2.0/authorize?" + urllib.parse.urlencode(auth_params)

    log.info("Initiating B2C login (tenant=%s, policy=%s)", tenant[:8] + "…", policy)
    log.debug("GET authorize URL: %s", auth_url)
    try:
        resp = session.get(auth_url, headers=_ua_headers(), timeout=30)
        resp.raise_for_status()
        log.debug("Authorize page HTTP %d, final URL: %s", resp.status_code, resp.url)
    except requests.RequestException as exc:
        log.exception("Failed to load B2C authorize page")
        raise RuntimeError(f"B2C authorize page unreachable: {exc}") from exc

    # CSRF token is set as a cookie by B2C
    csrf_token = session.cookies.get("x-ms-cpim-csrf", "")
    if not csrf_token:
        csrf_token = _extract_from_html(resp.text, "csrf")
    log.debug("CSRF token present: %s", bool(csrf_token))

    tx = _extract_tx(resp.url, resp.text)
    if not tx:
        log.error("Could not find tx/StateProperties in B2C login page (URL=%s)", resp.url)
        raise RuntimeError(
            "Could not extract B2C transaction ID (tx/StateProperties) "
            "from login page. The B2C policy URL may have changed."
        )
    log.debug("tx (StateProperties) extracted, length=%d", len(tx))

    # ------------------------------------------------------------------
    # Step 2 — POST credentials to SelfAsserted
    # ------------------------------------------------------------------
    username, password = get_credentials()
    selfasserted_url = (
        f"{base}/SelfAsserted"
        f"?tx={urllib.parse.quote(tx, safe='=')}&p={policy}"
    )
    log.info("Submitting credentials for %s", username)
    log.debug("POST SelfAsserted: %s", selfasserted_url)
    try:
        cred_resp = session.post(
            selfasserted_url,
            data={
                "request_type": "RESPONSE",
                "signInName": username,
                "password": password,
            },
            headers={
                **_ua_headers(),
                "X-CSRF-TOKEN": csrf_token,
                "Referer": resp.url,
            },
            timeout=30,
        )
        cred_resp.raise_for_status()
        log.debug("SelfAsserted HTTP %d", cred_resp.status_code)
    except requests.RequestException as exc:
        log.exception("SelfAsserted POST failed")
        raise RuntimeError(f"Credential submission failed: {exc}") from exc

    try:
        status_body = cred_resp.json()
        b2c_status = str(status_body.get("status", "200"))
        log.debug("SelfAsserted response body: %s", status_body)
        if b2c_status != "200":
            msg = status_body.get("message", str(status_body))
            log.error("B2C credential rejection: status=%s message=%s", b2c_status, msg)
            raise RuntimeError(f"B2C credential error: {msg}")
    except (ValueError, AttributeError):
        pass

    # ------------------------------------------------------------------
    # Step 3 — GET confirmed → follow redirect chain to capture auth code
    # ------------------------------------------------------------------
    confirmed_url = f"{base}/api/CombinedSigninAndSignup/confirmed"
    confirmed_params = {
        "rememberMe": "false",
        "csrf_token": csrf_token,
        "tx": tx,
        "p": policy,
    }
    log.info("Following authorization redirect chain…")
    code = None
    current_url = confirmed_url + "?" + urllib.parse.urlencode(confirmed_params)
    for hop_num in range(10):
        log.debug("Redirect hop %d: GET %s", hop_num + 1, current_url[:120])
        try:
            hop = session.get(
                current_url,
                headers={**_ua_headers(), "Referer": selfasserted_url},
                timeout=30,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            log.exception("Redirect hop %d failed", hop_num + 1)
            raise RuntimeError(f"Authorization redirect failed at hop {hop_num + 1}: {exc}") from exc

        location = hop.headers.get("Location", "")
        log.debug("Hop %d HTTP %d, Location: %s", hop_num + 1, hop.status_code, location[:120] if location else "(none)")
        if not location:
            log.warning("Redirect chain ended without auth code at hop %d (HTTP %d)", hop_num + 1, hop.status_code)
            break
        parsed_loc = urllib.parse.urlparse(location)
        qs = urllib.parse.parse_qs(parsed_loc.query)
        if "code" in qs:
            code = qs["code"][0]
            log.debug("Authorization code received (length=%d)", len(code))
            break
        current_url = location

    if not code:
        log.error("Authorization code not received after %d redirect hops", hop_num + 1)
        raise RuntimeError(
            "Authorization code not received after B2C redirect chain. "
            "The login may have failed or the B2C flow has changed."
        )

    # ------------------------------------------------------------------
    # Step 4 — Exchange code for tokens
    # ------------------------------------------------------------------
    token_url = f"{base}/oauth2/v2.0/token"
    log.info("Exchanging authorization code for tokens")
    log.debug("POST token endpoint: %s", token_url)
    try:
        token_resp = session.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code": code,
                "code_verifier": code_verifier,
            },
            headers=_ua_headers(),
            timeout=30,
        )
        token_resp.raise_for_status()
        log.debug("Token endpoint HTTP %d", token_resp.status_code)
    except requests.RequestException as exc:
        log.exception("Token endpoint request failed")
        raise RuntimeError(f"Token exchange failed: {exc}") from exc

    tokens = token_resp.json()
    log.debug("Token response fields: %s", list(tokens.keys()))

    id_token = tokens.get("id_token") or tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    if not id_token:
        log.error("Token endpoint returned no id_token. Keys: %s", list(tokens.keys()))
        raise RuntimeError(
            "Token endpoint did not return id_token or access_token. "
            f"Response keys: {list(tokens.keys())}"
        )

    log.info("Login successful (id_token length=%d, refresh_token present=%s)",
             len(id_token), bool(refresh_token))
    return id_token, refresh_token


# ---------------------------------------------------------------------------
# Token refresh (using stored refresh_token — no credentials needed)
# ---------------------------------------------------------------------------

def refresh_with_token(
    session: requests.Session,
    config: dict,
    refresh_token: str,
) -> tuple[str, Optional[str]]:
    """
    Use a refresh_token to obtain a new id_token silently.
    Returns (new_id_token, new_refresh_token).
    """
    tenant    = config["b2c_tenant"]
    policy    = config["b2c_policy"]
    client_id = config["b2c_client_id"]
    token_url = f"https://signin.costco.com/{tenant}/{policy}/oauth2/v2.0/token"

    log.info("Silently refreshing token via refresh_token")
    log.debug("POST token endpoint: %s", token_url)
    try:
        resp = session.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
            },
            headers=_ua_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        log.debug("Token refresh HTTP %d", resp.status_code)
    except requests.RequestException as exc:
        log.exception("Token refresh request failed")
        raise

    tokens = resp.json()
    log.debug("Token refresh response fields: %s", list(tokens.keys()))

    id_token = tokens.get("id_token") or tokens.get("access_token")
    new_refresh = tokens.get("refresh_token", refresh_token)

    if not id_token:
        log.error("Refresh response missing id_token. Keys: %s", list(tokens.keys()))
        raise RuntimeError(
            f"Refresh response missing id_token. Keys: {list(tokens.keys())}"
        )
    log.info("Token refreshed successfully (id_token length=%d)", len(id_token))
    return id_token, new_refresh


# ---------------------------------------------------------------------------
# High-level: get a valid token (cache → refresh → full login)
# ---------------------------------------------------------------------------

def get_valid_token(
    session: requests.Session,
    config: dict,
    force_refresh: bool = False,
) -> str:
    """
    Return a valid id_token, using the cheapest path available:
      1. Cached id_token (still valid)          → immediate, no network
      2. Cached refresh_token (still valid)     → one silent token request
      3. Full B2C login (email + password)      → multi-step browser-like flow
    """
    if not force_refresh:
        cached = load_token_cache()
        if cached:
            log.info("Using cached id_token")
            return cached["token"]

    if not force_refresh:
        rt = load_refresh_token()
        if rt:
            try:
                id_token, new_rt = refresh_with_token(session, config, rt)
                save_token_cache(id_token, new_rt)
                return id_token
            except Exception as exc:
                log.warning("Silent refresh failed (%s), falling back to full login", exc)

    log.info("Performing full B2C login")
    id_token, refresh_token = login(session, config)
    save_token_cache(id_token, refresh_token)
    return id_token


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ua_headers() -> dict:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }


def _extract_tx(url: str, html: str) -> Optional[str]:
    """
    Extract the B2C transaction ID (tx = StateProperties=...) from either:
    - The current URL's query string
    - A hidden form input named 'tx'
    - A JavaScript variable assignment like settings.csrf / StateProperties
    """
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    if "tx" in qs:
        log.debug("tx extracted from URL query string")
        return qs["tx"][0]

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("input", {"type": "hidden"}):
        if tag.get("name") == "tx":
            log.debug("tx extracted from hidden form input")
            return tag.get("value", "")

    import re
    m = re.search(r"StateProperties\s*[=:]\s*['\"]([^'\"]+)['\"]", html)
    if m:
        log.debug("tx extracted from JavaScript StateProperties variable")
        return f"StateProperties={m.group(1)}"

    for form in soup.find_all("form"):
        action = form.get("action", "")
        qs2 = urllib.parse.parse_qs(urllib.parse.urlparse(action).query)
        if "tx" in qs2:
            log.debug("tx extracted from form action URL")
            return qs2["tx"][0]

    log.warning("Could not find tx/StateProperties in page (URL=%s)", url)
    return None


def _extract_from_html(html: str, field_name: str) -> str:
    """Extract a hidden input value by name, or empty string."""
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("input", {"name": field_name})
    return tag.get("value", "") if tag else ""
