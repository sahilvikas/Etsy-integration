# Etsy Integration — Direct API for ERPNext

## Overview

This custom app integrates Etsy stores directly with ERPNext, replacing the Make.com middleware dependency for order creation. It fetches orders from Etsy's API every 15 minutes, creates Sales Orders with correct pricing (subtotal + tax), proper Address documents, and auto-submits to trigger the C-Order → PO → F-Order chain.

Buyer email is captured separately via Make.com scenarios since the Etsy API doesn't return buyer email due to privacy restrictions.

### Stores Integrated

| Store | Shop Name | Shop ID | Sales Channel |
|-------|-----------|---------|---------------|
| Maria | MariasCushionCorner | 40154721 | Etsy Maria |
| Zipcushions | ZIPCushions | 31937130 | Etsy Zipcushions |

---

## Architecture

### Before (Make.com handles everything)

```
Etsy → Make.com → ERPNext webhook (etsy_webhook.py)
```

Make.com watched Etsy for orders, processed the data, and sent it to ERPNext via webhook. Single point of failure.

### After (Direct Integration + Make.com for email only)

```
Order Creation:  ERPNext Scheduler (every 15 min) → Etsy API → Creates Sales Order → Auto-submits
Email Capture:   Make.com (every 15 min) → Watches Etsy → Sends buyer email → ERPNext API
```

ERPNext handles order creation directly. Make.com only sends buyer email — a lightweight, non-critical task.

---

## Complete Order Flow

```
1.  Scheduler runs every 15 minutes (hooks.py cron)
2.  Calls Etsy API — fetches only NEW orders (since last fetch)
3.  If token expired — auto-refreshes using refresh token
4.  Creates Customer (if new buyer)
5.  Creates Address document (Shipping type, linked to Customer)
6.  Creates Item (if new product, non-stock)
7.  Creates C-Order (Sales Order) with:
    - shopify_order_number = receipt_id
    - Item rate = price minus coupon discount (subtotal)
    - Tax row = Etsy sales tax (Actual type)
    - Shipping address document linked
    - custom_sales_channel = "Etsy Maria" or "Etsy Zipcushions"
8.  Auto-submits the C-Order
9.  Existing server scripts auto-trigger:
    - "Sales Order Name" → sets C-{receipt_id}
    - "Auto Create PO from CCP SO" → creates PO at 40% rate
    - "Auto Create FAM SO from CCP PO" → creates F-{receipt_id}
10. Logs result to Order Log (Success or Failed with error message)
11. Updates last_fetched timestamp

Separately (Make.com):
12. Make.com Watch Shop Receipts triggers on new order
13. Sends buyer_email to ERPNext via contact_update API
14. ERPNext finds Sales Order by shopify_order_number and stores email
```

---

## File Structure

```
etsy_integration/
├── api/
│   ├── __init__.py
│   ├── etsy_webhook.py           ← Existing: Make.com webhook (kept for backward compatibility)
│   └── contact_update.py         ← NEW: Receives buyer email from Make.com
├── tasks/
│   ├── __init__.py
│   ├── fetch_maria_orders.py     ← NEW: Maria store scheduler
│   └── fetch_zip_orders.py       ← NEW: Zipcushions store scheduler
├── utils/
│   ├── __init__.py
│   └── etsy_api.py               ← NEW: Shared API helper functions
├── config/
├── etsy_integration/
├── public/
├── templates/
├── hooks.py                      ← MODIFIED: Added scheduler_events cron
├── modules.txt
├── patches.txt
├── __init__.py
└── README.md                     ← NEW: This file
```

---

## File Details

### utils/etsy_api.py — Shared Helper Functions

Contains 7 functions used by both store schedulers:

| Function | Purpose |
|----------|---------|
| `get_settings(store)` | Reads API key, secret, token, shop ID from Settings doctype. Input: "maria" or "zipcushions" |
| `get_headers(settings)` | Creates HTTP headers for Etsy API. Format: `Authorization: Bearer {token}`, `x-api-key: {key}:{secret}` |
| `refresh_token(settings)` | Refreshes expired OAuth token (expires every 1 hour). Uses PKCE flow with `client_id` = keystring only. Saves new access_token + refresh_token to Settings |
| `fetch_orders(settings)` | Calls Etsy receipts API. Uses `min_created` timestamp to fetch only new orders. Auto-refreshes token on 401 error |
| `format_address(order)` | Formats Etsy address fields (first_line, city, state, zip, country_iso) into a newline-separated string |
| `get_order_date(timestamp)` | Converts Etsy unix timestamp to Python date using SQL `FROM_UNIXTIME()` |
| `get_item_properties(txn)` | Formats item variations (Width: 25, Length: 59-61) or description into a string for custom_shopify_properties field |

