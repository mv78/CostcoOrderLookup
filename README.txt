COSTCO ORDER LOOKUP
===================

Search your entire Costco order history -- both online orders and in-warehouse
receipts -- by item number or product description (full or partial match).
Results display in a table, JSON, or CSV.


QUICK START
-----------

  python main.py --inject-token              (paste token from Chrome -- see below)
  python main.py --item 1900477              (search by item number)
  python main.py --description "tires"       (search by product description)


================================================================
GETTING YOUR AUTH TOKEN FROM CHROME
================================================================

Costco's login blocks automated sign-in from Python. The workaround is to
grab your token directly from Chrome while you're already logged in.
Takes about 30 seconds.

STEP 1 -- Open Costco in Chrome and log in
-------------------------------------------
Make sure you are signed in at https://www.costco.com

STEP 2 -- Open DevTools and go to the Network tab
--------------------------------------------------
Press F12 (or Cmd+Option+I on Mac) to open Chrome DevTools.
Click the "Network" tab at the top of the DevTools panel.

STEP 3 -- Navigate to Order History
------------------------------------
On the Costco website go to:
  Account > Orders & Purchases > Order History

Network requests will appear in the DevTools panel as the page loads.

STEP 4 -- Find the order API request
--------------------------------------
In the DevTools Network panel:

  1. Type "graphql" in the filter box at the top of the panel
  2. Look for a request to "ecom-api.costco.com" (shows as "graphql")
  3. Click on that request to open its details

STEP 5 -- Copy the token
--------------------------
  1. Click the "Headers" tab in the request detail panel
  2. Scroll down to "Request Headers"
  3. Find the header named:  costco-x-authorization
  4. The value looks like:   Bearer eyJhbGciOiJSUzI1NiIs...  (very long)
  5. Copy everything AFTER "Bearer " -- starting from "eyJ"

  TIP: Right-click the header value and choose "Copy value" to copy
       just the token without the "Bearer " prefix.

STEP 6 -- Inject the token into the app
-----------------------------------------
Option A -- paste inline (easiest):

  python main.py --inject-token "eyJhbGciOiJSUzI1NiIs..."

Option B -- interactive prompt:

  python main.py --inject-token
  (paste the token when prompted, then press Enter twice)

The token is saved locally and lasts about 1 hour.
After it expires, repeat Steps 3-6 to get a fresh one.


================================================================
INSTALLATION
================================================================

Option 1 -- Windows portable .exe (no Python required)
-------------------------------------------------------
Download costco-lookup.exe from the GitHub Actions tab (latest build).
Place it in a folder alongside config.json.

  costco-lookup.exe --inject-token
  costco-lookup.exe --item 1900477

Option 2 -- Windows source install
------------------------------------
1. Install Python 3.9+ from https://www.python.org/downloads/
   Check "Add Python to PATH" during installation.
2. Download or clone this repo
3. Double-click install.bat
4. Use the generated costco-lookup.bat launcher:

  costco-lookup.bat --inject-token
  costco-lookup.bat --item 1900477

Option 3 -- Mac / Linux
-------------------------
  pip install -r requirements.txt
  python main.py --inject-token
  python main.py --item 1900477


================================================================
USAGE
================================================================

  python main.py --inject-token                      Save token from Chrome DevTools
  python main.py --inject-token "eyJ..."             Inject token inline
  python main.py --item ITEM_NUMBER                  Search by Costco item number
  python main.py --description "kirkland tires"      Search by product description (partial match)
  python main.py --item ITEM_NUMBER --output json
  python main.py --item ITEM_NUMBER --output csv
  python main.py --item ITEM_NUMBER --years 10       Search further back (default: 5 years)
  python main.py --description "tires" --years 3     Description search with custom range
  python main.py --item ITEM_NUMBER --debug          Verbose logging to terminal
  python main.py --item ITEM_NUMBER --download       Save HTML receipts and open in browser


OUTPUT COLUMNS
--------------
  Source          online = costco.com order | warehouse = in-store receipt
  Date            Order placed / receipt date
  Order/Receipt   Order number or receipt barcode
  Item #          Costco item number
  Description     Item name (online orders only)
  Status          Delivered, Shipped, Purchased, etc.
  Order Total     Total for the order or receipt
  Warehouse       Warehouse number (online) or name (warehouse)
  Carrier         Shipping carrier (online orders)
  Tracking #      Carrier tracking number (online orders)
  Tender          Payment method (warehouse receipts)


================================================================
TOKEN EXPIRY & RENEWAL
================================================================

  id_token        ~1 hour    Sent with every API request
  refresh_token   90 days    Used to silently obtain a new id_token

The app tries to silently renew your token using the refresh token (no
browser needed). If Costco blocks the renewal endpoint, it will tell you
to re-inject a token from Chrome.

Your token is stored in .token_cache.json in the app folder.
It is never committed to the repository.


================================================================
LOGS
================================================================

Every run writes detailed logs to costco_lookup.log (same folder as the app).
Pass --debug to also print verbose output to the terminal.

  Mac/Linux:  tail -50 costco_lookup.log
  Windows:    Get-Content costco_lookup.log -Tail 50


================================================================
BUILDING THE WINDOWS .EXE YOURSELF
================================================================

Using GitHub Actions (recommended):
  1. Push to main on GitHub
  2. Go to Actions > Build Windows EXE > latest run
  3. Download the costco-lookup-windows artifact
  4. Place costco-lookup.exe alongside config.json

Locally on Windows:
  pip install pyinstaller
  pyinstaller build.spec --clean --noconfirm
  Output: dist\costco-lookup.exe


================================================================
SECURITY NOTES
================================================================

  - Tokens are cached in .token_cache.json which is gitignored
  - HAR files from Chrome contain sensitive data -- never commit them
  - config.json contains endpoint URLs and your warehouse number -- no secrets
