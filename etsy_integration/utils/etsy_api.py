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


def get_headers(settings):
    """Return headers for Etsy API calls"""
    return {
        "Authorization": f"Bearer {settings.access_token}",
        "x-api-key": f"{settings.api_key}:{settings.api_secret}"
    }


def refresh_token(settings):
    """Refresh OAuth token and save new tokens"""
    response = requests.post(
        "https://api.etsy.com/v3/public/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": settings.api_key,
            "refresh_token": settings.refresh_token
        }
    )

    data = response.json()

    if data.get("access_token"):
        settings.access_token = data["access_token"]
        settings.refresh_token = data["refresh_token"]
        settings.save(ignore_permissions=True)
        frappe.db.commit()
        return True
    else:
        frappe.log_error(f"Token refresh failed: {data.get('error')}", "Etsy Integration")
        return False


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
        return []


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
    """Format variations or description into shopify_properties"""
    variations = txn.get("variations", [])
    has_vars = False
    for v in variations:
        if v.get("formatted_name") and v.get("formatted_value"):
            has_vars = True
            break

    if has_vars:
        lines = []
        for v in variations:
            if v.get("formatted_name") and v.get("formatted_value"):
                lines.append(f"{v['formatted_name']}: {v['formatted_value']}")
        return "\n".join(lines)
    else:
        desc = txn.get("description", "")
        return desc[:500] if len(desc) > 500 else desc