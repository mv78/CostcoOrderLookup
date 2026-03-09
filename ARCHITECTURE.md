# Architecture

## Overview

Costco Order Lookup is a Python CLI and local web application that searches Costco order history by item number **or product description**, spanning both online orders and in-warehouse receipts across multiple years. Authentication relies exclusively on a Bearer token manually copied from Chrome DevTools; Costco's bot-protection blocks all automated login flows.

Two entry points share the same core modules:
- **`main.py`** — CLI (`--item`, `--description`, `--inject-token`, `--download`)
- **`server.py`** — Flask web UI on `localhost:8080` (default)

---

## Component Diagram

```
┌──────────────────────────────┐   ┌──────────────────────────────┐
│        User / Shell          │   │       User / Browser         │
└──────────────┬───────────────┘   └──────────────┬───────────────┘
               │  CLI args                         │  HTTP
               ▼                                   ▼
┌──────────────────────────────┐   ┌──────────────────────────────┐
│           main.py            │   │          server.py           │
│  build_parser()              │   │  argparse --port (def 8080)  │
│  cmd_lookup()   --item       │   │  create_app()                │
│  cmd_lookup_by_description() │   │  webbrowser.open()           │
│                 --download   │   └──────────────┬───────────────┘
│  cmd_inject_token()          │                  │
└──────┬───────────────────────┘                  ▼
       │                          ┌──────────────────────────────┐
       │                          │          web.py              │
       │                          │  Flask app factory           │
       │                          │  GET  /                      │
       │                          │  POST /inject-token          │
       │                          │  GET  /search          (→ loading.html)
       │                          │  GET  /search/stream   (SSE)
       │                          │  GET  /search/results/<id>
       │                          │  GET  /receipt/<barcode>     │
       │                          │  GET  /order/<order_number>  │
       └──────────────────────────┴──────────────────────────────┘
                                           │  shared modules
                                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                        Shared Core                               │
└──────┬──────────────────────┬──────────────────────┬────────────┘
       │                      │                      │
       │                      │                      │
       ▼                      ▼                      ▼
┌─────────────┐    ┌──────────────────┐   ┌─────────────────────┐
│  config.py  │    │     auth.py      │   │      auth.py        │
│             │    │                  │   │                     │
│ load_config │    │ get_valid_token() │   │  inject_token()     │
│ save_config │    │ load_token_cache()│   │  save_token_cache() │
└──────┬──────┘    └────────┬─────────┘   └──────────┬──────────┘
       │                    │                         │
       ▼                    ▼                         ▼
┌────────────┐    ┌──────────────────┐       ┌───────────────────┐
│ config.json│    │ .token_cache.json│       │ .token_cache.json │
└────────────┘    └────────┬─────────┘       └───────────────────┘
                           │ valid id_token
                           ▼
              ┌────────────────────────┐
              │       client.py        │
              │                        │
              │   GraphQLClient        │
              │   .execute(query, vars)│
              └───────────┬────────────┘
                          │ HTTP POST (requests.Session)
                          ▼
              ┌────────────────────────┐
              │   orders.py                        │
              │                                    │
              │ find_orders_by_item()              │
              │  ├─ _build_date_chunks             │
              │  ├─ _fetch_online_orders (paged)   │
              │  └─ _fetch_receipts                │
              │                                    │
              │ find_orders_by_description()       │
              │  ├─ _build_date_chunks             │
              │  ├─ _fetch_online_orders_by_desc   │
              │  ├─ _fetch_receipt_summaries       │
              │  └─ _fetch_receipt_detail_by_desc  │
              │       (per receipt, all fetched)   │
              └───────────┬────────────────────────┘
                          │ GraphQL POST ×N chunks
                          ▼
              ┌────────────────────────┐
              │  ecom-api.costco.com   │
              │  /ebusiness/order/v1/  │
              │  orders/graphql        │
              └───────────┬────────────┘
                          │ JSON response
                          ▼
              ┌────────────────────────┐      ┌──────────────────────┐
              │      display.py        │      │     downloader.py    │
              │                        │      │  (--download only)   │
              │  print_table()  (rich) │      │                      │
              │  print_json()          │      │ download_documents() │
              │  print_csv()           │      │  ├─ RECEIPT_DETAIL   │
              └────────────────────────┘      │  │  _QUERY (warehouse│
                                              │  └─ ORDER_DETAIL     │
                                              │     _QUERY (online)  │
                                              │ _generate_warehouse_ │
                                              │   html()             │
                                              │ _generate_online_    │
                                              │   html()             │
                                              └──────────┬───────────┘
                                                         │ HTML files
                                                         ▼
                                              ┌──────────────────────┐
                                              │  BASE_DIR/invoices/  │
                                              └──────────────────────┘
```