### tasks/fetch_maria_orders.py — Maria Store Scheduler

Contains 3 functions:

| Function | Purpose |
|----------|---------|
| `run()` | Entry point called by hooks.py every 15 min. Gets settings, fetches orders, processes each one, updates last_fetched |
| `process_order()` | Processes one Etsy receipt. Skips if already logged. Creates Customer and Address document. Extracts subtotal, tax, address. Loops through transactions |
| `process_transaction()` | Creates one Sales Order per transaction. Calculates rate (price - coupon). Adds tax row. Links shipping address document. Inserts, submits, logs result |

**Key identifiers:**
- PO No format: `ETSY-{receipt_id}-{transaction_id}`
- Sales Channel: `Etsy Maria`
- Order Log doctype: `Etsy Maria Order Log`
- Settings doctype: `Etsy Maria Settings`

### tasks/fetch_zip_orders.py — Zipcushions Store Scheduler

Same logic as Maria with these differences:

| Field | Maria | Zipcushions |
|-------|-------|-------------|
| PO No prefix | `ETSY-` | `ETSYZ-` |
| Sales Channel | `Etsy Maria` | `Etsy Zipcushions` |
| Order Log | `Etsy Maria Order Log` | `Etsy Zipcushions Order Log` |
| Settings | `Etsy Maria Settings` | `Etsy Zipcushions Settings` |

### api/contact_update.py — Buyer Email Endpoint

Receives buyer email from Make.com and updates the Sales Order.

| Detail | Value |
|--------|-------|
| Endpoint | `POST /api/method/etsy_integration.api.contact_update.update_contact` |
| Input | `{"receipt_id": "4135871984", "email": "buyer@gmail.com"}` |
| What it does | Finds Sales Order by `shopify_order_number`, stores email in `custom_buyer_email` |
| Auth | Guest allowed (Make.com calls without ERPNext credentials) |

### api/etsy_webhook.py — Legacy Make.com Webhook

**Not modified.** Kept for backward compatibility. Previously used by Make.com to create Sales Orders. Can be removed after direct integration is verified on production.

### hooks.py — Scheduler Registration

Added cron scheduler to run both store fetchers every 15 minutes:

```python
scheduler_events = {
    "cron": {
        "*/15 * * * *": [
            "etsy_integration.tasks.fetch_maria_orders.run",
            "etsy_integration.tasks.fetch_zip_orders.run"
        ]
    }
}
```

---

## ERPNext Sales Order Mapping

### From Etsy to Sales Order

| Etsy Field | ERPNext Field | Example |
|------------|---------------|---------|
| `receipt_id` | `shopify_order_number` | 4135871984 |
| `name` (buyer) | `customer` | Michelle Pohl |
| `created_timestamp` | `transaction_date` | 2026-07-06 |
| `first_line, city, state, zip` | Address document + `shipping_address_name` | Linked Address doc |
| Shop name | `custom_sales_channel` | Etsy Maria / Etsy Zipcushions |
| `price - shop_coupon` | Item `rate` | $7.65 (after 15% discount) |
| `subtotal` | Sales Order `total` | $7.65 |
| `total_tax_cost` | `taxes` table (Actual type, US Sales Tax Payable) | $0.66 |
| `grandtotal` | `grand_total` (auto-calculated: subtotal + tax) | $8.31 |
| `variations` | `custom_shopify_properties` | Width: 25, Length: 59-61 |
| `buyer_email` (via Make.com) | `custom_buyer_email` | buyer@gmail.com |

### Price Calculation

```
Etsy receipt:
  Item total:    $9.00  (original price)
  Coupon:       -$1.35  (15% off — shop_coupon field)
  Subtotal:     $7.65  → Sales Order item rate
  Tax:           $0.66  → Sales Taxes and Charges row (Actual type)
  Order total:   $8.31  → Sales Order grand_total (auto-calculated)
```

### Address Document

For each order, an Address document is created:

| Field | Source | Example |
|-------|--------|---------|
| `address_title` | `{customer} - {receipt_id}` | Jennifer Reid - 4136829003 |
| `address_type` | Always "Shipping" | Shipping |
| `address_line1` | `first_line` from Etsy | 109 Thomas Court |
| `city` | `city` from Etsy | WASHINGTON |
| `state` | `state` from Etsy | IL |
| `pincode` | `zip` from Etsy | 61571 |
| `country` | Mapped from `country_iso` | United States |
| Link | Dynamic Link to Customer | Customer: Jennifer Reid |

