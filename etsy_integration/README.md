\# Etsy Integration — Direct API for ERPNext



\## Overview



This custom app integrates Etsy stores directly with ERPNext, replacing the Make.com middleware dependency for order creation. It fetches orders from Etsy's API every 15 minutes, creates Sales Orders with correct pricing (subtotal + tax), proper Address documents, and auto-submits to trigger the C-Order → PO → F-Order chain.



Buyer email is captured separately via Make.com scenarios since the Etsy API doesn't return buyer email due to privacy restrictions.



\### Stores Integrated



| Store | Shop Name | Shop ID | Sales Channel |

|-------|-----------|---------|---------------|

| Maria | MariasCushionCorner | 40154721 | Etsy Maria |

| Zipcushions | ZIPCushions | 31937130 | Etsy Zipcushions |



\---



\## Architecture



\### Before (Make.com handles everything)



```

Etsy → Make.com → ERPNext webhook (etsy\_webhook.py)

```



Make.com watched Etsy for orders, processed the data, and sent it to ERPNext via webhook. Single point of failure.



\### After (Direct Integration + Make.com for email only)



```

Order Creation:  ERPNext Scheduler (every 15 min) → Etsy API → Creates Sales Order → Auto-submits

Email Capture:   Make.com (every 15 min) → Watches Etsy → Sends buyer email → ERPNext API

```



ERPNext handles order creation directly. Make.com only sends buyer email — a lightweight, non-critical task.



\---



\## Complete Order Flow



```

1\.  Scheduler runs every 15 minutes (hooks.py cron)

2\.  Calls Etsy API — fetches only NEW orders (since last fetch)

3\.  If token expired — auto-refreshes using refresh token

4\.  Creates Customer (if new buyer)

5\.  Creates Address document (Shipping type, linked to Customer)

6\.  Creates Item (if new product, non-stock)

7\.  Creates C-Order (Sales Order) with:

&#x20;   - shopify\_order\_number = receipt\_id

&#x20;   - Item rate = price minus coupon discount (subtotal)

&#x20;   - Tax row = Etsy sales tax (Actual type)

&#x20;   - Shipping address document linked

&#x20;   - custom\_sales\_channel = "Etsy Maria" or "Etsy Zipcushions"

8\.  Auto-submits the C-Order

9\.  Existing server scripts auto-trigger:

&#x20;   - "Sales Order Name" → sets C-{receipt\_id}

&#x20;   - "Auto Create PO from CCP SO" → creates PO at 40% rate

&#x20;   - "Auto Create FAM SO from CCP PO" → creates F-{receipt\_id}

10\. Logs result to Order Log (Success or Failed with error message)

11\. Updates last\_fetched timestamp



Separately (Make.com):

12\. Make.com Watch Shop Receipts triggers on new order

13\. Sends buyer\_email to ERPNext via contact\_update API

14\. ERPNext finds Sales Order by shopify\_order\_number and stores email

```



\---



\## File Structure



```

etsy\_integration/

├── api/

│   ├── \_\_init\_\_.py

│   ├── etsy\_webhook.py           ← Existing: Make.com webhook (kept for backward compatibility)

│   └── contact\_update.py         ← NEW: Receives buyer email from Make.com

├── tasks/

│   ├── \_\_init\_\_.py

│   ├── fetch\_maria\_orders.py     ← NEW: Maria store scheduler

│   └── fetch\_zip\_orders.py       ← NEW: Zipcushions store scheduler

├── utils/

│   ├── \_\_init\_\_.py

│   └── etsy\_api.py               ← NEW: Shared API helper functions

├── config/

├── etsy\_integration/

├── public/

├── templates/

├── hooks.py                      ← MODIFIED: Added scheduler\_events cron

├── modules.txt

├── patches.txt

├── \_\_init\_\_.py

└── README.md                     ← NEW: This file

```



\---



\## File Details



\### utils/etsy\_api.py — Shared Helper Functions



Contains 7 functions used by both store schedulers:



| Function | Purpose |

|----------|---------|

