import base64
import json
import time

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


def _error(message, status="error", details=None):
    out = {"status": status, "message": message}
    if details:
        out["details"] = details
    return out


def _parse_webhook_request(raw):
    if not raw:
        return None, {"status": "empty"}

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, _error("Invalid JSON payload", details=str(e))

    if not isinstance(data, dict):
        return None, _error("Invalid webhook payload: expected JSON object")

    return data, None


def _validate_webhook_payload(data):
    resource_type = (data.get("resource_type") or "").strip()
    resource_url = (data.get("resource_url") or "").strip()

    if not resource_type:
        return None, None, _error("Missing required field: resource_type")

    if resource_type not in EVENT_MAPPER:
        return resource_type, resource_url, {
            "status": "ignored",
            "resource_type": resource_type,
        }

    if not resource_url:
        return resource_type, resource_url, _error("Missing required field: resource_url")

    return resource_type, resource_url, None


def get_ss_auth_headers():
    """Build Basic Auth header from ShipStation Settings api_key + api_secret."""
    doc = frappe.get_doc(SETTING_DOCTYPE)
    api_key = doc.api_key
    api_secret = doc.get_password("api_secret")
    token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }


def fetch_order_from_shipstation(resource_url, max_attempts=3, timeout=15):
    """
    ShipStation webhook only sends resource_url, not full data.
    We call back to resource_url to get the full order JSON.
    Returns the first order dict from the orders[] array.
    Retries up to max_attempts before failing.
    """
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(resource_url, headers=get_ss_auth_headers(), timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                orders = data.get("orders", [])
                if orders:
                    return orders[0]
                return data

            last_error = f"Attempt {attempt}: HTTP {resp.status_code}"
        except Exception as e:
            last_error = f"Attempt {attempt}: {e}"

        if attempt < max_attempts:
            time.sleep(1)

    frappe.log_error(
        f"ShipStation fetch failed after {max_attempts} attempts. URL={resource_url}. Last error={last_error}",
        "SS Connection",
    )
    return None


@frappe.whitelist(allow_guest=True)
def store_request_data():
    """
    Single entry point for ALL ShipStation webhooks.

    Register this ONE URL in ShipStation for ORDER_NOTIFY only:
      Settings -> Integration Partners -> Webhooks -> Subscribe
      URL: https://your-erp.com/api/method/etsy_integration.shipstation.connection.store_request_data
    """
    if not frappe.request:
        return _error("No request context available")

    data, parse_error = _parse_webhook_request(frappe.request.data)
    if parse_error:
        return parse_error

    resource_type, resource_url, validation_error = _validate_webhook_payload(data)
    if validation_error:
        return validation_error

    order_data = fetch_order_from_shipstation(resource_url, max_attempts=3, timeout=15)
    if not order_data:
        return _error("Could not fetch order from ShipStation after 3 attempts")

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