The Address document is linked to the Sales Order via `shipping_address_name`.

### C-Order / F-Order Chain

Every submitted Sales Order triggers existing server scripts (not modified by this integration):

```
C-Order submitted
  → "Sales Order Name" sets C-{receipt_id}
  → "Auto Create PO from CCP SO" creates PO at 40% rate
  → PO auto-submits
  → "Auto Create FAM SO from CCP PO" creates F-{receipt_id}
```

| Field | C-Order | F-Order |
|-------|---------|---------|
| Customer | Actual buyer | Cozy Corner Patios LLC |
| Company | Cozy Corner Patios LLC | Fabrics And More |
| SO Name | C-{receipt_id} | F-{receipt_id} |
| Rate | Actual price (after discount) | 40% of C-Order rate |
| PO Supplier | — | Fabrics And More(S) |

---

## Make.com Scenarios

### Why Make.com is still needed

The Etsy API returns `buyer_email: None` for all orders due to privacy restrictions. Make.com's Etsy module uses a different connection method that CAN access buyer email. Some buyers who use "Sign in with Apple" have Apple Private Relay emails which are hidden even from Make.com.

### Scenario 1: Etsy Zipcushions — Contact Details

```
Module 1: Etsy Watch Shop Receipts (Zipcushions connection)
    ↓
Module 2: HTTP POST to /api/method/update_etsy_contact
    Body: {"receipt_id": "{Receipt ID}", "email": "{buyer_email}"}
```

- Schedule: Every 15 minutes
- `buyer_email` field available directly from Etsy module

### Scenario 2: Etsy Maria — Contact Details

```
Module 1: Etsy Watch Shop Receipts (Maria connection)
    ↓
Module 2: HTTP POST to /api/method/update_etsy_contact
    Body: {"receipt_id": "{Receipt ID}", "email": "{buyer_email}"}
```

- Schedule: Every 15 minutes
- `buyer_email` available after running sample data ("Choose manually" first)

### API Endpoint (Server Script on dev)

Currently implemented as a Server Script `Update Etsy Contact` on dev. The custom app file `api/contact_update.py` provides the same functionality for production deployment.

| Environment | URL |
|-------------|-----|
| Dev | `POST dev.cozycornerpatios.com/api/method/update_etsy_contact` |
| Prod | `POST erp.cozycornerpatios.com/api/method/etsy_integration.api.contact_update.update_contact` |

---

## Dependencies (Must exist on ERPNext site)

### Custom Doctypes

| Doctype | Type | Purpose |
|---------|------|---------|
| Etsy Maria Settings | Single | Stores Maria API key, secret, shop ID, tokens, scheduler toggle |
| Etsy Zipcushions Settings | Single | Stores Zipcushions credentials |
| Etsy Maria Order Log | Regular | Logs every Maria order (Success/Failed) with receipt_id, customer, subtotal, address, variations |
| Etsy Zipcushions Order Log | Regular | Logs every Zipcushions order |

### Custom Fields

| Doctype | Field | Type | Purpose |
|---------|-------|------|---------|
| Sales Order | custom_shopify_order_number | Data | Etsy receipt ID |
| Sales Order | custom_sales_channel | Data | "Etsy Maria" or "Etsy Zipcushions" |
| Sales Order | custom_sales_order_name | Data | Auto-set: C-{receipt_id} |
| Sales Order | custom_ccp_id | Data | Receipt ID (on F-Orders) |
| Sales Order | custom_buyer_email | Data | Buyer email (from Make.com) |
| Sales Order Item | custom_shopify_properties | Small Text | Item variations |
| Purchase Order | custom_ccp_id | Data | Receipt ID for F-Order linking |

### Existing Server Scripts (NOT modified)

| Script | Event | Purpose |
|--------|-------|---------|
| Sales Order Name | Before Insert | Auto-sets C-{receipt_id} from shopify_order_number |
| Auto Create PO from CCP SO | After Submit | Creates PO at 40% rate |
| Auto Create FAM SO from CCP PO | After Submit | Creates F-Order |

### Tax Account

- `US Sales Tax Payable - CCP - CCP` — must exist under Cozy Corner Patios LLC company

---

## OAuth Configuration

### Token Flow

```
Access token expires every 1 hour
  → Scheduler catches 401 error
  → Calls refresh_token() with refresh_token
  → Gets new access_token + new refresh_token
  → Saves to Settings
  → Retries API call
```

### Key Formats

| Purpose | Format | Example |
|---------|--------|---------|
| API calls (x-api-key) | `keystring:shared_secret` | abc123:xyz789 |
| Token refresh (client_id) | `keystring` only | abc123 |
| Authorization header | `Bearer {access_token}` | Bearer 737062562.abc... |