---

## Data Flow: Item Lookup (`--item`)

```
1. Parse CLI args
      │
      ▼
2. cfg.load_config()
   ├─ Read config.json (must exist)
   ├─ Merge with DEFAULT_CONFIG
   └─ Validate warehouse_number present
      │
      ▼
3. auth.get_valid_token()
   ├─ Read .token_cache.json
   ├─ Check expires_at vs. now (300 s buffer)
   ├─ Valid  → return id_token
   └─ Expired/missing → raise RuntimeError ("run --inject-token")
      │
      ▼
4. GraphQLClient(session, config, token)
   └─ Stores: endpoint, headers template, token
      │
      ▼
5. orders.find_orders_by_item(..., on_progress=cb)
   │
   ├─ _build_date_chunks(search_years)
   │   └─ [today-6mo..today], [today-12mo..today-6mo], … (6-month windows)
   │   total = len(chunks) × 2
   │
   └─ For each chunk (calls on_progress after each):
       ├─ _fetch_online_orders(client, item, warehouse, start, end, ...)
       │   ├─ page = 1, loop until fetched >= total
       │   ├─ client.execute(GET_ONLINE_ORDERS_QUERY, vars)
       │   │   └─ HTTP POST → ecom-api.costco.com/graphql
       │   └─ filter bcOrders where orderLineItems[*].itemNumber == item
       │
       └─ _fetch_receipts(client, item, start, end, ...)
           ├─ client.execute(RECEIPTS_WITH_COUNTS_QUERY, vars)
           │   └─ HTTP POST → ecom-api.costco.com/graphql
           └─ filter receipts where itemArray[*].itemNumber == item
      │
      ▼
6. Merge + sort results by date (descending)
      │
      ▼
7. display.print_table() / print_json() / print_csv()
      │
      ▼
8. (optional) downloader.download_documents()   [only with --download]
   │
   ├─ For each result record:
   │   ├─ warehouse → client.execute(RECEIPT_DETAIL_QUERY, {barcode, documentType})
   │   │   └─ _generate_warehouse_html() → Costco receipt layout + Code128 barcode SVG
   │   └─ online   → client.execute(ORDER_DETAIL_QUERY, {orderNumbers})
   │       └─ _generate_online_html() → order summary with line items, tracking, payment
   │
   └─ Write BASE_DIR/invoices/{item}_{source}_{id}_{date}.html
      (skips existing files; logs warning on per-record failure)
```

---

## Data Flow: Description Search (`--description`)

