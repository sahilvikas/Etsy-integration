import frappe

WIDE_FIELDTYPES = ("Small Text", "Text", "Long Text", "Text Editor", "Code")


def execute():
    """Widen custom_shopify_properties on Sales Order Item.

    A Data field is capped at 140 chars in the DB, so long Etsy customization
    text gets re-truncated on save. Convert it to Small Text.
    """
    name = frappe.db.get_value(
        "Custom Field",
        {"dt": "Sales Order Item", "fieldname": "custom_shopify_properties"},
        "name",
    )
    if not name:
        return

    cf = frappe.get_doc("Custom Field", name)
    if cf.fieldtype in WIDE_FIELDTYPES:
        return

    cf.fieldtype = "Small Text"
    cf.length = 0
    cf.save(ignore_permissions=True)

    frappe.clear_cache(doctype="Sales Order Item")
