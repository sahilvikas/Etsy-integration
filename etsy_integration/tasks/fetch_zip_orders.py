import frappe
from etsy_integration.utils.etsy_api import (
    get_settings, fetch_orders, format_address,
    get_order_date, get_item_properties
)


def run():
    """Scheduled task: Fetches new Etsy Zipcushions orders every 15 minutes"""
    settings = get_settings("zipcushions")

    if not settings.enable_scheduler:
        return

    try:
        orders = fetch_orders(settings)
    except Exception as e:
        frappe.log_error(f"Etsy Zipcushions fetch error: {str(e)}", "Etsy Zipcushions")
        return

    now = frappe.utils.now_datetime()

    for order in orders:
        try:
            process_order(order, settings, now)
        except Exception as e:
            frappe.log_error(f"Etsy Zipcushions order error: {str(e)}", "Etsy Zipcushions")

    settings.last_fetched = now
    settings.save(ignore_permissions=True)
    frappe.db.commit()


def process_order(order, settings, now):
    """Process a single Etsy order"""
    receipt_id = str(order.get("receipt_id", ""))
    customer_name = order.get("name", "")

    if not customer_name:
        return

    already_logged = frappe.db.exists("Etsy Zipcushions Order Log", {"receipt_id": receipt_id, "status": "Success"})
    if already_logged:
        return

    formatted_address = format_address(order)

    if not frappe.db.exists("Customer", customer_name):
        frappe.get_doc({
            "doctype": "Customer",
            "customer_name": customer_name,
            "customer_type": "Individual",
            "customer_group": "Individual",
            "territory": "All Territories"
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    created_ts = order.get("created_timestamp", 0)
    trans_date = get_order_date(created_ts)
    delivery_date = frappe.utils.add_days(trans_date, 7)

    subtotal = order.get("subtotal", {}).get("amount", 0) / order.get("subtotal", {}).get("divisor", 100)
    etsy_tax = order.get("total_tax_cost", {}).get("amount", 0) / order.get("total_tax_cost", {}).get("divisor", 100)

    for txn in order.get("transactions", []):
        process_transaction(txn, order, receipt_id, customer_name, formatted_address,
                          trans_date, delivery_date, subtotal, etsy_tax, settings, now)


def process_transaction(txn, order, receipt_id, customer_name, formatted_address,
                       trans_date, delivery_date, subtotal, etsy_tax, settings, now):
    """Process a single transaction within an order"""
    transaction_id = str(txn.get("transaction_id", ""))
    product_id = str(txn.get("product_id", ""))
    po_no = f"ETSYZ-{receipt_id}-{transaction_id}"

    if frappe.db.exists("Sales Order", {"po_no": po_no}):
        return

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

    taxes = []
    if etsy_tax > 0:
        taxes.append({
            "charge_type": "Actual",
            "account_head": "US Sales Tax Payable - CCP - CCP",
            "tax_amount": etsy_tax,
            "description": "Etsy Sales Tax"
        })

    try:
        so = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": customer_name,
            "transaction_date": trans_date,
            "delivery_date": delivery_date,
            "company": settings.company or "Cozy Corner Patios LLC",
            "order_type": "Sales",
            "po_no": po_no,
            "currency": settings.currency or "USD",
            "custom_sales_channel": "Etsy Zipcushions",
            "shopify_order_number": receipt_id,
            "shipping_address": formatted_address,
            "address_display": formatted_address,
            "items": [{
                "item_code": product_id,
                "delivery_date": delivery_date,
                "qty": float(qty),
                "rate": float(rate),
                "custom_shopify_properties": shopify_properties
            }],
            "taxes": taxes
        })
        so.insert(ignore_permissions=True)
        frappe.db.commit()

        so.submit()
        frappe.db.commit()

        frappe.get_doc({
            "doctype": "Etsy Zipcushions Order Log",
            "receipt_id": receipt_id,
            "transaction_id": transaction_id,
            "customer_name": customer_name,
            "product_id": product_id,
            "qty": float(qty),
            "rate": float(rate),
            "order_total": subtotal,
            "shipping_address": formatted_address,
            "variations": shopify_properties,
            "status": "Success",
            "sales_order": so.name,
            "order_date": trans_date,
            "fetched_at": now
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    except Exception as e:
        frappe.get_doc({
            "doctype": "Etsy Zipcushions Order Log",
            "receipt_id": receipt_id,
            "transaction_id": transaction_id,
            "customer_name": customer_name,
            "product_id": product_id,
            "shipping_address": formatted_address,
            "status": "Failed",
            "error_message": str(e),
            "fetched_at": now
        }).insert(ignore_permissions=True)
        frappe.db.commit()