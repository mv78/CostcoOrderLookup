# Architecture

## Overview

Costco Order Lookup is a Python CLI that searches Costco order history by item number, spanning both online orders and in-warehouse receipts across multiple years. Authentication relies exclusively on a Bearer token manually copied from Chrome DevTools; Costco's bot-protection blocks all automated login flows.

---

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                          User / Shell                           │
└────────────────────────┬────────────────────────────────────────┘
                         │  CLI args
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                         main.py                                 │
│                                                                 │
│   build_parser()          cmd_lookup()     cmd_inject_token()   │
│   (argparse)              --item           --inject-token       │
└──────┬──────────────────────┬──────────────────────┬───────────┘
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
              ┌────────────────────────┐
              │      display.py        │
              │                        │
              │  print_table()  (rich) │
              │  print_json()          │
              │  print_csv()           │
              └────────────────────────┘
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
| `auth` | `costco_lookup/auth.py` | Token cache: load, save, inject, validate expiry |
| `client` | `costco_lookup/client.py` | `GraphQLClient`: HTTP POST with Costco headers; 401 → RuntimeError |
| `orders` | `costco_lookup/orders.py` | GraphQL query strings; date chunking; search orchestration; response parsing |
| `display` | `costco_lookup/display.py` | Output: rich table, JSON, CSV |
| `config` | `costco_lookup/config.py` | `load_config()` / `save_config()`: merge defaults, validate |
| `paths` | `costco_lookup/paths.py` | `BASE_DIR`: project root in dev, `.exe` folder when frozen |
| `logger` | `costco_lookup/logger.py` | `setup_logging(debug)`: rotating file + console handlers |

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
}
```

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

**Queries (both defined verbatim from a captured HAR in `orders.py`):**

| Query | Source | Pagination |
|-------|--------|------------|
| `getOnlineOrders` | Online orders | Yes — `pageNumber` / `pageSize` (50) |
| `receiptsWithCounts` | Warehouse receipts | No |

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
└── costco_lookup.log     ← rotating file log, always DEBUG  [gitignored]
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
- `config.json` is **not** bundled — it stays external so users can edit `warehouse_number`.
- `.token_cache.json` and `costco_lookup.log` are created at runtime next to the `.exe`.

Build command:
```bash
pyinstaller build.spec --clean --noconfirm
```
