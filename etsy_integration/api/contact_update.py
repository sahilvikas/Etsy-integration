import frappe


@frappe.whitelist(allow_guest=True, methods=['POST'])
def update_contact():
    """
    Receives buyer email from Make.com and stores it in the Address document.
    
    Make.com sends:
    - receipt_id: Etsy receipt number
    - email: Buyer's email address
    
    Finds Sales Order by shopify_order_number, then stores email
    in the linked Address document's email_id field.
    """
    try:
        data = frappe.request.get_json() or frappe.form_dict

        receipt_id = data.get("receipt_id") or ""
        email = data.get("email") or ""

        if not receipt_id:
            return {"status": "error", "message": "Missing receipt_id"}

        if not email:
            return {"status": "error", "message": "No email provided"}

        sales_orders = frappe.get_all(
            "Sales Order",
            filters={"shopify_order_number": receipt_id},
            fields=["name", "customer", "shipping_address_name"],
            limit=5
        )

        if not sales_orders:
            return {"status": "error", "message": f"No Sales Order found for receipt {receipt_id}"}

        updated = []
        for so in sales_orders:
            if so.shipping_address_name:
                frappe.db.set_value("Address", so.shipping_address_name, "email_id", email, update_modified=False)
                updated.append(so.name)
            elif so.customer:
                addr_name = f"{so.customer} - {receipt_id}-Shipping"
                if frappe.db.exists("Address", addr_name):
                    frappe.db.set_value("Address", addr_name, "email_id", email, update_modified=False)
                    updated.append(so.name)

        frappe.db.commit()

        return {
            "status": "success",
            "message": f"Updated {len(updated)} addresses",
            "sales_orders": updated,
            "receipt_id": receipt_id,
            "email": email
        }

    except Exception as e:
        frappe.log_error(f"Contact Update Error: {str(e)}", "Etsy Contact Update")
        frappe.db.rollback()
        return {"status": "error", "message": str(e)}