import frappe
import requests


def get_settings(store):
    """Get settings for Maria or Zipcushions"""
    if store == "maria":
        return frappe.get_doc("Etsy Maria Settings")
    elif store == "zipcushions":
        return frappe.get_doc("Etsy Zipcushions Settings")
    else:
        frappe.throw(f"Unknown store: {store}")


REFRESH_AHEAD_SECONDS = 900  # refresh at <15 min left; server scripts only refresh at <5 min


def _get_shop(settings):
    """Single token store: the Etsy Shop row (name = shop_id)"""
    return frappe.get_doc("Etsy Shop", str(settings.shop_id))


def _refresh_shop(shop, force=False):
    """Refresh tokens on the Etsy Shop row under a row lock. Returns True on success."""
    frappe.db.sql("SELECT name FROM `tabEtsy Shop` WHERE name=%s FOR UPDATE", (shop.name,))
    shop.reload()
    now = frappe.utils.now_datetime()
    exp = shop.token_expires_at
    if not force and exp and frappe.utils.time_diff_in_seconds(exp, now) > REFRESH_AHEAD_SECONDS:
        frappe.db.commit()
        return True  # someone else refreshed while we waited for the lock

    refresh = shop.get_password("refresh_token", raise_exception=False)
    if not refresh:
        frappe.log_error(f"Token refresh failed: no refresh_token (Etsy Shop {shop.name})", "Etsy Integration")
        frappe.db.commit()
        return False

    try:
        response = requests.post(
            "https://api.etsy.com/v3/public/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": shop.keystring,
                "refresh_token": refresh
            },
            timeout=30
        )
        data = response.json()
    except Exception as e:
        frappe.log_error(f"Token refresh failed: {e} (Etsy Shop {shop.name})", "Etsy Integration")
        frappe.db.commit()
        return False

    if not data.get("access_token"):
        frappe.log_error(f"Token refresh failed: {data.get('error')} (Etsy Shop {shop.name})", "Etsy Integration")
        frappe.db.commit()
        return False

    shop.access_token = data["access_token"]
    shop.refresh_token = data.get("refresh_token") or refresh
    shop.token_expires_at = frappe.utils.add_to_date(now, seconds=int(data.get("expires_in") or 3600) - 60)
    shop.save(ignore_permissions=True)
    frappe.db.commit()
    return True


def _access_token(settings):
    shop = _get_shop(settings)
    exp = shop.token_expires_at
    if not exp or frappe.utils.time_diff_in_seconds(exp, frappe.utils.now_datetime()) < REFRESH_AHEAD_SECONDS:
        if not _refresh_shop(shop):
            return ""
        shop.reload()
    return shop.get_password("access_token", raise_exception=False) or ""


def get_headers(settings):
    """Return headers for Etsy API calls (token comes from the Etsy Shop row)"""
    return {
        "Authorization": f"Bearer {_access_token(settings)}",
        "x-api-key": f"{settings.api_key}:{settings.api_secret}"
    }


def refresh_token(settings):
    """Forced refresh (after a 401). Writes to the Etsy Shop row, never to the settings doc."""
    return _refresh_shop(_get_shop(settings), force=True)


def fetch_orders(settings):
    """Fetch new orders from Etsy"""
    headers = get_headers(settings)
    shop_id = settings.shop_id

    url = f"https://openapi.etsy.com/v3/application/shops/{shop_id}/receipts?was_paid=true&limit=25&sort_on=created&sort_order=desc"

    if settings.last_fetched:
        last = str(settings.last_fetched)
        if "." in last:
            last = last.split(".")[0]
        ts_result = frappe.db.sql("SELECT UNIX_TIMESTAMP(%s)", (last,))
        if ts_result and ts_result[0][0]:
            url += f"&min_created={int(ts_result[0][0])}"

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json().get("results", [])
    except Exception:
        if refresh_token(settings):
            headers = get_headers(settings)
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json().get("results", [])
        # Auth could not be restored: raise so run() does NOT advance last_fetched
        raise


def format_address(order):
    """Format address from Etsy order data"""
    addr_parts = []
    if order.get("first_line"):
        addr_parts.append(order["first_line"])
    if order.get("second_line"):
        addr_parts.append(order["second_line"])
    city_state = ""
    if order.get("city"):
        city_state += order["city"]
    if order.get("state"):
        city_state += f", {order['state']}"
    if order.get("zip"):
        city_state += f" {order['zip']}"
    if city_state:
        addr_parts.append(city_state)
    if order.get("country_iso"):
        addr_parts.append(order["country_iso"])
    return "\n".join(addr_parts)


def get_order_date(created_timestamp):
    """Convert unix timestamp to date"""
    try:
        result = frappe.db.sql("SELECT FROM_UNIXTIME(%s)", (int(created_timestamp),))
        return result[0][0].date()
    except Exception:
        return frappe.utils.now_datetime().date()


def get_item_properties(txn):
    """Format variations and the full description into shopify_properties"""
    variations = txn.get("variations", [])
    has_vars = False
    for v in variations:
        if v.get("formatted_name") and v.get("formatted_value"):
            has_vars = True
            break

    lines = []
    if has_vars:
        for v in variations:
            if v.get("formatted_name") and v.get("formatted_value"):
                lines.append(f"{v['formatted_name']}: {v['formatted_value']}")

    desc = (txn.get("description", "") or "").strip()
    if desc:
        if lines:
            lines.append("")
        lines.append(desc)

    return "\n".join(lines)