# Etsy Integration — Direct API for ERPNext

Replaces Make.com middleware with direct Etsy API calls from ERPNext. Orders are fetched every 5 minutes, Sales Orders are created and auto-submitted, triggering the existing C-Order → PO → F-Order chain automatically.

Buyer email is captured via Make.com scenarios (Etsy API doesn't return buyer email due to privacy restrictions).

## Stores

|Store|Shop Name|Shop ID|Sales Channel|PO Format|
|-|-|-|-|-|
|Maria|MariasCushionCorner|40154721|Etsy Maria|`ETSY-{receipt\_id}`|
|Zipcushions|ZIPCushions|31937130|Etsy Zipcushions|`ETSY-{receipt\_id}`|

Both stores use `ETSY-` prefix for PO No — matching production behavior.

\---

## How It Works

```
Every 5 minutes:
  1. Scheduler calls Etsy API (auto-refreshes token if expired)
  2. Creates Customer, Item, Address document (if new)
  3. Creates ONE Sales Order per receipt with ALL items
     - PO No: ETSY-{receipt\_id}
     - shopify\_order\_number: {receipt\_id}
     - Item rate: price minus coupon (subtotal)
     - Tax row: Etsy sales tax
     - Shipping address document linked
  4. Auto-submits → triggers existing scripts:
     - C-{receipt\_id} naming
     - PO at 40% rate
     - F-{receipt\_id} created
  5. Logs to Order Log

Separately (Make.com every 15 min):
  - Captures buyer\_email → stores in custom\_buyer\_email field
```

\---

## Files

```
etsy\_integration/
├── api/
│   ├── etsy\_webhook.py        ← Existing (kept for backward compatibility)
│   └── contact\_update.py      ← Receives buyer email from Make.com
├── tasks/
│   ├── fetch\_maria\_orders.py  ← Maria scheduler
│   └── fetch\_zip\_orders.py    ← Zipcushions scheduler
├── utils/
│   └── etsy\_api.py            ← Shared: token refresh, fetch orders, format address
└── hooks.py                   ← Scheduler cron (\*/5 \* \* \* \*)
```

### utils/etsy\_api.py

|Function|What it does|
|-|-|
|`get\_settings(store)`|Reads credentials from Etsy Maria/Zipcushions Settings doctype|
|`get\_headers(settings)`|Returns `Authorization: Bearer {token}` and `x-api-key: {key}:{secret}`|
|`refresh\_token(settings)`|Refreshes expired token (1hr expiry). Uses `client\_id` = keystring only|
|`fetch\_orders(settings)`|Calls Etsy receipts API with `min\_created` filter. Auto-refreshes on 401|
|`format\_address(order)`|Joins first\_line, city, state, zip, country\_iso into text|
|`get\_order\_date(timestamp)`|Converts unix timestamp to date via `FROM\_UNIXTIME`|
|`get\_item\_properties(txn)`|Formats variations or description for `custom\_shopify\_properties`|

### tasks/fetch\_maria\_orders.py \& fetch\_zip\_orders.py

|Function|What it does|
|-|-|
|`run()`|Entry point (called by cron). Fetches orders, processes each, updates `last\_fetched`|
|`process\_order()`|Creates Customer + Address doc. Collects ALL transactions into one items list. Creates ONE Sales Order with multiple item rows. Auto-submits. Logs one entry per receipt|

Only difference between stores: Sales Channel name and Order Log doctype.

### api/contact\_update.py

|Detail|Value|
|-|-|
|Endpoint|`POST /api/method/etsy\_integration.api.contact\_update.update\_contact`|
|Input|`{"receipt\_id": "4135871984", "email": "buyer@gmail.com"}`|
|Action|Finds Sales Order by `shopify\_order\_number`, stores email in `custom\_buyer\_email`|

\---

## Sales Order Field Mapping

|Etsy|ERPNext|Example|
|-|-|-|
|`receipt\_id`|`po\_no`|ETSY-4135871984|
|`receipt\_id`|`shopify\_order\_number`|4135871984|
|`name` (buyer)|`customer`|Michelle Pohl|
|`created\_timestamp`|`transaction\_date`|2026-07-06|
|—|`shopify\_order\_id`|None (not from Shopify)|
|—|`custom\_sales\_order\_name`|C-4135871984 (auto-set by existing script)|
|—|`custom\_ccp\_id`|None on C-Order|
|Shop name|`custom\_sales\_channel`|Etsy Maria / Etsy Zipcushions|

### Items (multiple per receipt)

|Etsy|ERPNext|Example|
|-|-|-|
|`product\_id`|`item\_code`|32591851172|
|`quantity`|`qty`|1|
|`price - shop\_coupon`|`rate`|$7.65 (after 15% discount)|
|`variations`|`custom\_shopify\_properties`|Width: 25, Length: 59-61|

### Pricing

```
Item total:    $9.00  (price from Etsy)
Coupon:       -$1.35  (shop\_coupon from transaction)
Subtotal:     $7.65  → item rate
Tax:           $0.66  → Sales Taxes row (Actual, US Sales Tax Payable - CCP - CCP)
Grand Total:   $8.31  → auto-calculated
```

### Address Document

|Field|Source|Example|
|-|-|-|
|`address\_title`|`{customer} - {receipt\_id}`|Jennifer Reid - 4136829003|
|`address\_type`|Shipping|Shipping|
|`address\_line1`|`first\_line`|109 Thomas Court|
|`city`|`city`|WASHINGTON|
|`state`|`state`|IL|
|`pincode`|`zip`|61571|
|`country`|Mapped from `country\_iso`|United States|
|Link|Customer|Jennifer Reid|

Country ISO mapped: US, CA, GB, AU, NZ, DE, FR, IT, ES, NL, BE, AT, CH, SE, NO, DK, FI, IE, PT, JP, MX, BR, IN, SG, HK, IL, PL, CZ, GR. Unknown codes stored as-is.

\---

## C-Order / F-Order Chain

Triggered by existing server scripts (not modified):

```
C-Order auto-submitted
  → "Sales Order Name" → C-{receipt\_id}
  → "Auto Create PO from CCP SO" → PO at 40% rate, supplier Fabrics And More(S)
  → PO auto-submitted
  → "Auto Create FAM SO from CCP PO" → F-{receipt\_id} at Fabrics And More company
```

|Field|C-Order|F-Order|
|-|-|-|
|Customer|Actual buyer|Cozy Corner Patios LLC|
|Company|Cozy Corner Patios LLC|Fabrics And More|
|SO Name|C-{receipt\_id}|F-{receipt\_id}|
|Rate|After discount|40% of C-Order|

\---

## Make.com Scenarios (Buyer Email)

Etsy API returns `buyer\_email: None` for all orders. Make.com can access it.

### Zipcushions Scenario

```
Etsy Watch Shop Receipts (Zipcushions) → HTTP POST to update\_etsy\_contact
Body: {"receipt\_id": "{Receipt ID}", "email": "{buyer\_email}"}
Schedule: Every 15 minutes
```

`buyer\_email` available directly from Etsy Watch module.

### Maria Scenario

```
Etsy Watch Shop Receipts (Maria) → HTTP POST to update\_etsy\_contact
Body: {"receipt\_id": "{Receipt ID}", "email": "{buyer\_email}"}
Schedule: Every 15 minutes
```

`buyer\_email` available after first "Choose manually" run to load sample data.

### Dev vs Prod endpoint

|Environment|URL|
|-|-|
|Dev|`POST dev.cozycornerpatios.com/api/method/update\_etsy\_contact`|
|Prod|`POST erp.cozycornerpatios.com/api/method/etsy\_integration.api.contact\_update.update\_contact`|

\---

## Duplicate Prevention

1. Order Log check: `frappe.db.exists("Order Log", {"receipt\_id": receipt\_id, "status": "Success"})`
2. Sales Order check: `frappe.db.exists("Sales Order", {"po\_no": "ETSY-" + receipt\_id})`
3. Timestamp filter: `min\_created` parameter fetches only orders newer than `last\_fetched`

\---

## OAuth

|Item|Value|
|-|-|
|Flow|PKCE (no shared secret for authorization)|
|Token expiry|1 hour (auto-refreshed by scheduler)|
|Refresh token expiry|90 days if unused|
|API calls header|`x-api-key: keystring:shared\_secret`|
|Token refresh|`client\_id: keystring` only|
|Redirect URI|`https://erp.cozycornerpatios.com/api/method/etsy\_oauth\_callback`|

\---

## Dependencies

### Doctypes (create via System Console)

|Doctype|Type|
|-|-|
|Etsy Maria Settings|Single — API key, secret, shop ID, tokens, enable\_scheduler, last\_fetched|
|Etsy Zipcushions Settings|Single — same fields|
|Etsy Maria Order Log|Regular — receipt\_id, customer, status, sales\_order, order\_total, address, variations|
|Etsy Zipcushions Order Log|Regular — same fields|

### Custom Fields

|Doctype|Field|Purpose|
|-|-|-|
|Sales Order|`custom\_buyer\_email`|Buyer email from Make.com|
|Purchase Order|`custom\_ccp\_id`|Receipt ID for F-Order linking|

### Existing Fields Used

|Doctype|Field|Purpose|
|-|-|-|
|Sales Order|`shopify\_order\_number`|Stores receipt\_id, drives C-/F- naming|
|Sales Order|`custom\_sales\_channel`|Etsy Maria / Etsy Zipcushions|
|Sales Order|`custom\_sales\_order\_name`|C-{receipt\_id} (auto-set)|
|Sales Order|`custom\_ccp\_id`|Receipt ID on F-Orders|
|Sales Order Item|`custom\_shopify\_properties`|Item variations|

### Tax Account

`US Sales Tax Payable - CCP - CCP` under Cozy Corner Patios LLC

\---

## Deployment

### Deploy code

```bash
cd \~/frappe-bench/apps/etsy\_integration
git fetch origin
git checkout etsy-direct-integration
cd \~/frappe-bench
bench restart
```

### Manual setup (one-time)

1. Create 4 doctypes via System Console
2. Get OAuth tokens via bench console (PKCE flow)
3. Save credentials to Settings
4. Create `custom\_buyer\_email` on Sales Order, `custom\_ccp\_id` on Purchase Order
5. Enable scheduler in Settings
6. Create Make.com scenarios for buyer email
7. Disable old Make.com order creation scenarios

### Disable old Server Scripts (if migrating from dev setup)

```python
for name in \["Fetch Etsy Maria Orders", "Fetch Etsy Zipcushions Orders",
             "Refresh Etsy Maria Token", "Refresh Etsy Zipcushions Token",
             "Update Etsy Contact"]:
    if frappe.db.exists("Server Script", name):
        script = frappe.get\_doc("Server Script", name)
        script.disabled = 1
        script.save(ignore\_permissions=True)
frappe.db.commit()
```

\---

## Monitoring

### Dashboards

|Store|URL|
|-|-|
|Maria|`/etsy-maria-dashboard`|
|Zipcushions|`/etsy-zipcushions-dashboard`|

Cards: Successful, Failed, Today's Orders, Total Revenue, Last Scheduler Run, Emails Captured, Emails Missing, Last Email Received. Table: Status, Customer, Receipt, Sales Order, Qty, Subtotal, Address, Email, Variations.

### Quick checks

* Settings: `/app/etsy-maria-settings` or `/app/etsy-zipcushions-settings`
* Order Logs: `/app/etsy-maria-order-log` or `/app/etsy-zipcushions-order-log`
* Error Logs: `/app/error-log` → filter by "Etsy Maria" or "Etsy Zipcushions"

\---

## Known Limitations

1. **Buyer email not in API** — Etsy returns `buyer\_email: None`. Make.com required for email capture.
2. **Apple Private Relay** — Buyers using "Sign in with Apple" have masked emails hidden from both API and Make.com. Only visible on Etsy web dashboard.
3. **Tax handling** — Our integration adds Etsy sales tax as a row. Production Make.com does not add tax. This is an improvement but results in different Grand Total values.

