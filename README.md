# Costco Order Lookup

Search your entire Costco order history — both online orders and in-warehouse receipts — by item number. Results display in a rich table, JSON, or CSV.

---

## Quick Start

```
python main.py --inject-token    # paste token from Chrome (see below)
python main.py --item 1900477    # search for item
```

---

## Getting Your Auth Token from Chrome

Costco's login uses advanced bot protection that blocks automated sign-in from Python. The workaround is to grab your token directly from Chrome while you're already logged in — it takes about 30 seconds.

### Step 1 — Open Costco in Chrome and log in

Make sure you are signed in to your Costco account at [costco.com](https://www.costco.com).

### Step 2 — Open DevTools and go to the Network tab

Press **F12** (or **Cmd+Option+I** on Mac) to open Chrome DevTools.
Click the **Network** tab at the top.

### Step 3 — Navigate to Order History

In the Costco website, go to:
**Account → Orders & Purchases → Order History**

You will see network requests appear in the DevTools panel as the page loads.

### Step 4 — Find the order API request

In the DevTools Network panel:

1. Type `graphql` in the **filter box** at the top of the panel
2. Look for a request to `ecom-api.costco.com`
3. Click on that request

### Step 5 — Copy the token

1. Click the **Headers** tab in the request detail panel
2. Scroll down to **Request Headers**
3. Find the header named **`costco-x-authorization`**
4. The value will look like: `Bearer eyJhbGciOiJSUzI1NiIs...` (a long string)
5. **Copy everything after `Bearer `** — starting from `eyJ`

> **Tip:** Right-click the header value and choose **Copy value** to copy just the token without the `Bearer ` prefix.

### Step 6 — Inject the token into the app

**Option A — paste inline (easiest):**
```bash
python main.py --inject-token "eyJhbGciOiJSUzI1NiIs..."
```

**Option B — interactive prompt:**
```bash
python main.py --inject-token
# Paste the token when prompted, then press Enter twice
```

The token is saved locally and lasts **~1 hour**. After it expires, repeat steps 3–6 to get a fresh one.

---

## Installation

### Option 1 — Windows portable .exe (no Python required)

Download `costco-lookup.exe` from the [Actions tab](../../actions) (latest successful build).
Place it in a folder alongside `config.json`.

```
costco-lookup.exe --inject-token
costco-lookup.exe --item 1900477
```

### Option 2 — Windows source install

1. Install [Python 3.9+](https://www.python.org/downloads/) — check **"Add Python to PATH"**
2. Download or clone this repo
3. Double-click **`install.bat`**
4. Use the generated `costco-lookup.bat` launcher

```
costco-lookup.bat --inject-token
costco-lookup.bat --item 1900477
```

### Option 3 — Mac / Linux

```bash
pip install -r requirements.txt
python main.py --inject-token
python main.py --item 1900477
```

---

## Usage

```
python main.py --inject-token            # save token from Chrome DevTools
python main.py --inject-token "eyJ..."  # inject token inline
python main.py --item ITEM_NUMBER        # search by Costco item number
python main.py --item ITEM_NUMBER --output json
python main.py --item ITEM_NUMBER --output csv
python main.py --item ITEM_NUMBER --years 10   # search further back (default: 5)
python main.py --item ITEM_NUMBER --debug      # verbose logging to terminal
```

### Output columns

| Column | Description |
|--------|-------------|
| Source | `online` = costco.com order · `warehouse` = in-store receipt |
| Date | Order placed / receipt date |
| Order/Receipt ID | Order number or receipt barcode |
| Item # | Costco item number |
| Description | Item name (online orders only) |
| Status | Delivered, Shipped, Purchased, etc. |
| Order Total | Total for the order or receipt |
| Warehouse | Warehouse number (online) or name (warehouse) |
| Carrier | Shipping carrier (online orders) |
| Tracking # | Carrier tracking number (online orders) |
| Tender | Payment method (warehouse receipts) |

---

## Token Expiry & Renewal

The injected `id_token` lasts **~1 hour**. When it expires, the app will print a message and exit:

```
[error] No valid token found. Run --inject-token to save a fresh token from Chrome DevTools.
```

Simply repeat the Chrome DevTools steps above to get a new token. There is no automated renewal — Costco's bot protection blocks silent refresh requests.

Your token is stored in `.token_cache.json` in the same folder as the app. It is listed in `.gitignore` and never committed to the repository.

---

## Architecture

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
              │   GraphQLClient        │
              │   .execute(query, vars)│
              └───────────┬────────────┘
                          │ HTTP POST (requests.Session)
                          ▼
              ┌────────────────────────┐
              │   orders.py            │
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
              │  print_table()  (rich) │
              │  print_json()          │
              │  print_csv()           │
              └────────────────────────┘
```

For full architecture documentation — data flow diagrams, API details, data structures, search strategy, and PyInstaller build notes — see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Logs

Every run writes detailed logs to **`costco_lookup.log`** (in the same folder as the app).
Pass `--debug` to also print verbose output to the terminal.

```bash
# Review the last 50 log lines
tail -50 costco_lookup.log

# On Windows
Get-Content costco_lookup.log -Tail 50
```

---

## Building the Windows .exe Yourself

Requires Python + PyInstaller on a Windows machine, or use the automated GitHub Actions build:

1. Push to `main` on GitHub
2. Go to **Actions → Build Windows EXE → latest run**
3. Download the `costco-lookup-windows` artifact
4. Extract `costco-lookup.exe` and place it alongside `config.json`

To build locally on Windows:
```bat
pip install pyinstaller
pyinstaller build.spec --clean --noconfirm
```
Output: `dist\costco-lookup.exe`

---

## Project Structure

```
CostcoOrderLookup/
├── costco_lookup/
│   ├── auth.py       # token cache: load, save, inject, validate expiry
│   ├── client.py     # GraphQL HTTP client with Costco headers
│   ├── config.py     # config.json loader/saver
│   ├── display.py    # table / JSON / CSV output
│   ├── logger.py     # rotating file + console logging
│   ├── orders.py     # GraphQL queries + date chunking + response parsing
│   └── paths.py      # BASE_DIR for script and .exe modes
├── .github/
│   └── workflows/
│       └── build-windows.yml   # automated Windows .exe build
├── main.py           # CLI entry point
├── config.json       # API endpoints + warehouse number
├── requirements.txt
├── build.spec        # PyInstaller config
├── ARCHITECTURE.md   # component diagram, data flow, API details
└── install.bat       # Windows source installer
```

---

## Security Notes

- **Tokens** are cached in `.token_cache.json`, which is gitignored and never committed
- **HAR files** from Chrome contain sensitive data — never commit them
- **config.json** contains endpoint URLs and your warehouse number — no secrets