### Redirect URI

```
https://erp.cozycornerpatios.com/api/method/etsy_oauth_callback
```

### Re-authorization

If refresh token expires (unused for 90 days), re-authorize via bench console:

```python
import hashlib, base64, os, urllib.parse

API_KEY = "your_keystring"
REDIRECT_URI = "https://erp.cozycornerpatios.com/api/method/etsy_oauth_callback"

code_verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=').decode('utf-8')
code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b'=').decode('utf-8')

params = {
    "response_type": "code",
    "redirect_uri": REDIRECT_URI,
    "scope": "transactions_r shops_r address_r",
    "client_id": API_KEY,
    "state": "random123",
    "code_challenge": code_challenge,
    "code_challenge_method": "S256"
}

auth_url = f"https://www.etsy.com/oauth/connect?{urllib.parse.urlencode(params)}"
# Open URL, grant access, copy code from redirect URL
# Exchange code for tokens using requests.post
```

---

## Duplicate Prevention

Orders are checked at 3 levels:

1. **Order Log check** — `frappe.db.exists("Etsy Maria Order Log", {"receipt_id": receipt_id, "status": "Success"})`
2. **Sales Order check** — `frappe.db.exists("Sales Order", {"po_no": po_no})`
3. **Timestamp filter** — `min_created` parameter only fetches orders newer than last run

---

## Country ISO Mapping

The integration maps Etsy's `country_iso` codes to full country names for ERPNext Address documents:

```
US → United States, CA → Canada, GB → United Kingdom,
AU → Australia, DE → Germany, FR → France, IT → Italy,
ES → Spain, JP → Japan, MX → Mexico, BR → Brazil,
IN → India, and 15+ more countries
```

Unknown ISO codes are stored as-is.

---

## Monitoring

### Dashboards

| Store | URL | Version |
|-------|-----|---------|
| Maria | `/etsy-maria-dashboard` | v5.0 |
| Zipcushions | `/etsy-zipcushions-dashboard` | v2.0 |

Features: stat cards (Successful, Failed, Today's Orders, Total Revenue, Emails Captured, Emails Missing, Last Scheduler Run, Last Email Received), status/customer filters, date range, pagination, email column in table, auto-refresh every 60 seconds.

### Error Logs

Check `/app/error-log` and filter by "Etsy Maria" or "Etsy Zipcushions".

### Settings

| Store | URL |
|-------|-----|
| Maria | `/app/etsy-maria-settings` |
| Zipcushions | `/app/etsy-zipcushions-settings` |

Check `last_fetched` and `enable_scheduler` fields.

---

## Deployment

### On new server

```bash
# 1. Install the app
cd ~/frappe-bench
bench get-app https://github.com/sahilvikas/Etsy-integration.git --branch etsy-direct-integration
bench --site sitename install-app etsy_integration
bench restart

# 2. Create Settings doctypes and configure credentials via System Console
# 3. Create Order Log doctypes
# 4. Create custom fields on Sales Order and Purchase Order
# 5. Set enable_scheduler = 1 in Settings
# 6. Create Make.com scenarios for buyer email
```

### Existing server

```bash
cd ~/frappe-bench/apps/etsy_integration
git fetch origin
git checkout etsy-direct-integration
cd ~/frappe-bench
bench restart
```

### Disable old Server Scripts after deployment

```python
for name in ["Fetch Etsy Maria Orders", "Fetch Etsy Zipcushions Orders",
             "Refresh Etsy Maria Token", "Refresh Etsy Zipcushions Token"]:
    script = frappe.get_doc("Server Script", name)
    script.disabled = 1
    script.save(ignore_permissions=True)
frappe.db.commit()
```

---

## Known Limitations

1. **Apple Private Relay emails** — Buyers who use "Sign in with Apple" have masked emails (e.g., `8yhbfxeu9w@privaterelay.appleid.com`). These are hidden from both the direct API and Make.com. Only visible on the Etsy web dashboard.

2. **Buyer email not in direct API** — Etsy API returns `buyer_email: None` for all orders. Make.com is required for email capture.

3. **One Sales Order per transaction** — If an Etsy receipt has 2 items, 2 separate Sales Orders are created (matching the existing Make.com behavior).

---

## What's NOT Changed

| Component | Status |
|-----------|--------|
| `api/etsy_webhook.py` | Untouched — available for backward compatibility |
| Existing server scripts | Untouched — C-Order/PO/F-Order chain works as before |
| Custom doctypes | Created via System Console, not in app files |
| Dashboards | Web Pages in database, not in app files |
