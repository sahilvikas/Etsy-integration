import frappe
from etsy_integration.utils.etsy_api import (
    get_settings, fetch_orders, format_address,
    get_order_date, get_item_properties
)

COUNTRY_MAP = {
    "US": "United States", "CA": "Canada", "GB": "United Kingdom",
    "AU": "Australia", "NZ": "New Zealand", "DE": "Germany",
    "FR": "France", "IT": "Italy", "ES": "Spain", "NL": "Netherlands",
    "BE": "Belgium", "AT": "Austria", "CH": "Switzerland",
    "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "FI": "Finland",
    "IE": "Ireland", "PT": "Portugal", "JP": "Japan", "MX": "Mexico",
    "BR": "Brazil", "IN": "India", "SG": "Singapore", "HK": "Hong Kong",
    "IL": "Israel", "PL": "Poland", "CZ": "Czech Republic", "GR": "Greece"
}


def run():
    """Scheduled task: Fetches new Etsy Maria orders every 5 minutes"""
    settings = get_settings("maria")

    if not settings.enable_scheduler:
        return

    try:
        orders = fetch_orders(settings)
    except Exception as e:
        frappe.log_error(f"Etsy Maria fetch error: {str(e)}", "Etsy Maria")
        return

    now = frappe.utils.now_datetime()

    for order in orders:
        try:
            process_order(order, settings, now)
        except Exception as e:
            frappe.log_error(f"Etsy Maria order error: {str(e)}", "Etsy Maria")

    settings.last_fetched = now
    settings.save(ignore_permissions=True)
    frappe.db.commit()


def process_order(order, settings, now):
    """Process a single Etsy order — creates ONE Sales Order with all items"""
    receipt_id = str(order.get("receipt_id", ""))
    customer_name = order.get("name", "")

    if not customer_name:
        return

    # Skip if already processed
    already_logged = frappe.db.exists("Etsy Maria Order Log", {"receipt_id": receipt_id, "status": "Success"})
    if already_logged:
        return

    po_no = f"ETSY-{receipt_id}"

    # If Make.com already created it, log and skip
    existing_so = frappe.db.exists("Sales Order", {"po_no": po_no})
    if existing_so:
        formatted_address = format_address(order)
        created_ts = order.get("created_timestamp", 0)
        trans_date = get_order_date(created_ts)
        subtotal = order.get("subtotal", {}).get("amount", 0) / order.get("subtotal", {}).get("divisor", 100)
        frappe.get_doc({
            "doctype": "Etsy Maria Order Log",
            "receipt_id": receipt_id,
            "customer_name": customer_name,
            "status": "Success",
            "sales_order": existing_so,
            "order_total": subtotal,
            "shipping_address": formatted_address,
            "order_date": trans_date,
            "fetched_at": now
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        return

    # Format address
    formatted_address = format_address(order)

    # Create customer if needed
    if not frappe.db.exists("Customer", customer_name):
        frappe.get_doc({
            "doctype": "Customer",
            "customer_name": customer_name,
            "customer_type": "Individual",
            "customer_group": "Individual",
            "territory": "All Territories"
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    # Create Address document
    addr_name = f"{customer_name} - {receipt_id}-Shipping"
    if not frappe.db.exists("Address", addr_name):
        country_iso = order.get("country_iso", "US")
        country_full = COUNTRY_MAP.get(country_iso, country_iso)

        frappe.get_doc({
            "doctype": "Address",
            "address_title": f"{customer_name} - {receipt_id}",
            "address_type": "Shipping",
            "address_line1": order.get("first_line", "") or customer_name,
            "address_line2": order.get("second_line", "") or "",
            "city": order.get("city", "") or "N/A",
            "state": order.get("state", "") or "",
            "pincode": order.get("zip", "") or "",
            "country": country_full,
            "links": [{
                "link_doctype": "Customer",
                "link_name": customer_name
            }]
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    # Get order date
    created_ts = order.get("created_timestamp", 0)
    trans_date = get_order_date(created_ts)
    delivery_date = frappe.utils.add_days(trans_date, 7)

    # Get order level prices
    subtotal = order.get("subtotal", {}).get("amount", 0) / order.get("subtotal", {}).get("divisor", 100)
    etsy_tax = order.get("total_tax_cost", {}).get("amount", 0) / order.get("total_tax_cost", {}).get("divisor", 100)

    # Build items list — ALL transactions in ONE Sales Order
    items = []
    for txn in order.get("transactions", []):
        product_id = str(txn.get("product_id", ""))

        if not frappe.db.exists("Item", product_id):
            item_name = txn.get("title", product_id)[:140]
            frappe.get_doc({
                "doctype": "Item",
                "item_code": product_id,
                "item_name": item_name,
                "item_group": "Products",
                "stock_uom": "Nos",
                "is_stock_item": 0
            }).insert(ignore_permissions=True)
            frappe.db.commit()

        shopify_properties = get_item_properties(txn)

        price_amount = txn.get("price", {}).get("amount", 0)
        price_divisor = txn.get("price", {}).get("divisor", 100)
        coupon = txn.get("shop_coupon", 0)
        rate = round((price_amount / price_divisor) - coupon, 2)
        qty = txn.get("quantity", 1)

        item_row = {
            "item_code": product_id,
            "delivery_date": delivery_date,
            "qty": float(qty),
            "rate": float(rate),
            "custom_shopify_properties": shopify_properties
        }
        is_stock = frappe.db.get_value("Item", product_id, "is_stock_item")
        if is_stock:
            item_row["warehouse"] = "Finished Goods - CCP"
        items.append(item_row)

    if not items:
        return

    # Build taxes
    taxes = []
    if etsy_tax > 0:
        taxes.append({
            "charge_type": "Actual",
            "account_head": "US Sales Tax Payable - CCP - CCP",
            "tax_amount": etsy_tax,
            "description": "Etsy Sales Tax"
        })

    try:
        # Create ONE Sales Order with all items
        so = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": customer_name,
            "transaction_date": trans_date,
            "delivery_date": delivery_date,
            "company": settings.company or "Cozy Corner Patios LLC",
            "order_type": "Sales",
            "po_no": po_no,
            "currency": settings.currency or "USD",
            "custom_sales_channel": "Etsy Maria",
            "shopify_order_number": receipt_id,
            "shipping_address_name": addr_name,
            "shipping_address": formatted_address,
            "address_display": formatted_address,
            "items": items,
            "taxes": taxes
        })
        so.insert(ignore_permissions=True)
        frappe.db.commit()

        so.submit()
        frappe.db.commit()

        # Log ONE entry per receipt
        frappe.get_doc({
            "doctype": "Etsy Maria Order Log",
            "receipt_id": receipt_id,
            "transaction_id": str(order.get("transactions", [{}])[0].get("transaction_id", "")),
            "customer_name": customer_name,
            "product_id": str(order.get("transactions", [{}])[0].get("product_id", "")),
            "qty": len(items),
            "rate": items[0]["rate"],
            "order_total": subtotal,
            "shipping_address": formatted_address,
            "variations": items[0].get("custom_shopify_properties", ""),
            "status": "Success",
            "sales_order": so.name,
            "order_date": trans_date,
            "fetched_at": now
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    except Exception as e:
        frappe.get_doc({
            "doctype": "Etsy Maria Order Log",
            "receipt_id": receipt_id,
            "transaction_id": "",
            "customer_name": customer_name,
            "product_id": "",
            "shipping_address": formatted_address,
            "status": "Failed",
            "error_message": str(e),
            "fetched_at": now
        }).insert(ignore_permissions=True)
        frappe.db.commit()