| `get\_settings(store)` | Reads API key, secret, token, shop ID from Settings doctype. Input: "maria" or "zipcushions" |

| `get\_headers(settings)` | Creates HTTP headers for Etsy API. Format: `Authorization: Bearer {token}`, `x-api-key: {key}:{secret}` |

| `refresh\_token(settings)` | Refreshes expired OAuth token (expires every 1 hour). Uses PKCE flow with `client\_id` = keystring only. Saves new access\_token + refresh\_token to Settings |

| `fetch\_orders(settings)` | Calls Etsy receipts API. Uses `min\_created` timestamp to fetch only new orders. Auto-refreshes token on 401 error |

| `format\_address(order)` | Formats Etsy address fields (first\_line, city, state, zip, country\_iso) into a newline-separated string |

| `get\_order\_date(timestamp)` | Converts Etsy unix timestamp to Python date using SQL `FROM\_UNIXTIME()` |

| `get\_item\_properties(txn)` | Formats item variations (Width: 25, Length: 59-61) or description into a string for custom\_shopify\_properties field |



\### tasks/fetch\_maria\_orders.py — Maria Store Scheduler



Contains 2 functions:



| Function | Purpose |

|----------|---------|

| `run()` | Entry point called by hooks.py every 15 min. Gets settings, fetches orders, processes each one, updates last\_fetched |

| `process\_order()` | Processes one Etsy receipt. Skips if already logged. Creates Customer and Address document. Collects all transactions into one items list. Creates ONE Sales Order with multiple item rows (matching production). Calculates rate (price - coupon). Adds tax row. Links shipping address. Auto-submits. Logs one entry per receipt |



\*\*Key identifiers:\*\*

\- PO No format: `ETSY-{receipt\_id}`

\- Sales Channel: `Etsy Maria`

\- Order Log doctype: `Etsy Maria Order Log`

\- Settings doctype: `Etsy Maria Settings`



\### tasks/fetch\_zip\_orders.py — Zipcushions Store Scheduler



Same logic as Maria with these differences:



| Field | Maria | Zipcushions |

|-------|-------|-------------|

| PO No prefix | `ETSY-` | `ETSYZ-` |

| Sales Channel | `Etsy Maria` | `Etsy Zipcushions` |

| Order Log | `Etsy Maria Order Log` | `Etsy Zipcushions Order Log` |

| Settings | `Etsy Maria Settings` | `Etsy Zipcushions Settings` |



\### api/contact\_update.py — Buyer Email Endpoint



Receives buyer email from Make.com and updates the Sales Order.



| Detail | Value |

|--------|-------|

| Endpoint | `POST /api/method/etsy\_integration.api.contact\_update.update\_contact` |

| Input | `{"receipt\_id": "4135871984", "email": "buyer@gmail.com"}` |

| What it does | Finds Sales Order by `shopify\_order\_number`, stores email in `custom\_buyer\_email` |

| Auth | Guest allowed (Make.com calls without ERPNext credentials) |



\### api/etsy\_webhook.py — Legacy Make.com Webhook



\*\*Not modified.\*\* Kept for backward compatibility. Previously used by Make.com to create Sales Orders. Can be removed after direct integration is verified on production.



\### hooks.py — Scheduler Registration



Added cron scheduler to run both store fetchers every 15 minutes:



```python

scheduler\_events = {

&#x20;   "cron": {

&#x20;       "\*/15 \* \* \* \*": \[

&#x20;           "etsy\_integration.tasks.fetch\_maria\_orders.run",

&#x20;           "etsy\_integration.tasks.fetch\_zip\_orders.run"

&#x20;       ]

&#x20;   }

}

```



\---



\## ERPNext Sales Order Mapping



\### From Etsy to Sales Order



| Etsy Field | ERPNext Field | Example |

|------------|---------------|---------|

| `receipt\_id` | `shopify\_order\_number` | 4135871984 |

| `name` (buyer) | `customer` | Michelle Pohl |

| `created\_timestamp` | `transaction\_date` | 2026-07-06 |

