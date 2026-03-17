# =============================================================================
# DEPRECATED — This file is superseded by etsy_integration/shipstation/
#
# receive_order()   -> replaced by ORDER_NOTIFY  -> sync_sales_order()
# update_address()  -> replaced by address created inside sync_sales_order()
#
# Both functions are kept alive until Make.com scenarios are fully disabled.
# Steps to eliminate Make.com:
#   1. Register ShipStation webhooks (ORDER_NOTIFY + ITEM_ORDER_NOTIFY)
#   2. Run parallel for 7 days - verify all orders come via ShipStation
#   3. Disable Make.com scenarios in Make.com UI
#   4. Delete receive_order() and update_address() from this file
# =============================================================================

import frappe
import json
import re
from frappe.utils import add_days, getdate, now_datetime

@frappe.whitelist(allow_guest=False, methods=['POST'])
def receive_order():
    """
    Custom webhook endpoint for Etsy / Make.com
    Cleans incoming order JSON and creates/updates Sales Orders in ERPNext.
    """

    try:
        data = frappe.local.form_dict

        # Extract main fields
        transaction_id = data.get('transaction_id', '')
        order_data_raw = data.get('order_data', '')
        total_items = int(data.get('total_items', '1'))
        current_item = int(data.get('current_item', '1'))
        sales_channel = data.get('sales_channel', '')  # NEW: Extract sales channel (Etsy Maria or Etsy Zipcushions)

        # Parse Etsy key-value pairs (e.g. "CUSTOMER: John Doe || PRODUCT: Table")
        parts = {}
        for part in order_data_raw.split("||"):
            if ":" in part:
                key, value = part.split(":", 1)
                parts[key.strip()] = value.strip()

        # Cleaning helper
        def clean_field(text):
            text = re.sub(r'[\r\n\t]+', ' ', text)
            text = re.sub(r'\s+', ' ', text)
            return text.strip()

        # Extract cleaned fields
        customer_name = clean_field(parts.get("CUSTOMER", ""))
        receipt_id = clean_field(parts.get("RECEIPT", ""))
        transaction_id_parsed = clean_field(parts.get("TRANSACTION", transaction_id))
        transaction_date = clean_field(parts.get("DATE", ""))
        product_id = clean_field(parts.get("PRODUCT", ""))
        product_title = clean_field(parts.get("TITLE", ""))  # â† NEW: Extract title
        qty = clean_field(parts.get("QTY", "1"))
        rate = clean_field(parts.get("RATE", "0"))
        description = parts.get("DESC", "").strip()

        po_number = f"ETSY-{receipt_id}"

        # 1ï¸âƒ£ Ensure Customer exists
        if not frappe.db.exists("Customer", customer_name):
            frappe.get_doc({
                "doctype": "Customer",
                "customer_name": customer_name,
                "customer_group": "All Customer Groups",
                "territory": "All Territories"
            }).insert(ignore_permissions=True)
            frappe.db.commit()

        # 2ï¸âƒ£ Ensure Item exists
        if not frappe.db.exists("Item", product_id):
            frappe.get_doc({
                "doctype": "Item",
                "item_code": product_id,
                "item_name": product_title,  # â† CHANGED: Use title instead of product_id
                "item_group": "Products",
                "is_sales_item": 1,
                "include_item_in_manufacturing": 0
            }).insert(ignore_permissions=True)
            frappe.db.commit()

        # 3ï¸âƒ£ Prepare custom properties (variations / personalization)
        var1_name = clean_field(parts.get("VAR1NAME", ""))
        var1_val = clean_field(parts.get("VAR1VAL", ""))
        var2_name = clean_field(parts.get("VAR2NAME", ""))
        var2_val = clean_field(parts.get("VAR2VAL", ""))
        var3_name = clean_field(parts.get("VAR3NAME", ""))
        var3_val = clean_field(parts.get("VAR3VAL", ""))

        if var1_name and var1_val:
            formatted_lines = []
            if var1_name and var1_val:
                formatted_lines.append(f"{var1_name}: {var1_val}")
            if var2_name and var2_val:
                formatted_lines.append(f"{var2_name}: {var2_val}")
            if var3_name and var3_val:
                formatted_lines.append(f"{var3_name}: {var3_val}")
            custom_properties = "\n".join(formatted_lines)
        else:
            # Clean up the description but keep newlines for readability
            desc = description.replace("Your Customization Summary", "").strip()
            lines = desc.split('\n')
            filtered_lines = [line for line in lines if not line.strip().startswith('Price')]
            desc = '\n'.join(filtered_lines)
            desc = re.sub(r'[\r\t]+', '', desc)
            desc = re.sub(r' +', ' ', desc)
            desc = re.sub(r'\n\n\n+', '\n\n', desc)
            custom_properties = desc.strip()

        # 4ï¸âƒ£ Calculate dates
        try:
            trans_date = getdate(transaction_date)
        except:
            trans_date = now_datetime().date()
        delivery_date = add_days(trans_date, 7)
        ship_deadline = add_days(trans_date, 6)

        # 5ï¸âƒ£ Check if Sales Order already exists
        existing_order = frappe.db.get_value("Sales Order", {"po_no": po_number}, "name")

        if existing_order:
            sales_order = frappe.get_doc("Sales Order", existing_order)

            # Skip if already submitted
            if sales_order.docstatus == 1:
                return {
                    'status': 'success',
                    'sales_order': sales_order.name,
                    'message': f'Sales Order {sales_order.name} already submitted'
                }

            # Add item to existing order (no duplicate check - each transaction is unique)
            sales_order.append("items", {
                "item_code": product_id,
                "delivery_date": delivery_date,
                "qty": float(qty),
                "rate": float(rate),
                "warehouse": "Finished Goods - CCP",
                "custom_shopify_properties": custom_properties
            })
            sales_order.save(ignore_permissions=True)
            frappe.db.commit()

            # If last item, submit the order
            if current_item >= total_items:
                sales_order.submit()
                frappe.db.commit()
                return {
                    'status': 'success',
                    'sales_order': sales_order.name,
                    'message': f"All {total_items} items added and Sales Order submitted.",
                    'submitted': True
                }

            return {
                'status': 'success',
                'sales_order': sales_order.name,
                'message': f"Item {current_item}/{total_items} added to existing Sales Order.",
                'item_added': True
            }

        # 6ï¸âƒ£ Create new Sales Order
        sales_order = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": customer_name,
            "transaction_date": trans_date,
            "delivery_date": delivery_date,
            "ship_deadline": ship_deadline,
            "company": "Cozy Corner Patios LLC",
            "order_type": "Sales",
            "po_no": po_number,
            "currency": "USD",
            "set_warehouse": "Finished Goods - CCP",
            "shopify_order_number": receipt_id,
            "custom_sales_channel": sales_channel,  # NEW: Save the sales channel (Etsy Maria or Etsy Zipcushions)
            "items": [{
                "item_code": product_id,
                "delivery_date": delivery_date,
                "qty": float(qty),
                "rate": float(rate),
                "warehouse": "Finished Goods - CCP",
                "custom_shopify_properties": custom_properties
            }]
        })
        sales_order.insert(ignore_permissions=True)

        # Submit only if this is the last item
        if total_items == 1 or current_item >= total_items:
            sales_order.submit()

        frappe.db.commit()

        return {
            'status': 'success',
            'sales_order': sales_order.name,
            'message': f"Sales Order {sales_order.name} created successfully.",
            'submitted': current_item >= total_items
        }

    except Exception as e:
        frappe.log_error(f"Etsy Webhook Error: {str(e)}", "Etsy Webhook Failure")
        frappe.db.rollback()
        return {'status': 'error', 'message': str(e)}


