# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the CLI (primary usage)
python main.py --item ITEM_NUMBER
python main.py --item ITEM_NUMBER --output json
python main.py --item ITEM_NUMBER --output csv
python main.py --item ITEM_NUMBER --years 10

# First-time setup (warehouse number + credentials via OS keyring)
python main.py --setup

# Inject a bearer token from Chrome DevTools (main auth workaround)
python main.py --inject-token "eyJ..."
python main.py --inject-token   # interactive prompt

# Force token refresh
python main.py --refresh-token

# Verbose logging to terminal
python main.py --item ITEM_NUMBER --debug

# Build Windows .exe (Windows only or via GitHub Actions)
pip install pyinstaller
pyinstaller build.spec --clean --noconfirm
# Output: dist\costco-lookup.exe
```

There is no test suite or linter configured.

## Architecture

This is a Python CLI that searches Costco order history (both online orders and in-store warehouse receipts) by Costco item number, across multiple years.

### Authentication

Costco uses Azure AD B2C with PKCE, hosted at `signin.costco.com`. The automated login flow in `auth.py` mimics a browser:
1. GET `/oauth2/v2.0/authorize` — gets CSRF cookie and B2C transaction ID
2. POST `/SelfAsserted` — submits email + password
3. GET `/api/CombinedSigninAndSignup/confirmed` — follows redirect chain to capture auth code
4. POST `/oauth2/v2.0/token` — exchanges code for `id_token` + `refresh_token`

The `id_token` is sent as `costco-x-authorization: Bearer <id_token>` on every API request.

**Because Costco bot-protection often blocks the automated flow**, the primary workaround is `--inject-token`: the user copies the token directly from Chrome DevTools and the app caches it.

Token resolution order in `get_valid_token()`:
1. Cached `id_token` (valid ~1 hour) — no network
2. Cached `refresh_token` (valid 90 days) — one silent request
3. Full B2C login — multi-step browser-like flow

Credentials (email/password) are stored in the OS keyring (`keyring` library). Tokens are cached in `.token_cache.json` (gitignored).

### Data Flow

`main.py` → `GraphQLClient` (`client.py`) → Costco ecom GraphQL API (`ecom-api.costco.com`)

`orders.py` contains the verbatim GraphQL queries (`getOnlineOrders`, `receiptsWithCounts`) captured from Chrome HAR. The search spans a configurable number of years, split into 6-month chunks to work within API limits. Both query types are run for every chunk, and results are merged and sorted by date.

### Key Files

| File | Responsibility |
|------|----------------|
| `main.py` | CLI argument parsing and command dispatch |
| `costco_lookup/auth.py` | B2C PKCE auth flow, OS keyring, token cache read/write |
| `costco_lookup/client.py` | GraphQL HTTP client; handles 401 with one automatic token refresh |
| `costco_lookup/orders.py` | GraphQL query strings, date chunking, response parsing |
| `costco_lookup/display.py` | Output formatting: rich table, JSON, CSV |
| `costco_lookup/config.py` | `config.json` load/save with defaults merged in |
| `costco_lookup/paths.py` | `BASE_DIR` — resolves to `.exe` folder when frozen by PyInstaller, or project root in script mode |
| `costco_lookup/logger.py` | Rotating file logger (`costco_lookup.log`) + optional console output |
| `config.json` | API endpoints, B2C tenant/policy/client IDs, and user's `warehouse_number` |

### PyInstaller / Frozen Mode

`paths.py` is critical for the Windows `.exe` build. PyInstaller onefile mode extracts to a temp dir (`sys._MEIPASS`), so `Path(__file__)` would resolve there. `BASE_DIR` detects the frozen environment and uses `Path(sys.executable).parent` instead, keeping `config.json`, `.token_cache.json`, and `costco_lookup.log` alongside the executable. The GitHub Actions workflow (`.github/workflows/build-windows.yml`) automates this build.

### config.json

All B2C and API endpoint values are pre-populated. The only user-supplied field is `warehouse_number` (set via `--setup`). `config.py` merges file contents with `DEFAULT_CONFIG` so new keys added to defaults are automatically present.