| `first\_line, city, state, zip` | Address document + `shipping\_address\_name` | Linked Address doc |

| Shop name | `custom\_sales\_channel` | Etsy Maria / Etsy Zipcushions |

| `price - shop\_coupon` | Item `rate` | $7.65 (after 15% discount) |

| `subtotal` | Sales Order `total` | $7.65 |

| `total\_tax\_cost` | `taxes` table (Actual type, US Sales Tax Payable) | $0.66 |

| `grandtotal` | `grand\_total` (auto-calculated: subtotal + tax) | $8.31 |

| `variations` | `custom\_shopify\_properties` | Width: 25, Length: 59-61 |

| `buyer\_email` (via Make.com) | `custom\_buyer\_email` | buyer@gmail.com |



\### Price Calculation



```

Etsy receipt:

&#x20; Item total:    $9.00  (original price)

&#x20; Coupon:       -$1.35  (15% off — shop\_coupon field)

&#x20; Subtotal:     $7.65  → Sales Order item rate

&#x20; Tax:           $0.66  → Sales Taxes and Charges row (Actual type)

&#x20; Order total:   $8.31  → Sales Order grand\_total (auto-calculated)

```



\### Address Document



For each order, an Address document is created:



| Field | Source | Example |

|-------|--------|---------|

| `address\_title` | `{customer} - {receipt\_id}` | Jennifer Reid - 4136829003 |

| `address\_type` | Always "Shipping" | Shipping |

| `address\_line1` | `first\_line` from Etsy | 109 Thomas Court |

| `city` | `city` from Etsy | WASHINGTON |

| `state` | `state` from Etsy | IL |

| `pincode` | `zip` from Etsy | 61571 |

| `country` | Mapped from `country\_iso` | United States |

| Link | Dynamic Link to Customer | Customer: Jennifer Reid |



The Address document is linked to the Sales Order via `shipping\_address\_name`.



\### C-Order / F-Order Chain



Every submitted Sales Order triggers existing server scripts (not modified by this integration):



```

C-Order submitted

&#x20; → "Sales Order Name" sets C-{receipt\_id}

&#x20; → "Auto Create PO from CCP SO" creates PO at 40% rate

&#x20; → PO auto-submits

&#x20; → "Auto Create FAM SO from CCP PO" creates F-{receipt\_id}

```



| Field | C-Order | F-Order |

|-------|---------|---------|

| Customer | Actual buyer | Cozy Corner Patios LLC |

| Company | Cozy Corner Patios LLC | Fabrics And More |

| SO Name | C-{receipt\_id} | F-{receipt\_id} |

| Rate | Actual price (after discount) | 40% of C-Order rate |

| PO Supplier | — | Fabrics And More(S) |



\---



\## Make.com Scenarios



\### Why Make.com is still needed



The Etsy API returns `buyer\_email: None` for all orders due to privacy restrictions. Make.com's Etsy module uses a different connection method that CAN access buyer email. Some buyers who use "Sign in with Apple" have Apple Private Relay emails which are hidden even from Make.com.



\### Scenario 1: Etsy Zipcushions — Contact Details



```

Module 1: Etsy Watch Shop Receipts (Zipcushions connection)

&#x20;   ↓

Module 2: HTTP POST to /api/method/update\_etsy\_contact

&#x20;   Body: {"receipt\_id": "{Receipt ID}", "email": "{buyer\_email}"}

```



\- Schedule: Every 15 minutes

\- `buyer\_email` field available directly from Etsy module



\### Scenario 2: Etsy Maria — Contact Details



```

Module 1: Etsy Watch Shop Receipts (Maria connection)

&#x20;   ↓

Module 2: HTTP POST to /api/method/update\_etsy\_contact

&#x20;   Body: {"receipt\_id": "{Receipt ID}", "email": "{buyer\_email}"}

```



\- Schedule: Every 15 minutes

\- `buyer\_email` available after running sample data ("Choose manually" first)



\### API Endpoint (Server Script on dev)



