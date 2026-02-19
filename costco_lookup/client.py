"""
client.py — GraphQL HTTP client with Costco-specific headers and 401 retry.

Required headers discovered from HAR:
  Content-Type:           application/json-patch+json
  costco-x-authorization: Bearer <id_token>
  client-identifier:      481b1aec-aa3b-454b-b81b-48187e28f205
  costco-x-wcs-clientId:  4900eb1f-0c10-4bd9-99c3-c59e6c1ecebf
  costco.env:             ecom
  costco.service:         restOrders
"""

import logging
import time
from typing import Callable, Optional

import requests

log = logging.getLogger(__name__)


class GraphQLClient:
    """
    Executes GraphQL queries against the Costco ecom API.

    On HTTP 401, calls `on_token_refresh()` once to get a fresh token
    and retries the request automatically.
    """

    def __init__(
        self,
        session: requests.Session,
        config: dict,
        token: str,
        on_token_refresh: Optional[Callable[[], str]] = None,
    ):
        self._session = session
        self._endpoint = config["graphql_endpoint"]
        self._token_header = config.get("token_header_name", "costco-x-authorization")
        self._client_identifier = config.get("client_identifier", "")
        self._wcs_client_id = config.get("wcs_client_id", "")
        self._token = token
        self._on_token_refresh = on_token_refresh

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def execute(self, query: str, variables: Optional[dict] = None) -> dict:
        """
        Execute a GraphQL operation. Returns the full parsed JSON response.
        Raises RuntimeError on HTTP errors or GraphQL-level errors.
        """
        operation = _extract_operation_name(query)
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        log.debug("GraphQL execute: operation=%s variables=%s", operation, variables)

        t0 = time.monotonic()
        response = self._post(payload)
        elapsed = time.monotonic() - t0
        log.debug("HTTP %d in %.2fs (operation=%s)", response.status_code, elapsed, operation)

        if response.status_code == 401:
            log.warning("HTTP 401 on operation=%s — attempting token refresh", operation)
            if self._on_token_refresh:
                self._token = self._on_token_refresh()
                t0 = time.monotonic()
                response = self._post(payload)
                elapsed = time.monotonic() - t0
                log.debug("Retry HTTP %d in %.2fs", response.status_code, elapsed)
            if response.status_code == 401:
                log.error("Authentication failed (401) even after token refresh for operation=%s", operation)
                raise RuntimeError(
                    "Authentication failed (401). "
                    "Run --refresh-token or --setup to re-authenticate."
                )

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            log.error("HTTP error on operation=%s: %s", operation, exc)
            log.debug("Response body: %s", response.text[:500])
            raise RuntimeError(f"GraphQL request failed: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            log.error("Non-JSON response for operation=%s: %s", operation, response.text[:200])
            raise RuntimeError(f"Invalid JSON response: {exc}") from exc

        if "errors" in data and data["errors"]:
            messages = "; ".join(e.get("message", str(e)) for e in data["errors"])
            log.error("GraphQL errors on operation=%s: %s", operation, messages)
            raise RuntimeError(f"GraphQL errors: {messages}")

        log.debug("operation=%s returned successfully", operation)
        return data

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_headers(self) -> dict:
        return {
            "Content-Type": "application/json-patch+json",
            "Accept": "application/json",
            self._token_header: f"Bearer {self._token}",
            "client-identifier": self._client_identifier,
            "costco-x-wcs-clientId": self._wcs_client_id,
            "costco.env": "ecom",
            "costco.service": "restOrders",
            "Origin": "https://www.costco.com",
            "Referer": "https://www.costco.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
        }

    def _post(self, payload: dict) -> requests.Response:
        return self._session.post(
            self._endpoint,
            json=payload,
            headers=self._build_headers(),
            timeout=30,
        )


def _extract_operation_name(query: str) -> str:
    """Pull the operation name from a GraphQL query string, or return '?'."""
    import re
    m = re.search(r"\b(query|mutation)\s+(\w+)", query)
    return m.group(2) if m else "?"