# =============================================================================
# UPDATE ADDRESS FUNCTION - WITH EMAIL SUPPORT
# =============================================================================

@frappe.whitelist(allow_guest=False, methods=['POST'])
def update_address():
    """
    Update Sales Order with shipping address from Gmail parsing.
    Called by Make.com scenario that parses Etsy sale notification emails.

    Expected parameters:
    - order_id: Etsy order/receipt number (e.g., "3938139725")
    - recipient_name: Customer name from shipping address
    - address_line1: Street address
    - address_line2: (optional) Apartment, suite, etc.
    - city: City name
    - state: State/province code
    - zip: Postal/ZIP code
    - country: Country name
    - email_id: (NEW) Buyer's email address
    - phone: (NEW) Buyer's phone number (optional)
    """

    try:
        data = frappe.local.form_dict

        # Extract parameters
        order_id = data.get('order_id', '').strip()
        recipient_name = data.get('recipient_name', '').strip()
        address_line1 = data.get('address_line1', '').strip()
        address_line2 = data.get('address_line2', '').strip()
        city = data.get('city', '').strip()
        state = data.get('state', '').strip()
        zip_code = data.get('zip', '').strip()
        country = data.get('country', '').strip()
        email_id = data.get('email_id', '').strip()  # NEW: Extract email
        phone = data.get('phone', '').strip()  # NEW: Extract phone (for future use)

        # Validate required fields
        if not order_id:
            return {
                'status': 'error',
                'message': 'Missing order_id parameter'
            }

        if not address_line1 or not city or not state or not zip_code:
            return {
                'status': 'error',
                'message': 'Missing required address fields (address_line1, city, state, zip)'
            }

        # Find the Sales Order by PO Number (ETSY-{receipt_id})
        po_number = f"ETSY-{order_id}"
        sales_order_name = frappe.db.get_value("Sales Order", {"po_no": po_number}, "name")

        if not sales_order_name:
            return {
                'status': 'error',
                'message': f'Sales Order with PO# {po_number} not found'
            }

        # Get the Sales Order
        sales_order = frappe.get_doc("Sales Order", sales_order_name)
        customer_name = sales_order.customer

        # Format the full address for display
        address_parts = [address_line1]
        if address_line2:
            address_parts.append(address_line2)
        address_parts.append(f"{city}, {state} {zip_code}")
        if country:
            address_parts.append(country)
        full_address = "\n".join(address_parts)

        # Create or update Address in ERPNext
        address_title = f"{recipient_name} - {order_id}"

        # Check if address already exists
        existing_address = frappe.db.get_value("Address", {"address_title": address_title}, "name")

        if existing_address:
            # Update existing address
            address_doc = frappe.get_doc("Address", existing_address)
            address_doc.address_line1 = address_line1
            address_doc.address_line2 = address_line2
            address_doc.city = city
            address_doc.state = state
            address_doc.pincode = zip_code
            address_doc.country = country if country else "United States"
            # NEW: Update email if provided
            if email_id:
                address_doc.email_id = email_id
            # NEW: Update phone if provided
            if phone:
                address_doc.phone = phone
            address_doc.save(ignore_permissions=True)
        else:
            # Create new address
            address_doc = frappe.get_doc({
                "doctype": "Address",
                "address_title": address_title,
                "address_type": "Shipping",
                "address_line1": address_line1,
                "address_line2": address_line2,
                "city": city,
                "state": state,
                "pincode": zip_code,
                "country": country if country else "United States",
                "email_id": email_id if email_id else "",  # NEW: Add email
                "phone": phone if phone else "",  # NEW: Add phone
                "links": [{
                    "link_doctype": "Customer",
                    "link_name": customer_name
                }]
            })
            address_doc.insert(ignore_permissions=True)

        frappe.db.commit()

        # Update Sales Order with shipping address
        # Use db_set to update even submitted documents
        frappe.db.set_value("Sales Order", sales_order_name, {
            "shipping_address_name": address_doc.name,
            "shipping_address": full_address
        }, update_modified=False)
        frappe.db.commit()

        return {
            'status': 'success',
            'message': f'Address updated for Sales Order {sales_order_name}',
            'sales_order': sales_order_name,
            'address': address_doc.name,
            'full_address': full_address,
            'email_id': email_id,  # NEW: Return email in response
            'phone': phone,  # NEW: Return phone in response
            'docstatus': sales_order.docstatus
        }

    except Exception as e:
        frappe.log_error(f"Update Address Error: {str(e)}", "Etsy Address Webhook")
        frappe.db.rollback()
        return {
            'status': 'error',
            'message': str(e)
        }