```
1–4. Same as Item Lookup (config → token → GraphQLClient)
      │
      ▼
5. orders.find_orders_by_description(query, ..., on_progress=cb)
   │
   ├─ _build_date_chunks(search_years)
   │   Phase 1 total = len(chunks) × 2
   │
   ├─ Phase 1a — Online orders (per chunk):
   │   _fetch_online_orders_by_description(client, query, warehouse, start, end)
   │   ├─ Fetch ALL online orders (full pagination, no item filter)
   │   └─ Filter: query.lower() in itemDescription.lower()
   │   on_progress(++current, phase1_total, msg) after each chunk
   │
   ├─ Phase 1b — Receipt summaries (per chunk):
   │   _fetch_receipt_summaries(client, start, end, warehouse)
   │   ├─ RECEIPTS_WITH_COUNTS_QUERY — returns all receipts, no filter
   │   └─ Collect all_receipts list
   │   on_progress(++current, phase1_total, msg) after each chunk
   │
   │   ← at this point all_receipts count is known
   │   total updated: phase1_total + len(all_receipts)
   │
   └─ Phase 2 — Receipt details (per receipt):
       _fetch_receipt_detail_by_description(client, receipt, query)
       ├─ RECEIPT_DETAIL_QUERY → itemArray with itemDescription01/02
       ├─ Filter: query.lower() in (desc01 + " " + desc02).lower()
       └─ Returns normalized record or None
       on_progress(++current, updated_total, msg) after each receipt
      │
      ▼
6–8. Same merge/sort/display/download as Item Lookup
```

**Performance note:** Phase 2 issues one `RECEIPT_DETAIL_QUERY` per receipt in the date range regardless of match. For 5 years of history this is typically 50–200 HTTP calls. Progress percentage reflects this cost accurately.

---

## Data Flow: Web Search with SSE Progress

```
Browser                     Flask (web.py)              orders.py
   │                              │                          │
   ├─ GET /search?item=X ────────►│                          │
   │                              ├─ return loading.html     │
   │◄──── loading.html ───────────┤                          │
   │                              │                          │
   ├─ EventSource /search/stream ►│                          │
   │                              ├─ Thread: run_search()    │
   │                              │    ├─ find_orders_by_*() │
   │◄── data: {progress,5,20} ────┤◄── on_progress(5,20,msg)─┤
   │◄── data: {progress,10,45} ───┤◄── on_progress(10,45,…) ─┤
   │      (total grows as        │    │  (receipt count known)│
   │       receipts are counted) │    │                       │
   │◄── data: {done, search_id} ──┤    └─ results stored in   │
   │                              │       _result_cache[id]   │
   ├─ GET /search/results/<id> ──►│                          │
   │◄── results.html ─────────────┤ pop from cache + render  │
```

**Result cache:** `_result_cache: dict` at module level in `web.py`, protected by `threading.Lock()`. Entry is single-use — popped on first read. `search_id = secrets.token_hex(8)`.

**SSE event schema:**

```json
{"type": "progress", "current": 10, "total": 45, "message": "Checking receipt 2023-06-14..."}
{"type": "done",     "search_id": "a3f9c1b2"}
{"type": "error",    "message":   "Token expired. Run --inject-token."}
```

---

## Data Flow: Token Injection (`--inject-token`)

```
User pastes token from Chrome DevTools
         │
         ▼
cmd_inject_token()
   ├─ Strip "Bearer " prefix if present
   ├─ Prompt for optional refresh_token
   └─ auth.inject_token(id_token, refresh_token=...)
         │
         ▼
   save_token_cache(id_token, ttl=3600, refresh_token=...)
         │
         ▼
   .token_cache.json written:
   {
     "token": "eyJ...",
     "token_type": "Bearer",
     "expires_at": "<now + 1 hour, UTC ISO>",
     "refresh_token": "eyJ..."   ← optional, present only when provided
   }
```

## Data Flow: Token Auto-Renewal

When `get_valid_token()` is called and the cached `id_token` is expired:

```
get_valid_token()
   ├─ load_token_cache()   → None (expired)
   ├─ _load_cache_raw()    → check for refresh_token
   │
   ├─ refresh_token present?
   │    YES → refresh_access_token()
   │             POST https://signin.costco.com/.../oauth2/v2.0/token
   │             grant_type=refresh_token
   │             client_id=a3a5186b-...
   │             → new id_token + rotated refresh_token
   │             save_token_cache(new_id_token, refresh_token=new_refresh_token)
   │             return new id_token
   │
   └─ refresh_token absent or refresh failed
        → raise RuntimeError("Run --inject-token")
```