Currently implemented as a Server Script `Update Etsy Contact` on dev. The custom app file `api/contact\_update.py` provides the same functionality for production deployment.



| Environment | URL |

|-------------|-----|

| Dev | `POST dev.cozycornerpatios.com/api/method/update\_etsy\_contact` |

| Prod | `POST erp.cozycornerpatios.com/api/method/etsy\_integration.api.contact\_update.update\_contact` |



\---



\## Dependencies (Must exist on ERPNext site)



\### Custom Doctypes



| Doctype | Type | Purpose |

|---------|------|---------|

| Etsy Maria Settings | Single | Stores Maria API key, secret, shop ID, tokens, scheduler toggle |

| Etsy Zipcushions Settings | Single | Stores Zipcushions credentials |

| Etsy Maria Order Log | Regular | Logs every Maria order (Success/Failed) with receipt\_id, customer, subtotal, address, variations |

| Etsy Zipcushions Order Log | Regular | Logs every Zipcushions order |



\### Custom Fields



| Doctype | Field | Type | Purpose |

|---------|-------|------|---------|

| Sales Order | custom\_shopify\_order\_number | Data | Etsy receipt ID |

| Sales Order | custom\_sales\_channel | Data | "Etsy Maria" or "Etsy Zipcushions" |

| Sales Order | custom\_sales\_order\_name | Data | Auto-set: C-{receipt\_id} |

| Sales Order | custom\_ccp\_id | Data | Receipt ID (on F-Orders) |

| Sales Order | custom\_buyer\_email | Data | Buyer email (from Make.com) |

| Sales Order Item | custom\_shopify\_properties | Small Text | Item variations |

| Purchase Order | custom\_ccp\_id | Data | Receipt ID for F-Order linking |



\### Existing Server Scripts (NOT modified)



| Script | Event | Purpose |

|--------|-------|---------|

| Sales Order Name | Before Insert | Auto-sets C-{receipt\_id} from shopify\_order\_number |

| Auto Create PO from CCP SO | After Submit | Creates PO at 40% rate |

| Auto Create FAM SO from CCP PO | After Submit | Creates F-Order |



\### Tax Account



\- `US Sales Tax Payable - CCP - CCP` — must exist under Cozy Corner Patios LLC company



\---



\## OAuth Configuration



\### Token Flow



```

Access token expires every 1 hour

&#x20; → Scheduler catches 401 error

&#x20; → Calls refresh\_token() with refresh\_token

&#x20; → Gets new access\_token + new refresh\_token

&#x20; → Saves to Settings

&#x20; → Retries API call

```



\### Key Formats



| Purpose | Format | Example |

|---------|--------|---------|

| API calls (x-api-key) | `keystring:shared\_secret` | abc123:xyz789 |

| Token refresh (client\_id) | `keystring` only | abc123 |

| Authorization header | `Bearer {access\_token}` | Bearer 737062562.abc... |



\### Redirect URI



```

https://erp.cozycornerpatios.com/api/method/etsy\_oauth\_callback

```



\### Re-authorization



If refresh token expires (unused for 90 days), re-authorize via bench console:



```python

import hashlib, base64, os, urllib.parse



API\_KEY = "your\_keystring"

REDIRECT\_URI = "https://erp.cozycornerpatios.com/api/method/etsy\_oauth\_callback"



code\_verifier = base64.urlsafe\_b64encode(os.urandom(32)).rstrip(b'=').decode('utf-8')

code\_challenge = base64.urlsafe\_b64encode(hashlib.sha256(code\_verifier.encode()).digest()).rstrip(b'=').decode('utf-8')



params = {

&#x20;   "response\_type": "code",

&#x20;   "redirect\_uri": REDIRECT\_URI,

&#x20;   "scope": "transactions\_r shops\_r address\_r",

&#x20;   "client\_id": API\_KEY,

&#x20;   "state": "random123",

&#x20;   "code\_challenge": code\_challenge,

&#x20;   "code\_challenge\_method": "S256"

}



auth\_url = f"https://www.etsy.com/oauth/connect?{urllib.parse.urlencode(params)}"

\# Open URL, grant access, copy code from redirect URL

\# Exchange code for tokens using requests.post

```



