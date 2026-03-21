import json
import re

import frappe
from frappe.utils import add_days, getdate, now_datetime

from etsy_integration.shipstation.constants import (
    SS_ORDER_ID_FIELD,
    SS_STATUS_FIELD,
    SETTING_DOCTYPE,
    STORE_CHANNEL_MAP_FIELD,
)


def _update_log(request_id, status, exception=None, rollback=False):
    if rollback:
        frappe.db.rollback()
    if not request_id:
        return
    log = frappe.get_doc("Etsy Integration Log", request_id)
    log.status = status
    if exception:
        log.message = str(exception)
        log.traceback = frappe.get_traceback()
    log.save(ignore_permissions=True)
    frappe.db.commit()


def _get_receipt_id(order_number):
    return re.sub(r"^ETSY-", "", order_number).strip()


def _get_sales_channel(store_id):
    if not store_id:
        return ""
    try:
        raw = frappe.db.get_single_value(SETTING_DOCTYPE, STORE_CHANNEL_MAP_FIELD)
        if not raw:
            return ""
        mapping = json.loads(raw)
        return mapping.get(str(store_id), "")
    except Exception:
        return ""


def _sync_customer(order):
    ship_to = order.get("shipTo", {}) or {}
    bill_to = order.get("billTo", {}) or {}

    ship_name = (ship_to.get("name") or "").strip()
    bill_name = (bill_to.get("name") or "").strip()
    username = (order.get("customerUsername") or "").strip()

    # Numeric-only usernames are Etsy IDs, not customer names.
    if username.isdigit():
        username = ""

    customer_name = ship_name or bill_name or username or "Etsy Customer"

    if not frappe.db.exists("Customer", customer_name):
        frappe.get_doc({
            "doctype": "Customer",
            "customer_name": customer_name,
            "customer_group": "All Customer Groups",
            "territory": "All Territories",
            "customer_type": "Individual",
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    updates = {}
    customer_email = (order.get("customerEmail") or "").strip()
    ship_phone = (ship_to.get("phone") or "").strip()
    if customer_email:
        if not frappe.db.get_value("Customer", customer_name, "email_id"):
            updates["email_id"] = customer_email
    if ship_phone:
        if not frappe.db.get_value("Customer", customer_name, "mobile_no"):
            updates["mobile_no"] = ship_phone
    if updates:
        frappe.db.set_value("Customer", customer_name, updates)

    return customer_name


def _sync_address(order, customer_name):
    ship_to = order.get("shipTo", {}) or {}
    if not (ship_to.get("street1") or "").strip():
        return None, None

    receipt_id = _get_receipt_id(order.get("orderNumber", "") or "")
    address_title = f"{(ship_to.get('name') or customer_name).strip()} - {receipt_id}"
    address_line1 = (ship_to.get("street1") or "").strip()
    address_line2 = (ship_to.get("street2") or "").strip()
    city = (ship_to.get("city") or "").strip()
    state = (ship_to.get("state") or "").strip()
    pincode = (ship_to.get("postalCode") or "").strip()
    country = (ship_to.get("country") or "United States").strip()
    phone = (ship_to.get("phone") or "").strip()
    email = (order.get("customerEmail") or "").strip()

    existing = frappe.db.get_value("Address", {"address_title": address_title}, "name")

    if existing:
        addr = frappe.get_doc("Address", existing)
        addr.address_line1 = address_line1
        addr.address_line2 = address_line2
        addr.city = city
        addr.state = state
        addr.pincode = pincode
        addr.country = country
        if phone:
            addr.phone = phone
        if email:
            addr.email_id = email
        addr.flags.ignore_mandatory = True
        addr.save(ignore_permissions=True)
    else:
        addr = frappe.get_doc({
            "doctype": "Address",
            "address_title": address_title,
            "address_type": "Shipping",
            "address_line1": address_line1,
            "address_line2": address_line2,
            "city": city,
            "state": state,
            "pincode": pincode,
            "country": country,
            "phone": phone,
            "email_id": email,
            "links": [{"link_doctype": "Customer", "link_name": customer_name}],
        })
        addr.flags.ignore_mandatory = True
        addr.insert(ignore_permissions=True)

    frappe.db.commit()

    parts = [address_line1]
    if address_line2:
        parts.append(address_line2)
    parts.append(f"{city}, {state} {pincode}")
    if country and country not in ("US", "United States"):
        parts.append(country)

    return addr.name, "\n".join(parts)


def _ensure_item_exists(sku, item_name):
    if not frappe.db.exists("Item", sku):
        frappe.get_doc({
            "doctype": "Item",
            "item_code": sku,
            "item_name": item_name,
            "item_group": "Products",
            "is_sales_item": 1,
            "include_item_in_manufacturing": 0,
        }).insert(ignore_permissions=True)
        frappe.db.commit()


def _build_so_items(items_list, delivery_date):
    so_items = []
    for item in (items_list or []):
        # Skip ShipStation adjustment lines (e.g., Discount rows).
        if item.get("adjustment") is True:
            continue

        sku = (item.get("sku") or item.get("lineItemKey") or "ETSY-ITEM").strip()
        item_name = (item.get("name") or sku).strip()
        if not sku:
            continue

        qty = float(item.get("quantity") or 1)
        rate = float(item.get("unitPrice") or 0)
        if rate < 0:
            continue

        options = item.get("options") or []
        custom_props = "\n".join(
            f"{o.get('name', '')}: {o.get('value', '')}"
            for o in options if o.get("name")
        )
        _ensure_item_exists(sku, item_name)
        so_items.append({
            "item_code": sku,
            "delivery_date": delivery_date,
            "qty": qty,
            "rate": rate,
            "warehouse": "Finished Goods - CCP",
            "custom_shopify_properties": custom_props,
        })
    return so_items


def _create_sales_order(order, customer_name, addr_name, full_address):
    """
    Creates a NEW Sales Order only.
    Called only when the order does not exist in ERPNext yet.
    """
    order_number = (order.get("orderNumber") or "").strip()
    order_status = (order.get("orderStatus") or "").strip()
    ss_order_id = str(order.get("orderId") or "")
    receipt_id = _get_receipt_id(order_number)
    po_number = f"ETSY-{receipt_id}"

    try:
        trans_date = getdate(order.get("orderDate"))
    except Exception:
        trans_date = now_datetime().date()

    try:
        delivery_date = getdate(order.get("shipByDate")) if order.get("shipByDate") else add_days(trans_date, 7)
    except Exception:
        delivery_date = add_days(trans_date, 7)

    ship_deadline = add_days(trans_date, 6)
    sales_channel = _get_sales_channel(
        order.get("advancedOptions", {}).get("storeId")
    )

    so_items = _build_so_items(order.get("items", []), delivery_date)
    if not so_items:
        frappe.log_error(f"No valid items in SS order {order_number}", "SS Order")
        return None

    should_submit = True

    so_doc = {
        "doctype": "Sales Order",
        "customer": customer_name,
        "transaction_date": trans_date,
        "delivery_date": delivery_date,
        "ship_deadline": ship_deadline,
        "company": "Cozy Corner Patios LLC",
        "order_type": "Sales",
        "po_no": po_number,
        "shopify_order_number": receipt_id,
        "currency": "USD",
        "set_warehouse": "Finished Goods - CCP",
        "customer_notes": (order.get("customerNotes") or "").strip(),
        "instructions": (order.get("internalNotes") or "").strip(),
        "items": so_items,
        SS_ORDER_ID_FIELD: ss_order_id,
        SS_STATUS_FIELD: order_status,
    }

    requested_service = (order.get("requestedShippingService") or "").strip()
    carrier_code = (order.get("carrierCode") or "").strip()
    so_meta = frappe.get_meta("Sales Order")
    if requested_service and so_meta.has_field("custom_shipping_service"):
        so_doc["custom_shipping_service"] = requested_service
    if carrier_code and so_meta.has_field("custom_carrier_code"):
        so_doc["custom_carrier_code"] = carrier_code

    if sales_channel:
        so_doc["custom_sales_channel"] = sales_channel
    if addr_name:
        so_doc["shipping_address_name"] = addr_name
        so_doc["shipping_address"] = full_address

    so = frappe.get_doc(so_doc)
    so.flags.ignore_mandatory = True
    so.insert(ignore_permissions=True)
    if should_submit:
        so.submit()
    frappe.db.commit()
    return so.name


def sync_sales_order(payload, request_id=None):
    """
    Called for ORDER_NOTIFY â€” new orders only.
    If the Sales Order already exists in ERPNext, skip entirely.
    Flow: Customer -> Address -> Sales Order.
    """
    frappe.set_user("Administrator")
    frappe.flags.request_id = request_id

    try:
        order        = payload
        ss_order_id  = str(order.get("orderId", ""))
        order_number = order.get("orderNumber", "")
        receipt_id   = _get_receipt_id(order_number)
        po_number    = f"ETSY-{receipt_id}"

        # Skip if Sales Order already exists â€” we only create, never update
        existing = None
        if ss_order_id:
            existing = frappe.db.get_value(
                "Sales Order", {SS_ORDER_ID_FIELD: ss_order_id}, "name"
            )
        if not existing:
            existing = frappe.db.get_value("Sales Order", {"po_no": po_number}, "name")

        if existing:
            if request_id:
                frappe.db.set_value(
                    "Etsy Integration Log", request_id,
                    "message", f"Skipped - SO {existing} already exists",
                    update_modified=False
                )
                frappe.db.commit()
            _update_log(request_id, "Success")
            return

        # New order â€” run full flow
        customer_name           = _sync_customer(order)
        addr_name, full_address = _sync_address(order, customer_name)
        so_name                 = _create_sales_order(order, customer_name, addr_name, full_address)

        if so_name is None:
            _update_log(
                request_id, "Error",
                exception=Exception(
                    "No valid items - all items may be adjustment/discount lines"
                )
            )
            return

        if so_name and request_id:
            frappe.db.set_value(
                "Etsy Integration Log", request_id, "sales_order", so_name,
                update_modified=False
            )

    except Exception as e:
        _update_log(request_id, "Error", exception=e, rollback=True)
    else:
        _update_log(request_id, "Success")