# =============================================================================
# SHIPSTATION AUTH HELPER
# =============================================================================

def _get_ss_headers():
    import base64
    api_key = frappe.db.get_single_value("ShipStation Settings", "api_key")
    api_secret = frappe.db.get_single_value("ShipStation Settings", "api_secret")
    token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def _fetch_shipstation_order(resource_url):
    import requests
    try:
        resp = requests.get(resource_url, headers=_get_ss_headers(), timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            orders = data.get("orders", [])
            return orders[0] if orders else data
        frappe.log_error(f"SS fetch failed {resp.status_code}: {resource_url}", "SS Fetch")
        return None
    except Exception as e:
        frappe.log_error(str(e), "SS Fetch Exception")
        return None


def _upsert_address(ship_to, customer_name, order_id, email_id="", phone=""):
    address_title = f"{ship_to.get('name', customer_name)} - {order_id}"
    address_line1 = (ship_to.get("street1") or "").strip()
    address_line2 = (ship_to.get("street2") or "").strip()
    city          = (ship_to.get("city") or "").strip()
    state         = (ship_to.get("state") or "").strip()
    pincode       = (ship_to.get("postalCode") or "").strip()
    country       = (ship_to.get("country") or "United States").strip()
    phone_val     = (ship_to.get("phone") or phone or "").strip()

    existing = frappe.db.get_value("Address", {"address_title": address_title}, "name")
    if existing:
        addr = frappe.get_doc("Address", existing)
        addr.address_line1 = address_line1
        addr.address_line2 = address_line2
        addr.city    = city
        addr.state   = state
        addr.pincode = pincode
        addr.country = country
        if email_id: addr.email_id = email_id
        if phone_val: addr.phone = phone_val
        addr.save(ignore_permissions=True)
    else:
        addr = frappe.get_doc({
            "doctype": "Address",
            "address_title": address_title,
            "address_type": "Shipping",
            "address_line1": address_line1,
            "address_line2": address_line2,
            "city": city, "state": state,
            "pincode": pincode, "country": country,
            "email_id": email_id, "phone": phone_val,
            "links": [{"link_doctype": "Customer", "link_name": customer_name}],
        })
        addr.insert(ignore_permissions=True)

    frappe.db.commit()
    parts = [address_line1]
    if address_line2: parts.append(address_line2)
    parts.append(f"{city}, {state} {pincode}")
    if country and country not in ("US", "United States"): parts.append(country)
    return addr.name, "\n".join(parts)


def _upsert_sales_order(order):
    import re
    from frappe.utils import getdate, add_days

    order_number   = order.get("orderNumber", "")
    order_status   = order.get("orderStatus", "")
    ss_order_id    = str(order.get("orderId", ""))
    customer_email = (order.get("customerEmail") or "").strip()
    customer_notes = (order.get("customerNotes") or "").strip()
    internal_notes = (order.get("internalNotes") or "").strip()
    ship_by_date   = order.get("shipByDate")
    order_date_raw = order.get("orderDate", "")
    modify_date    = order.get("modifyDate", "")
    requested_svc  = (order.get("requestedShippingService") or "").strip()
    carrier_code   = (order.get("carrierCode") or "").strip()
    tax_amount     = order.get("taxAmount", 0)
    shipping_amt   = order.get("shippingAmount", 0)
    ship_to        = order.get("shipTo", {})
    items_list     = order.get("items", [])

    receipt_id = re.sub(r"^ETSY-", "", order_number).strip()
    po_number  = f"ETSY-{receipt_id}"

    customer_name = (
        ship_to.get("name") or order.get("customerUsername")
        or order.get("billTo", {}).get("name") or "Etsy Customer"
    ).strip()

    try:
        trans_date = getdate(order_date_raw)
    except Exception:
        trans_date = now_datetime().date()

    try:
        delivery_date = getdate(ship_by_date) if ship_by_date else add_days(trans_date, 7)
    except Exception:
        delivery_date = add_days(trans_date, 7)

    ship_deadline = add_days(trans_date, 6)

    if not frappe.db.exists("Customer", customer_name):
        frappe.get_doc({
            "doctype": "Customer",
            "customer_name": customer_name,
            "customer_group": "All Customer Groups",
            "territory": "All Territories",
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    cust = frappe.get_doc("Customer", customer_name)
    if customer_email and not cust.get("email_id"):
        frappe.db.set_value("Customer", customer_name, "email_id", customer_email)
    if ship_to.get("phone") and not cust.get("mobile_no"):
        frappe.db.set_value("Customer", customer_name, "mobile_no", ship_to.get("phone"))

    so_items = []
    for item in items_list:
        sku       = (item.get("sku") or item.get("lineItemKey") or "ETSY-ITEM").strip()
        item_name = (item.get("name") or sku).strip()
        qty       = float(item.get("quantity", 1))
        rate      = float(item.get("unitPrice", 0))
        options   = item.get("options", [])
        custom_props = "\n".join(
            f"{o.get('name','')}: {o.get('value','')}"
            for o in options if o.get("name")
        )
        if not frappe.db.exists("Item", sku):
            frappe.get_doc({
                "doctype": "Item", "item_code": sku, "item_name": item_name,
                "item_group": "Products", "is_sales_item": 1,
                "include_item_in_manufacturing": 0,
            }).insert(ignore_permissions=True)
            frappe.db.commit()
        so_items.append({
            "item_code": sku, "delivery_date": delivery_date,
            "qty": qty, "rate": rate,
            "warehouse": "Finished Goods - CCP",
            "custom_shopify_properties": custom_props,
        })

    if not so_items:
        return {"status": "error", "message": "No items in order"}

    should_submit = order_status in ("awaiting_shipment", "shipped")
    enrichment = {
        "custom_shipstation_order_id":   ss_order_id,
        "custom_shipstation_status":     order_status,
        "custom_shipping_service":        requested_svc,
        "custom_carrier_code":            carrier_code,
        "custom_shipstation_modify_date": modify_date,
        "custom_shipping_amount":         shipping_amt,
        "custom_tax_amount":              tax_amount,
    }

    existing_name = None
    if ss_order_id:
        existing_name = frappe.db.get_value(
            "Sales Order", {"custom_shipstation_order_id": ss_order_id}, "name"
        )
    if not existing_name:
        existing_name = frappe.db.get_value("Sales Order", {"po_no": po_number}, "name")

    if existing_name:
        so = frappe.get_doc("Sales Order", existing_name)
        if so.docstatus == 1:
            frappe.db.set_value("Sales Order", so.name, enrichment, update_modified=False)
        else:
            so.items = []
            for i in so_items: so.append("items", i)
            so.transaction_date = trans_date
            so.delivery_date    = delivery_date
            so.ship_deadline    = ship_deadline
            so.customer_notes   = customer_notes
            so.instructions     = internal_notes
            for k, v in enrichment.items(): setattr(so, k, v)
            so.save(ignore_permissions=True)
            if should_submit: so.submit()
        frappe.db.commit()
        so_name = so.name
    else:
        so = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": customer_name,
            "transaction_date": trans_date,
            "delivery_date": delivery_date,
            "ship_deadline": ship_deadline,
            "company": "Cozy Corner Patios LLC",
            "order_type": "Sales",
            "po_no": po_number,
            "currency": "USD",
            "set_warehouse": "Finished Goods - CCP",
            "shopify_order_number": receipt_id,
            "customer_notes": customer_notes,
            "instructions": internal_notes,
            "items": so_items,
            **enrichment,
        })
        so.insert(ignore_permissions=True)
        if should_submit: so.submit()
        frappe.db.commit()
        so_name = so.name

    if ship_to.get("street1"):
        addr_name, full_address = _upsert_address(
            ship_to, customer_name, receipt_id,
            customer_email, ship_to.get("phone", "")
        )
        frappe.db.set_value("Sales Order", so_name, {
            "shipping_address_name": addr_name,
            "shipping_address":      full_address,
        }, update_modified=False)
        frappe.db.commit()

    return {"status": "success", "sales_order": so_name, "po_no": po_number}


def _handle_cancellation(order):
    import re
    order_number = order.get("orderNumber", "")
    receipt_id   = re.sub(r"^ETSY-", "", order_number).strip()
    po_number    = f"ETSY-{receipt_id}"
    ss_order_id  = str(order.get("orderId", ""))

    so_name = None
    if ss_order_id:
        so_name = frappe.db.get_value(
            "Sales Order", {"custom_shipstation_order_id": ss_order_id}, "name"
        )
    if not so_name:
        so_name = frappe.db.get_value("Sales Order", {"po_no": po_number}, "name")

    if not so_name:
        frappe.log_error(f"Cancel: SO not found for {po_number}", "ShipStation Cancel")
        return {"status": "not_found", "po_no": po_number}

    so = frappe.get_doc("Sales Order", so_name)
    if so.docstatus == 2:
        return {"status": "already_cancelled", "sales_order": so.name}

    try:
        so.cancel()
    except Exception as e:
        frappe.log_error(str(e), f"Cancel SO failed: {so.name}")
        return {"status": "error", "message": str(e)}

    frappe.db.set_value("Sales Order", so.name, {
        "custom_shipstation_status": "cancelled",
        "custom_cancelled_at":       now_datetime(),
    }, update_modified=False)

    if frappe.db.exists("DocType", "Etsy Cancelled Order"):
        items_summary = ", ".join(
            f"{i.get('quantity')}x {i.get('name','?')}"
            for i in order.get("items", [])
        )
        frappe.get_doc({
            "doctype": "Etsy Cancelled Order",
            "sales_order":          so.name,
            "etsy_receipt_id":      po_number,
            "shipstation_order_id": ss_order_id,
            "customer":             so.customer,
            "order_date":           so.transaction_date,
            "cancelled_at":         now_datetime(),
            "cancellation_source":  "ShipStation",
            "items_summary":        items_summary,
            "total_amount":         order.get("amountPaid", 0),
        }).insert(ignore_permissions=True)

    frappe.db.commit()
    return {"status": "cancelled", "sales_order": so.name}


# =============================================================================
# ENDPOINT 1 â€” ORDER_NOTIFY (new order from Etsy into ShipStation)
# =============================================================================

@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive_shipstation_order():
    """
    Fires when a NEW Etsy order arrives in ShipStation.
    Creates a full Sales Order in ERPNext with address, email, phone, shipping service.

    ShipStation setup:
      Settings -> Integration Partners -> Webhooks -> Subscribe
      Event: On New Orders
      URL: https://your-erp.com/api/method/etsy_integration.api.etsy_webhook.receive_shipstation_order
    """
    try:
        data = json.loads(frappe.request.data or "{}")
        resource_type = data.get("resource_type", "")
        resource_url  = data.get("resource_url", "")

        if resource_type != "ORDER_NOTIFY":
            return {"status": "ignored", "resource_type": resource_type}

        if not resource_url:
            return {"status": "error", "message": "Missing resource_url"}

        order = _fetch_shipstation_order(resource_url)
        if not order:
            return {"status": "error", "message": "Could not fetch order from ShipStation"}

        if order.get("orderStatus") == "cancelled":
            return {"status": "skipped", "reason": "cancellation handled by 30-min poller"}

        return _upsert_sales_order(order)

    except Exception as e:
        frappe.log_error(str(e), "receive_shipstation_order")
        frappe.db.rollback()
        return {"status": "error", "message": str(e)}


# =============================================================================
# ENDPOINT 2 â€” ITEM_ORDER_NOTIFY (new line item added to existing order)
# =============================================================================

@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive_shipstation_item():
    """
    Fires when a new line item is added to an existing order in ShipStation.
    Adds the item to the existing Sales Order in ERPNext.

    ShipStation setup:
      Settings -> Integration Partners -> Webhooks -> Subscribe
      Event: On New Items
      URL: https://your-erp.com/api/method/etsy_integration.api.etsy_webhook.receive_shipstation_item
    """
    try:
        data = json.loads(frappe.request.data or "{}")
        resource_type = data.get("resource_type", "")
        resource_url  = data.get("resource_url", "")

        if resource_type != "ITEM_ORDER_NOTIFY":
            return {"status": "ignored", "resource_type": resource_type}

        if not resource_url:
            return {"status": "error", "message": "Missing resource_url"}

        order = _fetch_shipstation_order(resource_url)
        if not order:
            return {"status": "error", "message": "Could not fetch order from ShipStation"}

        order_number = order.get("orderNumber", "")
        import re
        receipt_id = re.sub(r"^ETSY-", "", order_number).strip()
        po_number  = f"ETSY-{receipt_id}"

        so_name = frappe.db.get_value("Sales Order", {"po_no": po_number}, "name")

        if not so_name:
            # Order not in ERP yet â€” create it fully
            return _upsert_sales_order(order)

        so = frappe.get_doc("Sales Order", so_name)

        if so.docstatus == 1:
            # Submitted â€” flag for manual review, stamp modify date
            frappe.db.set_value("Sales Order", so_name, {
                "custom_shipstation_status":     order.get("orderStatus", ""),
                "custom_shipstation_modify_date": order.get("modifyDate", ""),
            }, update_modified=False)
            frappe.db.commit()
            return {
                "status": "review_needed",
                "message": f"New item added to submitted SO {so_name} â€” review manually",
                "sales_order": so_name,
            }

        # Draft â€” full upsert rebuilds items from latest ShipStation data
        return _upsert_sales_order(order)

    except Exception as e:
        frappe.log_error(str(e), "receive_shipstation_item")
        frappe.db.rollback()
        return {"status": "error", "message": str(e)}


def on_so_cancel(doc, method=None):
    """Logs when an Etsy Sales Order is manually cancelled inside ERPNext."""
    if not doc.po_no or not doc.po_no.startswith("ETSY-"):
        return
    if not frappe.db.exists("DocType", "Etsy Cancelled Order"):
        return
    if frappe.db.get_value("Etsy Cancelled Order", {"sales_order": doc.name}, "name"):
        return
    frappe.get_doc({
        "doctype": "Etsy Cancelled Order",
        "sales_order":         doc.name,
        "etsy_receipt_id":     doc.po_no,
        "customer":            doc.customer,
        "order_date":          doc.transaction_date,
        "cancelled_at":        now_datetime(),
        "cancellation_source": "Manual (ERP)",
        "total_amount":        doc.grand_total,
        "notes":               "Cancelled manually inside ERPNext",
    }).insert(ignore_permissions=True)
    frappe.db.commit()


