import base64
import json

import frappe
import requests

from etsy_integration.shipstation.constants import EVENT_MAPPER, SETTING_DOCTYPE


def create_etsy_log(event_type, method, request_data=None, status="Queued"):
    """Create an Etsy Integration Log entry. Visible on ShipStation Settings page."""
    if request_data and not isinstance(request_data, str):
        request_data = json.dumps(request_data, indent=2)

    log = frappe.get_doc({
        "doctype": "Etsy Integration Log",
        "event_type": event_type,
        "method": method,
        "status": status,
        "request_data": request_data,
    })
    log.insert(ignore_permissions=True)
    frappe.db.commit()
    return log


def get_ss_auth_headers():
    """Build Basic Auth header from ShipStation Settings api_key + api_secret."""
    api_key = frappe.db.get_single_value(SETTING_DOCTYPE, "api_key")
    api_secret = frappe.db.get_single_value(SETTING_DOCTYPE, "api_secret")
    token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }


def fetch_order_from_shipstation(resource_url):
    """
    ShipStation webhook only sends resource_url, not full data.
    We call back to resource_url to get the full order JSON.
    Returns the first order dict from the orders[] array.
    """
    try:
        resp = requests.get(resource_url, headers=get_ss_auth_headers(), timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            orders = data.get("orders", [])
            if orders:
                return orders[0]
            return data
        frappe.log_error(
            f"ShipStation fetch failed {resp.status_code}: {resource_url}",
            "SS Connection"
        )
        return None
    except Exception as e:
        frappe.log_error(str(e), "SS Fetch Exception")
        return None


@frappe.whitelist(allow_guest=True)
def store_request_data():
    """
    Single entry point for ALL ShipStation webhooks.

    Register this ONE URL in ShipStation for each event:
      Settings -> Integration Partners -> Webhooks -> Subscribe
      URL: https://your-erp.com/api/method/etsy_integration.shipstation.connection.store_request_data

    Register for:
      - On New Orders  (ORDER_NOTIFY)
      - On New Items   (ITEM_ORDER_NOTIFY)
    """
    if not frappe.request:
        return

    raw = frappe.request.data
    if not raw:
        return {"status": "empty"}

    if isinstance(raw, bytes): raw = raw.decode("utf-8")
    data = json.loads(raw)
    resource_type = data.get("resource_type", "")
    resource_url = data.get("resource_url", "")

    if resource_type not in EVENT_MAPPER:
        return {"status": "ignored", "resource_type": resource_type}

    if not resource_url:
        return {"status": "error", "message": "Missing resource_url"}

    order_data = fetch_order_from_shipstation(resource_url)
    if not order_data:
        return {"status": "error", "message": "Could not fetch order from ShipStation"}

    log = create_etsy_log(
        event_type=resource_type,
        method=EVENT_MAPPER[resource_type],
        request_data=order_data,
        status="Queued",
    )

    frappe.enqueue(
        method=EVENT_MAPPER[resource_type],
        queue="short",
        timeout=300,
        is_async=True,
        payload=order_data,
        request_id=log.name,
    )

    return {"status": "queued", "log": log.name}

