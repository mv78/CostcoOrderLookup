# Costco Order Lookup — Setup Guide

## 1. Prerequisites

```bash
pip install -r requirements.txt
```

---

## 2. Configure Your Warehouse Number

Open `config.json` and set your warehouse number (printed on any Costco receipt or membership card):

```json
{
  "warehouse_number": "847"
}
```

All other values in `config.json` are pre-populated and do not need to be changed.

---

## 3. Get Your Auth Token from Chrome

Costco's bot protection blocks automated login. The only supported auth path is copying a Bearer token from Chrome DevTools while already logged in.

1. Open **Chrome** and sign in to [costco.com](https://www.costco.com)
2. Press **F12** to open DevTools → **Network** tab
3. Navigate to **Account → Orders & Purchases → Order History**
4. In the Network filter box, type `graphql`
5. Click any request to `ecom-api.costco.com`
6. Go to **Headers → Request Headers**
7. Find **`costco-x-authorization`** — value starts with `Bearer eyJ...`
8. Copy everything **after** `Bearer ` (the `eyJ...` part)

Then inject the token:

```bash
python main.py --inject-token "eyJhbGciOiJSUzI1NiIs..."
```

Or use the interactive prompt:

```bash
python main.py --inject-token
# Paste the token when prompted, press Enter twice
```

The token is cached in `.token_cache.json` for ~1 hour.

---

## 4. Daily Usage

```bash
# Look up orders containing item number 1900477
python main.py --item 1900477

# JSON output (pipe-friendly)
python main.py --item 1900477 --output json

# CSV output
python main.py --item 1900477 --output csv

# Search further back (default is 5 years)
python main.py --item 1900477 --years 10

# Download HTML receipts/invoices and open them in your browser
python main.py --item 1900477 --download
```

`--download` fetches full receipt or order detail for each matched result, saves HTML files to the `invoices/` folder alongside the app, and automatically opens each file in your default browser. The output table gains an **Invoice** column (✓ = file saved).

When the token expires (~1 hour), repeat step 3 to get a fresh one.

---

## 5. Token Caching

On each run, the app reads `.token_cache.json` and checks the expiry timestamp. If valid, no network call is made for authentication. If expired or absent, the app exits with instructions to run `--inject-token`.

`.token_cache.json` is in `.gitignore` and should never be committed.

---

## 6. Build a Portable Windows .exe

```bat
pip install pyinstaller
pyinstaller build.spec --clean --noconfirm
```

The resulting `dist\costco-lookup.exe` runs on Windows without Python installed.
Copy it alongside `config.json` — the config file must stay external so you can update your warehouse number without rebuilding.

---

## 7. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `config.json not found` | Ensure `config.json` is in the same folder as the app |
| `warehouse_number` missing | Edit `config.json` and set `warehouse_number` |
| `No valid token found` | Run `--inject-token` with a fresh token from Chrome DevTools |
| `Token expired. Run --inject-token` | Token is >1 hour old; get a new one from Chrome |
| `GraphQL errors` | Token may be for a different Costco account or region |
| No results returned | Verify the item number; try `--years 10` to search further back |
| `.exe` can't find `config.json` | Place `config.json` in the same folder as `costco-lookup.exe` |
| `--download` saves file but doesn't open | Browser auto-open uses `webbrowser` stdlib; ensure a default browser is set in your OS |
| `--download` Invoice column missing | Download runs before display; if column absent, no files were saved (check log for errors) |
