import base64
import json

import frappe
import requests
from frappe.utils import add_to_date, now_datetime

from etsy_integration.shipstation.constants import SS_ORDER_ID_FIELD, SETTING_DOCTYPE
from etsy_integration.shipstation.order import sync_sales_order


def _ss_get(path):
    settings = frappe.get_doc(SETTING_DOCTYPE)
    api_key = settings.api_key
    api_secret = settings.get_password("api_secret")
    token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
    headers = {"Authorization": f"Basic {token}"}
    return requests.get(f"https://ssapi.shipstation.com{path}", headers=headers, timeout=15)


def _create_cancelled_order_log(order):
    request_data = json.dumps(order, indent=2)
    frappe.get_doc({
        "doctype": "Etsy Integration Log",
        "event_type": "ORDER_NOTIFY",
        "status": "Success",
        "method": "etsy_integration.tasks.poll_cancelled_orders",
        "request_data": request_data,
    }).insert(ignore_permissions=True)


def poll_cancelled_orders():
    last_check = frappe.db.get_single_value(SETTING_DOCTYPE, "last_cancel_check")
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
                "Cancellation Poller",
            )
            break

        data = resp.json()
        orders = data.get("orders", [])

        for order in orders:
            try:
                source = (order.get("advancedOptions", {}) or {}).get("source")
                if str(source or "").lower() == "etsy":
                    _create_cancelled_order_log(order)
            except Exception as e:
                frappe.log_error(str(e), f"Cancel poll log error: {order.get('orderNumber', '')}")

        total = data.get("total", 0)
        if page * 100 >= total:
            break
        page += 1

    frappe.db.set_single_value(SETTING_DOCTYPE, "last_cancel_check", now_datetime())
    frappe.db.commit()


def sync_missing_orders():
    """
    Safety net — runs every 15 minutes.
    Fetches all awaiting_shipment Etsy orders from last 1 hour from ShipStation.
    Creates Sales Orders in ERPNext for any that are missing.
    1 hour lookback with 15 minute run interval = overlap buffer for reliability.
    """
    modify_start = add_to_date(now_datetime(), hours=-1)
    since = str(modify_start).replace(" ", "T").split(".")[0]

    path = (
        "/orders?orderStatus=awaiting_shipment"
        "&pageSize=100"
        "&sortBy=modifyDate"
        "&sortDir=DESC"
        f"&modifyDateStart={since}"
    )

    resp = _ss_get(path)
    if resp.status_code != 200:
        frappe.log_error(
            f"sync_missing_orders failed: {resp.status_code}",
            "SS Order Sync"
        )
        return

    data = resp.json()
    orders = data.get("orders", []) or []
    new_created = 0

    for order in orders:
        try:
            source = ((order.get("advancedOptions") or {}).get("source") or "").lower()
            if source != "etsy":
                continue

            ss_order_id = str(order.get("orderId") or "").strip()
            if ss_order_id and frappe.db.exists(
                "Sales Order", {SS_ORDER_ID_FIELD: ss_order_id}
            ):
                continue

            sync_sales_order(order)
            new_created += 1

        except Exception as e:
            frappe.log_error(
                str(e),
                f"sync_missing_orders error: {order.get('orderNumber', '')}"
            )

    if new_created > 0:
        frappe.log_error(
            f"sync_missing_orders created {new_created} missing orders",
            "SS Order Sync"
        )