Azure AD B2C uses **rotating refresh tokens** — each successful refresh invalidates the old refresh token and issues a new one. `save_token_cache()` always persists the latest refresh token.

---

## Module Reference

| Module | File | Responsibility |
|--------|------|----------------|
| `main` | `main.py` | CLI entry point; `build_parser()`, `cmd_lookup()`, `cmd_lookup_by_description()`, `cmd_inject_token()` |
| `server` | `server.py` | Web UI entry point; starts Flask on `localhost:PORT`, auto-opens browser |
| `web` | `costco_lookup/web.py` | Flask app factory; 7 routes; SSE progress stream; in-memory result cache |
| `auth` | `costco_lookup/auth.py` | Token cache: load, save, inject, validate expiry; auto-refresh via Azure AD B2C refresh token |
| `client` | `costco_lookup/client.py` | `GraphQLClient`: HTTP POST with Costco headers; 401 → RuntimeError |
| `orders` | `costco_lookup/orders.py` | GraphQL query strings; date chunking; `find_orders_by_item()` and `find_orders_by_description()`; parallel chunk execution via `ThreadPoolExecutor`; `on_progress` callback protocol |
| `display` | `costco_lookup/display.py` | Output: rich table, JSON, CSV; Invoice column when `--download` used |
| `downloader` | `costco_lookup/downloader.py` | HTML rendering for receipts/invoices; used by CLI `--download` and web `/receipt`, `/order` routes |
| `config` | `costco_lookup/config.py` | `load_config()` / `save_config()`: merge defaults, validate |
| `paths` | `costco_lookup/paths.py` | `BASE_DIR` (runtime files) and `TEMPLATE_DIR` (bundled assets): each resolves correctly in both script and frozen `.exe` modes |
| `logger` | `costco_lookup/logger.py` | `setup_logging(debug)`: rotating file + console handlers |

---

## Web UI

**Entry point:** `python server.py [--port 8080]`

Flask runs on `127.0.0.1:PORT` (localhost only). On startup, `webbrowser.open()` launches the default browser automatically.

**Routes:**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Index — token status banner, token injection form (+ optional refresh token), search form with item/description toggle |
| `POST` | `/inject-token` | Calls `auth.inject_token(token, refresh_token=...)`; redirects to `/` |
| `GET` | `/search` | Returns `loading.html` immediately; accepts `item=` or `description=` + `years=` |
| `GET` | `/search/stream` | SSE endpoint; runs `find_orders_by_item` or `find_orders_by_description` in a daemon thread; streams progress/done/error events |
| `GET` | `/search/results/<search_id>` | Pops results from `_result_cache`; renders `results.html`; 404-redirects if cache miss |
| `GET` | `/receipt/<barcode>` | Fetches warehouse receipt via `RECEIPT_DETAIL_QUERY`; returns full HTML (new tab) |
| `GET` | `/order/<order_number>` | Fetches online order via `ORDER_DETAIL_QUERY`; returns full HTML (new tab) |

**Template folder:** resolved via `TEMPLATE_DIR` from `paths.py` — in frozen mode this is `sys._MEIPASS/costco_lookup/templates/` (where PyInstaller extracts bundled data), not `BASE_DIR` (which is the `.exe` folder for runtime files only).

**No CDN dependencies** — all CSS is inline in `base.html`; works fully offline and inside the `.exe`.

**Port note:** Default port is `8080`. Port `5000` is avoided — macOS Monterey+ reserves it for AirPlay Receiver (`ControlCenter`).

**Shared modules:** `web.py` calls `auth`, `config`, `client`, `orders`, and `downloader` directly — no code duplication between CLI and web paths.

---

## Key Data Structures

### Order record (produced by `orders.py`, consumed by `display.py`)

