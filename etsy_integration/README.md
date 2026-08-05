# Etsy Integration — Direct API for ERPNext

## Overview

This custom app integrates Etsy stores directly with ERPNext, replacing the Make.com middleware dependency. It fetches orders from Etsy's API every 15 minutes and creates Sales Orders automatically.

### Stores Integrated

| Store | Shop Name | Shop ID | Sales Channel |
|-------|-----------|---------|---------------|
| Maria | MariasCushionCorner | 40154721 | Etsy Maria |
| Zipcushions | ZIPCushions | 31937130 | Etsy Zipcushions |

---

## Architecture

### Before (Make.com)

```
Etsy → Make.com → ERPNext webhook (etsy_webhook.py)
```

Make.com watched Etsy for orders, processed the data, and sent it to ERPNext via webhook.

### After (Direct Integration)

```
ERPNext Scheduler (every 15 min) → Etsy API → Creates Sales Order → Auto-submits
```

ERPNext calls Etsy API directly. No middleman needed.

---

## Complete Order Flow

```
1. Scheduler runs every 15 minutes (hooks.py cron)
2. Calls Etsy API — fetches only NEW orders (since last fetch)
3. If token expired — auto-refreshes using refresh token
4. Creates Customer (if new buyer)
5. Creates Item (if new product)
6. Creates C-Order (Sales Order) with:
   - shopify_order_number = receipt_id
   - Item rate = price minus coupon discount (subtotal)
   - Tax row = Etsy sales tax
   - Shipping address from Etsy
   - custom_sales_channel = "Etsy Maria" or "Etsy Zipcushions"
7. Auto-submits the C-Order
8. Existing server scripts auto-trigger:
   - "Sales Order Name" → sets C-{receipt_id}
   - "Auto Create PO from CCP SO" → creates PO at 40% rate
   - "Auto Create FAM SO from CCP PO" → creates F-{receipt_id}
9. Logs result to Order Log (Success or Failed)
10. Updates last_fetched timestamp
```

---

## File Structure

```
etsy_integration/
├── api/
│   ├── __init__.py
│   └── etsy_webhook.py           ← Existing: Make.com webhook (kept for contact details)
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
| `process_order()` | Processes one Etsy receipt. Skips if already logged. Creates Customer if new. Extracts subtotal, tax, address. Loops through transactions |
| `process_transaction()` | Creates one Sales Order per transaction. Calculates rate (price - coupon). Adds tax row. Inserts, submits, logs result |

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
| `first_line, city, state, zip` | `shipping_address`, `address_display` | 179 Hobson St, San Jose, CA 95110 |
| Shop name | `custom_sales_channel` | Etsy Maria / Etsy Zipcushions |
| `price - shop_coupon` | Item `rate` | $7.65 (after 15% discount) |
| `subtotal` | Sales Order `total` | $7.65 |
| `total_tax_cost` | `taxes` table (Actual type) | $0.66 |
| `grandtotal` | `grand_total` (auto-calculated) | $8.31 |
| `variations` | `custom_shopify_properties` | Width: 25, Length: 59-61 |

### Price Calculation

```
Etsy receipt:
  Item total:    $9.00  (original price)
  Coupon:       -$1.35  (15% off)
  Subtotal:     $7.65  → Sales Order item rate
  Tax:           $0.66  → Sales Taxes and Charges row
  Order total:   $8.31  → Sales Order grand_total (auto)
```

### C-Order / F-Order Chain

Every submitted Sales Order triggers existing server scripts:

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

## Dependencies (Must exist on ERPNext site)

### Custom Doctypes

| Doctype | Type | Purpose |
|---------|------|---------|
| Etsy Maria Settings | Single | Stores Maria API key, secret, shop ID, tokens, scheduler toggle |
| Etsy Zipcushions Settings | Single | Stores Zipcushions credentials |
| Etsy Maria Order Log | Regular | Logs every Maria order (Success/Failed) |
| Etsy Zipcushions Order Log | Regular | Logs every Zipcushions order |

### Custom Fields

| Doctype | Field | Type | Purpose |
|---------|-------|------|---------|
| Sales Order | custom_shopify_order_number | Data | Etsy receipt ID |
| Sales Order | custom_sales_channel | Data | "Etsy Maria" or "Etsy Zipcushions" |
| Sales Order | custom_sales_order_name | Data | Auto-set: C-{receipt_id} |
| Sales Order | custom_ccp_id | Data | Receipt ID (on F-Orders) |
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
# Step 1: Generate auth URL (PKCE flow)
# Step 2: Open URL in browser, grant access
# Step 3: Exchange code for tokens
# Step 4: Save tokens to Settings
```

---

## Duplicate Prevention

Orders are checked at 3 levels:

1. **Order Log check** — `frappe.db.exists("Etsy Maria Order Log", {"receipt_id": receipt_id, "status": "Success"})`
2. **Sales Order check** — `frappe.db.exists("Sales Order", {"po_no": po_no})`
3. **Timestamp filter** — `min_created` parameter only fetches orders newer than last run

---

## Monitoring

### Dashboards

| Store | URL |
|-------|-----|
| Maria | `/etsy-maria-dashboard` |
| Zipcushions | `/etsy-zipcushions-dashboard` |

Features: stat cards (Successful, Failed, Today's Orders, Total Orders, Total Revenue), status/customer filters, date range, pagination, auto-refresh every 60 seconds.

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

# 2. Create Settings doctypes and configure credentials
# 3. Create Order Log doctypes
# 4. Set enable_scheduler = 1 in Settings
```

### Existing server

```bash
cd ~/frappe-bench/apps/etsy_integration
git fetch origin
git checkout etsy-direct-integration
cd ~/frappe-bench
bench restart
```

---

## What's NOT Changed

| Component | Status |
|-----------|--------|
| `api/etsy_webhook.py` | Untouched — still available for Make.com contact details scenario |
| Existing server scripts | Untouched — C-Order/PO/F-Order chain works as before |
| Custom doctypes | Untouched — created via System Console, not in app files |
| Dashboards | Untouched — Web Pages in database |
