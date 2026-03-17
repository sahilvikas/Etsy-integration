import frappe
import requests
import base64
from frappe.utils import now_datetime, add_to_date


def _ss_get(path):
    api_key = frappe.db.get_single_value("ShipStation Settings", "api_key")
    api_secret = frappe.db.get_single_value("ShipStation Settings", "api_secret")
    token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
    headers = {"Authorization": f"Basic {token}"}
    return requests.get(f"https://ssapi.shipstation.com{path}", headers=headers, timeout=15)


def poll_cancelled_orders():
    if not frappe.db.get_single_value("ShipStation Settings", "enabled"):
        return

    last_check = frappe.db.get_single_value("ShipStation Settings", "last_cancel_check")
    if not last_check:
        last_check = str(add_to_date(now_datetime(), hours=-24))

    since = str(last_check).replace(" ", "T").split(".")[0]
    page = 1

    while True:
        resp = _ss_get(
            f"/orders?orderStatus=cancelled&modifyDateStart={since}&page={page}&pageSize=100"
        )
        if resp.status_code != 200:
            frappe.log_error(
                f"ShipStation poll failed: {resp.status_code}",
                "Cancellation Poller"
            )
            break

        data = resp.json()
        orders = data.get("orders", [])

        for order in orders:
            num = order.get("orderNumber", "")
            if "ETSY" in num.upper():
                try:
                    from etsy_integration.api.etsy_webhook import _handle_cancellation
                    _handle_cancellation(order)
                except Exception as e:
                    frappe.log_error(str(e), f"Cancel poll error: {num}")

        total = data.get("total", 0)
        if page * 100 >= total:
            break
        page += 1

    frappe.db.set_single_value("ShipStation Settings", "last_cancel_check", now_datetime())
    frappe.db.commit()
