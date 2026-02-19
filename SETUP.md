# Costco Order Lookup — Setup Guide

## 1. Prerequisites

```
pip install -r requirements.txt
```

---

## 2. Discover Costco API Endpoints via Chrome DevTools

You need two URLs from your browser before running `--setup`:

### Auth endpoint (login URL)

1. Open **Chrome** and go to [costco.com](https://www.costco.com).
2. Open **DevTools** (`F12` or `Ctrl+Shift+I`) → **Network** tab.
3. Check **Preserve log**.
4. Log in to your Costco account.
5. In the Network tab, filter by `XHR` or `Fetch`.
6. Look for a **POST** request that fires during login — it will contain your credentials and will likely be something like:
   - `https://www.costco.com/AjaxLogonForm`
   - or `https://www.costco.com/login`
7. Click the request → **Headers** → copy the **Request URL**.
8. This is your **`auth_endpoint`**.

### GraphQL endpoint (order history URL)

1. Stay in the **Network** tab (with Preserve log still on).
2. Navigate to **Orders** → **Order History** on costco.com.
3. In the Network tab, look for a **POST** request to a URL containing `graphql`.
4. Click it → **Headers** → copy the **Request URL**.
5. This is your **`graphql_endpoint`**.

### Copy the order query (for `orders.py`)

1. In the same GraphQL request → **Payload** tab.
2. Copy the `query` field value.
3. Open `costco_lookup/orders.py` and replace `ORDER_BY_ITEM_QUERY` with your copied query.
4. Update variable names in `find_orders_by_item()` and `_parse_response()` to match the real query.

---

## 3. First-Time Setup

```bash
python main.py --setup
```

You will be prompted for:
- Auth endpoint URL (from DevTools)
- GraphQL endpoint URL (from DevTools)
- Your Costco email
- Your Costco password (hidden input, stored in OS Credential Manager)

The tool will attempt a test login to confirm everything works.

---

## 4. Daily Usage

```bash
# Look up orders containing item number 123456
python main.py --item 123456

# JSON output (pipe-friendly)
python main.py --item 123456 --output json

# CSV output
python main.py --item 123456 --output csv

# Force token refresh (if you get auth errors)
python main.py --refresh-token
```

---

## 5. Token Caching

On the first successful login, a `.token_cache.json` file is created locally. It stores:
- The bearer token (or a sentinel if Costco uses session cookies)
- The expiry time

On subsequent runs the cached token is used automatically — no re-login required until it expires. The token is proactively refreshed 5 minutes before expiry. If a request returns HTTP 401, the client automatically re-authenticates once and retries.

`.token_cache.json` is in `.gitignore` and should never be committed.

---

## 6. Build a Portable Windows .exe

```bash
pip install pyinstaller
pyinstaller build.spec
```

The resulting `dist/costco-lookup.exe` runs on Windows without Python installed. Copy it alongside `config.json` — the config file must stay external so you can update endpoints without rebuilding.

---

## 7. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `config.json not found` | Run `python main.py --setup` |
| `No credentials found` | Run `python main.py --setup` |
| `Login test failed` | Double-check your `auth_endpoint` in config.json |
| `GraphQL errors` | Update `ORDER_BY_ITEM_QUERY` in `orders.py` with real query |
| `No orders found` | Check variable names in `orders.py` match real API response |
| HTTP 401 on every request | Run `python main.py --refresh-token` |