```python
{
    "source":        "online" | "warehouse",
    "order_id":      str,   # sourceOrderNumber / transactionBarcode
    "date":          str,   # "YYYY-MM-DD"
    "item_number":   str,
    "description":   str,
    "status":        str,   # "Delivered", "Purchased", …
    "carrier":       str,   # "—" for warehouse receipts
    "tracking":      str,   # "—" for warehouse receipts
    "receipt_total": str,   # "$123.45"
    "warehouse":     str,
    "tender":        str,   # "Visa $100.00, Amex $50.00" / "—"
    # Added by downloader.download_documents() when --download is used:
    "invoice_path":  str,   # absolute path to saved HTML file (optional)
}
```

`invoice_path` is stamped onto the record in-place by `downloader.download_documents()` before `display.py` renders output. `display.py` adds an **Invoice** column (✓) only when any record has this key. `print_json` includes it automatically; `print_csv` adds it to fieldnames dynamically.

### Token cache (`.token_cache.json`)

```json
{
  "token":         "eyJ...",
  "token_type":    "Bearer",
  "expires_at":    "2025-02-20T11:30:00.000000+00:00",
  "refresh_token": "eyJ..."
}
```

`refresh_token` is optional. When present, `get_valid_token()` automatically calls `refresh_access_token()` on expiry instead of raising an error. Azure AD B2C rotates the refresh token on each use — the latest value is always saved back to cache.

### Config (`config.json`)

```json
{
  "graphql_endpoint":  "https://ecom-api.costco.com/ebusiness/order/v1/orders/graphql",
  "client_identifier": "481b1aec-aa3b-454b-b81b-48187e28f205",
  "wcs_client_id":     "4900eb1f-0c10-4bd9-99c3-c59e6c1ecebf",
  "token_header_name": "costco-x-authorization",
  "warehouse_number":  "847"
}
```

(`b2c_*` and `redirect_uri` fields remain in the file but are unused after removing the automated login flow.)

---

## API Details

**Endpoint:** `POST https://ecom-api.costco.com/ebusiness/order/v1/orders/graphql`

**Required headers:**

| Header | Value |
|--------|-------|
| `Content-Type` | `application/json-patch+json` |
| `costco-x-authorization` | `Bearer <id_token>` |
| `client-identifier` | `481b1aec-aa3b-454b-b81b-48187e28f205` |
| `costco-x-wcs-clientId` | `4900eb1f-0c10-4bd9-99c3-c59e6c1ecebf` |
| `costco.env` | `ecom` |
| `costco.service` | `restOrders` |
| `Origin` | `https://www.costco.com` |

**Queries (all defined verbatim from captured HARs in `orders.py`):**

| Query constant | GraphQL operation | Used by | Pagination |
|----------------|------------------|---------|------------|
| `GET_ONLINE_ORDERS_QUERY` | `getOnlineOrders` | search | Yes — `pageNumber` / `pageSize` (50) |
| `RECEIPTS_WITH_COUNTS_QUERY` | `receiptsWithCounts` | search | No |
| `RECEIPT_DETAIL_QUERY` | `receiptsWithCounts` | `--download` (warehouse) | No |
| `ORDER_DETAIL_QUERY` | `getOrderDetails` | `--download` (online) | No |

**`ORDER_DETAIL_QUERY` structure note:** Address fields are flat on the order object (not a sub-object). Line items are nested under `orderShipTos[].orderLineItems[]`. Field aliases used: `orderNumber: sourceOrderNumber`, `orderPlacedDate: orderedDate`, `itemDescription: sourceItemDescription`, `quantity: orderedTotalQuantity`.

**Date format quirk:**

| Query | Format | Example |
|-------|--------|---------|
| `getOnlineOrders` | `YYYY-M-D` (no zero-padding) | `2025-2-3` |
| `receiptsWithCounts` | `M/DD/YYYY` | `2/03/2025` |

---

## Search Strategy

Orders are searched in **6-month chunks** going backwards from today, up to `--years` years (default 5). This stays within API limits that reject very wide date ranges.

