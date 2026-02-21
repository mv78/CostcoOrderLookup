# Architecture

## Overview

Costco Order Lookup is a Python CLI and local web application that searches Costco order history by item number, spanning both online orders and in-warehouse receipts across multiple years. Authentication relies exclusively on a Bearer token manually copied from Chrome DevTools; Costco's bot-protection blocks all automated login flows.

Two entry points share the same core modules:
- **`main.py`** — CLI (`--item`, `--inject-token`, `--download`)
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
│                 --download   │   │  webbrowser.open()           │
│  cmd_inject_token()          │   └──────────────┬───────────────┘
└──────┬───────────────────────┘                  │
       │                                          ▼
       │                          ┌──────────────────────────────┐
       │                          │          web.py              │
       │                          │  Flask app factory           │
       │                          │  GET  /                      │
       │                          │  POST /inject-token          │
       │                          │  GET  /search                │
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
              │   orders.py            │
              │                        │
              │ find_orders_by_item()  │
              │  ├─ _build_date_chunks │
              │  ├─ _fetch_online_orders (paginated)
              │  └─ _fetch_receipts    │
              └───────────┬────────────┘
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
5. orders.find_orders_by_item()
   │
   ├─ _build_date_chunks(search_years)
   │   └─ [today-6mo..today], [today-12mo..today-6mo], … (6-month windows)
   │
   └─ For each chunk:
       ├─ _fetch_online_orders(client, item, warehouse, start, end)
       │   ├─ page = 1, loop until fetched >= total
       │   ├─ client.execute(GET_ONLINE_ORDERS_QUERY, vars)
       │   │   └─ HTTP POST → ecom-api.costco.com/graphql
       │   └─ filter bcOrders where orderLineItems[*].itemNumber == item
       │
       └─ _fetch_receipts(client, item, start, end)
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

## Data Flow: Token Injection (`--inject-token`)

```
User pastes token from Chrome DevTools
         │
         ▼
cmd_inject_token()
   ├─ Strip "Bearer " prefix if present
   └─ auth.inject_token(id_token)
         │
         ▼
   save_token_cache(id_token, ttl=3600)
         │
         ▼
   .token_cache.json written:
   {
     "token": "eyJ...",
     "token_type": "Bearer",
     "expires_at": "<now + 1 hour, UTC ISO>"
   }
```

---

## Module Reference

| Module | File | Responsibility |
|--------|------|----------------|
| `main` | `main.py` | CLI entry point; `build_parser()`, `cmd_lookup()`, `cmd_inject_token()` |
| `server` | `server.py` | Web UI entry point; starts Flask on `localhost:PORT`, auto-opens browser |
| `web` | `costco_lookup/web.py` | Flask app factory; 5 routes; reuses core modules directly |
| `auth` | `costco_lookup/auth.py` | Token cache: load, save, inject, validate expiry |
| `client` | `costco_lookup/client.py` | `GraphQLClient`: HTTP POST with Costco headers; 401 → RuntimeError |
| `orders` | `costco_lookup/orders.py` | GraphQL query strings; date chunking; search orchestration; response parsing |
| `display` | `costco_lookup/display.py` | Output: rich table, JSON, CSV; Invoice column when `--download` used |
| `downloader` | `costco_lookup/downloader.py` | HTML rendering for receipts/invoices; used by CLI `--download` and web `/receipt`, `/order` routes |
| `config` | `costco_lookup/config.py` | `load_config()` / `save_config()`: merge defaults, validate |
| `paths` | `costco_lookup/paths.py` | `BASE_DIR`: project root in dev, `.exe` folder when frozen |
| `logger` | `costco_lookup/logger.py` | `setup_logging(debug)`: rotating file + console handlers |

---

## Web UI

**Entry point:** `python server.py [--port 8080]`

Flask runs on `127.0.0.1:PORT` (localhost only). On startup, `webbrowser.open()` launches the default browser automatically.

**Routes:**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Index — token status banner, token injection form, search form |
| `POST` | `/inject-token` | Calls `auth.inject_token(token)`; redirects to `/` |
| `GET` | `/search?item=X&years=N` | Runs `orders.find_orders_by_item()`; renders results table |
| `GET` | `/receipt/<barcode>` | Fetches warehouse receipt via `RECEIPT_DETAIL_QUERY`; returns full HTML (new tab) |
| `GET` | `/order/<order_number>` | Fetches online order via `ORDER_DETAIL_QUERY`; returns full HTML (new tab) |

**Template folder:** `costco_lookup/templates/` — resolved via `BASE_DIR` (not `__file__`) for PyInstaller compatibility.

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
  "token":      "eyJ...",
  "token_type": "Bearer",
  "expires_at": "2025-02-20T11:30:00.000000+00:00"
}
```

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
├── index.html            ← token injection form + search form
└── results.html          ← rich results table with source/status badges
```

**`BASE_DIR` resolution (`paths.py`):**

| Context | `BASE_DIR` |
|---------|-----------|
| Dev (script mode) | project root (`main.py`'s directory) |
| Frozen (`.exe`) | directory containing `costco-lookup.exe` |

This ensures config, token, and log files always live next to the executable rather than in PyInstaller's ephemeral temp directory.

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| `config.json` missing or no `warehouse_number` | Print error, `sys.exit(1)` |
| Token expired or cache absent | Print error with `--inject-token` instructions, `sys.exit(1)` |
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
