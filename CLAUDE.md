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

# Inject a bearer token from Chrome DevTools (required before first use)
python main.py --inject-token "eyJ..."
python main.py --inject-token   # interactive prompt

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

Authentication is done exclusively via `--inject-token`: the user copies the `costco-x-authorization` Bearer token from Chrome DevTools and the app caches it in `.token_cache.json` (gitignored). The token is assumed valid for ~1 hour from injection time.

Costco's bot-protection reliably blocks automated Azure AD B2C login flows, so only the manual token injection path is supported.

`get_valid_token()` in `auth.py` checks the cache and raises `RuntimeError` with instructions to run `--inject-token` if no valid token is found.

The `id_token` is sent as `costco-x-authorization: Bearer <id_token>` on every API request.

### Data Flow

`main.py` → `GraphQLClient` (`client.py`) → Costco ecom GraphQL API (`ecom-api.costco.com`)

`orders.py` contains the verbatim GraphQL queries (`getOnlineOrders`, `receiptsWithCounts`) captured from Chrome HAR. The search spans a configurable number of years, split into 6-month chunks to work within API limits. Both query types are run for every chunk, and results are merged and sorted by date.

### Key Files

| File | Responsibility |
|------|----------------|
| `main.py` | CLI argument parsing and command dispatch |
| `costco_lookup/auth.py` | Token cache read/write; `inject_token`; `get_valid_token` |
| `costco_lookup/client.py` | GraphQL HTTP client; raises RuntimeError on 401 |
| `costco_lookup/orders.py` | GraphQL query strings, date chunking, response parsing |
| `costco_lookup/display.py` | Output formatting: rich table, JSON, CSV |
| `costco_lookup/config.py` | `config.json` load/save with defaults merged in |
| `costco_lookup/paths.py` | `BASE_DIR` — resolves to `.exe` folder when frozen by PyInstaller, or project root in script mode |
| `costco_lookup/logger.py` | Rotating file logger (`costco_lookup.log`) + optional console output |
| `config.json` | API endpoints and user's `warehouse_number` |

### PyInstaller / Frozen Mode

`paths.py` is critical for the Windows `.exe` build. PyInstaller onefile mode extracts to a temp dir (`sys._MEIPASS`), so `Path(__file__)` would resolve there. `BASE_DIR` detects the frozen environment and uses `Path(sys.executable).parent` instead, keeping `config.json`, `.token_cache.json`, and `costco_lookup.log` alongside the executable. The GitHub Actions workflow (`.github/workflows/build-windows.yml`) automates this build.

### config.json

All API endpoint values are pre-populated. The only user-supplied field is `warehouse_number`. `config.py` merges file contents with `DEFAULT_CONFIG` so new keys added to defaults are automatically present.