```
today ─────────────────────────────────────────── today - N years
  │        │        │        │        │        │
[chunk 1][chunk 2][chunk 3][chunk 4][chunk 5]  …
 0-6mo   6-12mo  12-18mo  18-24mo  24-30mo
```

Both queries run for every chunk. Results from all chunks are merged and sorted by date descending before display.

A failed chunk (e.g., network error) is logged as a warning and skipped; the rest still complete.

### Parallelization

All chunk fetches are **independent** — no result depends on another within the same phase. Both public search functions use `concurrent.futures.ThreadPoolExecutor` (stdlib, no new dependency) to run chunks concurrently.

**`find_orders_by_item`** — a single pool submits all `len(chunks) × 2` tasks (online + receipt per chunk) at once:

```
ThreadPoolExecutor(max_workers=min(total_tasks, 5))
  ├── _fetch_online_chunk(chunk 1) ──┐
  ├── _fetch_receipt_chunk(chunk 1) ─┤
  ├── _fetch_online_chunk(chunk 2) ──┼─ collected via as_completed()
  ├── _fetch_receipt_chunk(chunk 2) ─┤
  └── …                             ┘
```

**`find_orders_by_description`** — two sequential pools:

```
Pool 1 (max 5 workers) — Phase 1: online desc chunks + receipt summary chunks
  └── all complete → all_receipt_summaries known → Phase 2 total calculated

Pool 2 (max 8 workers) — Phase 2: one task per receipt detail fetch
  └── results filtered by description, appended to results list
```

**Thread safety:**
- Each worker creates its own `requests.Session` + `GraphQLClient` via `_make_client(token, config)` — avoids shared session state
- `on_progress` is wrapped in a `threading.Lock` + shared counter list before being passed to workers
- Results are collected from `as_completed()` in the main thread — no concurrent list writes

**Worker caps:**

| Pool | `max_workers` | Rationale |
|------|--------------|-----------|
| Item search | `min(chunks × 2, 5)` | Balanced API load; typically 10 chunks = 20 tasks |
| Description Phase 1 | `min(chunks × 2, 5)` | Same |
| Description Phase 2 | `min(len(receipts), 8)` | Bigger win here; 8 caps concurrent detail fetches |

**Why not reactive streams / asyncio:** `requests` is synchronous; adapting to `asyncio` would require replacing the HTTP stack (`httpx`/`aiohttp`) and rewriting all callers. `ThreadPoolExecutor` achieves the same parallelism with zero new dependencies and no architectural disruption.

### Description search chunking

`find_orders_by_description()` uses the same 6-month chunks. Phase 1 (online + receipt summaries) runs in parallel across all chunks in a single pool. Phase 2 (receipt detail fetches) runs in a second parallel pool after Phase 1 completes — the receipt count is not known until Phase 1 finishes, so the progress `total` is updated before Phase 2 begins.

### Description matching — normalization

Both the search query and item descriptions are normalized before comparison via `_normalize()` in `orders.py`:

```python
_PUNCT_TABLE = str.maketrans("", "", string.punctuation)

def _normalize(text: str) -> str:
    return (text or "").lower().translate(_PUNCT_TABLE)
```