\---



\## Duplicate Prevention



Orders are checked at 3 levels:



1\. \*\*Order Log check\*\* — `frappe.db.exists("Etsy Maria Order Log", {"receipt\_id": receipt\_id, "status": "Success"})`

2\. \*\*Sales Order check\*\* — `frappe.db.exists("Sales Order", {"po\_no": po\_no})`

3\. \*\*Timestamp filter\*\* — `min\_created` parameter only fetches orders newer than last run



\---



\## Country ISO Mapping



The integration maps Etsy's `country\_iso` codes to full country names for ERPNext Address documents:



```

US → United States, CA → Canada, GB → United Kingdom,

AU → Australia, DE → Germany, FR → France, IT → Italy,

ES → Spain, JP → Japan, MX → Mexico, BR → Brazil,

IN → India, and 15+ more countries

```



Unknown ISO codes are stored as-is.



\---



\## Monitoring



\### Dashboards



| Store | URL | Version |

|-------|-----|---------|

| Maria | `/etsy-maria-dashboard` | v5.0 |

| Zipcushions | `/etsy-zipcushions-dashboard` | v2.0 |



Features: stat cards (Successful, Failed, Today's Orders, Total Revenue, Emails Captured, Emails Missing, Last Scheduler Run, Last Email Received), status/customer filters, date range, pagination, email column in table, auto-refresh every 60 seconds.



\### Error Logs



Check `/app/error-log` and filter by "Etsy Maria" or "Etsy Zipcushions".



\### Settings



| Store | URL |

|-------|-----|

| Maria | `/app/etsy-maria-settings` |

| Zipcushions | `/app/etsy-zipcushions-settings` |



Check `last\_fetched` and `enable\_scheduler` fields.



\---



\## Deployment



\### On new server



```bash

\# 1. Install the app

cd \~/frappe-bench

bench get-app https://github.com/sahilvikas/Etsy-integration.git --branch etsy-direct-integration

bench --site sitename install-app etsy\_integration

bench restart



\# 2. Create Settings doctypes and configure credentials via System Console

\# 3. Create Order Log doctypes

\# 4. Create custom fields on Sales Order and Purchase Order

\# 5. Set enable\_scheduler = 1 in Settings

\# 6. Create Make.com scenarios for buyer email

```



\### Existing server



```bash

cd \~/frappe-bench/apps/etsy\_integration

git fetch origin

git checkout etsy-direct-integration

cd \~/frappe-bench

bench restart

```



\### Disable old Server Scripts after deployment



```python

for name in \["Fetch Etsy Maria Orders", "Fetch Etsy Zipcushions Orders",

&#x20;            "Refresh Etsy Maria Token", "Refresh Etsy Zipcushions Token"]:

&#x20;   script = frappe.get\_doc("Server Script", name)

&#x20;   script.disabled = 1

&#x20;   script.save(ignore\_permissions=True)

frappe.db.commit()

```



\---



\## Known Limitations



1\. \*\*Apple Private Relay emails\*\* — Buyers who use "Sign in with Apple" have masked emails (e.g., `8yhbfxeu9w@privaterelay.appleid.com`). These are hidden from both the direct API and Make.com. Only visible on the Etsy web dashboard.



2\. \*\*Buyer email not in direct API\*\* — Etsy API returns `buyer\_email: None` for all orders. Make.com is required for email capture.



3\. \*\*One Sales Order per receipt\*\* — If an Etsy receipt has multiple items, all items are grouped into ONE Sales Order with multiple item rows (matching production behavior). One Order Log entry is created per receipt.



\---



\## What's NOT Changed



| Component | Status |

|-----------|--------|

| `api/etsy\_webhook.py` | Untouched — available for backward compatibility |

| Existing server scripts | Untouched — C-Order/PO/F-Order chain works as before |

| Custom doctypes | Created via System Console, not in app files |

| Dashboards | Web Pages in database, not in app files |