This strips all `string.punctuation` characters (`` !"#$%&'()*+,-./:;<=>?@[\]^_`{|}~ ``) and lowercases the result. Raw descriptions are stored in result records unchanged — normalization is applied only at match time.

**Examples:**
- Query `"delonghi"` matches `"De'Longhi"` → normalized: `delonghi`
- Query `"kirkland"` matches `"Kirkland's Signature"` → normalized: `kirklands signature`
- Query `"2 pack"` matches `"2-Pack"` → normalized: `2pack`

### Progress callback protocol

Both public search functions accept `on_progress=None`:

```python
on_progress(current: int, total: int, message: str) -> None
```

- `None` → no-op (backward compatible — existing callers unaffected)
- CLI: Rich `Progress` bar supplied as the callback
- Web: enqueues JSON dict into a `queue.Queue`; SSE generator drains the queue

---

## File Layout at Runtime

```
BASE_DIR/
├── config.json           ← user config (warehouse number + API defaults)
├── .token_cache.json     ← injected Bearer token + expiry  [gitignored]
├── costco_lookup.log     ← rotating file log, always DEBUG  [gitignored]
└── invoices/             ← HTML receipts/invoices (created by --download)  [gitignored]
    └── {item}_{source}_{order_id}_{date}.html

# Web UI templates (bundled into .exe via build.spec datas)
BASE_DIR/costco_lookup/templates/
├── base.html             ← nav, token banner, flash messages, inline CSS
├── index.html            ← token injection form + search form (item/description toggle)
├── loading.html          ← animated progress bar; EventSource JS → /search/stream
└── results.html          ← rich results table; handles both item and description search types
```

**Path resolution (`paths.py`):**

Two distinct path constants serve different purposes:

| Constant | Dev (script mode) | Frozen (`.exe`) | Purpose |
|----------|-------------------|-----------------|---------|
| `BASE_DIR` | project root | directory containing the `.exe` | Mutable runtime files: `config.json`, `.token_cache.json`, `costco_lookup.log`, `invoices/` |
| `TEMPLATE_DIR` | `costco_lookup/templates/` | `sys._MEIPASS/costco_lookup/templates/` | Read-only bundled assets: Jinja2 templates |

**Critical distinction:** PyInstaller extracts bundled `datas[]` entries to `sys._MEIPASS` (an ephemeral temp directory), not to the `.exe` folder. Using `BASE_DIR` for templates in frozen mode causes `TemplateNotFound` because templates are never copied alongside the `.exe`. `TEMPLATE_DIR` always resolves to wherever PyInstaller actually places the bundled files.

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| `config.json` missing or no `warehouse_number` | Print error, `sys.exit(1)` |
| Token expired, refresh_token present | `get_valid_token()` silently calls `refresh_access_token()` and returns new token |
| Token expired, no refresh_token | Print error with `--inject-token` instructions, `sys.exit(1)` |
| Token refresh fails (B2C error) | Warning logged; falls through to `--inject-token` error |
| HTTP 401 from API | `RuntimeError`: "Token expired. Run --inject-token." |
| Other HTTP error | `RuntimeError` with status; logged + printed; `sys.exit(1)` |
| GraphQL `errors` array in response | `RuntimeError` with extracted messages |
| Individual chunk fetch failure | Warning logged; chunk skipped; search continues |

---

## Logging

Two handlers configured by `setup_logging(debug)` in `logger.py`:

| Handler | Path | Level | Trigger |
|---------|------|-------|---------|
| `RotatingFileHandler` | `BASE_DIR/costco_lookup.log` | DEBUG | Always |
| `StreamHandler` (stderr) | — | DEBUG or INFO | `--debug` flag |

Rotation: 5 MB per file, 3 backups kept (~15 MB total). Noisy third-party loggers (`urllib3`, `requests`, `charset_normalizer`) are suppressed to WARNING.

---

## PyInstaller Build

`build.spec` produces a single-file Windows executable (`dist/costco-lookup.exe`).

Key packaging decisions:
- `collect_all('rich')` — required because `rich` loads locale data via `importlib.import_module()` at runtime, which static analysis misses.
- `dateutil`, `requests`, `certifi`, `charset_normalizer`, `idna` added as hidden imports.
- `python-barcode` used in `downloader.py` for Code128 SVG generation; no additional PyInstaller hooks needed (pure Python).
- `config.json` is **not** bundled — it stays external so users can edit `warehouse_number`.
- `.token_cache.json`, `costco_lookup.log`, and `invoices/` are created at runtime next to the `.exe`.

Build command:
```bash
pyinstaller build.spec --clean --noconfirm
```
