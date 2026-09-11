import frappe
import requests

from etsy_integration.utils.etsy_api import (
    get_settings, get_headers, refresh_token,
    format_address, get_order_date
)

RECEIPTS_URL = "https://openapi.etsy.com/v3/application/shops/{shop_id}/receipts"
PAGE_LIMIT = 100
MAX_OFFSET = 20000

STORE_CONF = {
    "maria": {
        "settings_key": "maria",
        "process_module": "etsy_integration.tasks.fetch_maria_orders",
        "log_doctype": "Etsy Maria Order Log",
        "store_label": "Maria"
    },
    "zipcushions": {
        "settings_key": "zipcushions",
        "process_module": "etsy_integration.tasks.fetch_zip_orders",
        "log_doctype": "Etsy Zipcushions Order Log",
        "store_label": "Zipcushions"
    }
}

STORE_LABEL_TO_KEY = {"Maria": "maria", "Zipcushions": "zipcushions"}


def _to_unix(dt_value):
    """Convert a datetime (or datetime string) to a unix timestamp"""
    if not dt_value:
        return None

    value = str(dt_value)
    if "." in value:
        value = value.split(".")[0]

    result = frappe.db.sql("SELECT UNIX_TIMESTAMP(%s)", (value,))
    if result and result[0][0]:
        return int(result[0][0])
    return None


def _fetch_receipts_in_window(settings, min_created, max_created):
    """Page through ALL paid receipts in the window"""
    url = RECEIPTS_URL.format(shop_id=settings.shop_id)
    headers = get_headers(settings)

    receipts = []
    offset = 0

    while True:
        params = {
            "was_paid": "true",
            "limit": PAGE_LIMIT,
            "offset": offset,
            "sort_on": "created",
            "sort_order": "desc"
        }
        if min_created:
            params["min_created"] = int(min_created)
        if max_created:
            params["max_created"] = int(max_created)

        response = requests.get(url, headers=headers, params=params)

        # Token expired mid-scan — refresh once and retry this page
        if response.status_code == 401:
            if not refresh_token(settings):
                break
            headers = get_headers(settings)
            response = requests.get(url, headers=headers, params=params)

        response.raise_for_status()
        rows = response.json().get("results", []) or []
        receipts.extend(rows)

        if len(rows) < PAGE_LIMIT:
            break

        offset += PAGE_LIMIT
        if offset > MAX_OFFSET:
            break

    return receipts


def _log_gap(store_label, receipt, status, reason, sales_order=None, error_message=None):
    """Upsert an Etsy Reconciliation Log row for this receipt + store"""
    receipt_id = str(receipt.get("receipt_id", ""))

    subtotal = receipt.get("subtotal", {}) or {}
    order_total = (subtotal.get("amount", 0) or 0) / (subtotal.get("divisor", 100) or 100)

    values = {
        "status": status,
        "reason": reason,
        "customer_name": receipt.get("name", ""),
        "order_date": get_order_date(receipt.get("created_timestamp", 0)),
        "order_total": order_total,
        "sales_order": sales_order,
        "error_message": (error_message or "")[:500],
        "checked_at": frappe.utils.now_datetime()
    }

    existing = frappe.db.exists(
        "Etsy Reconciliation Log",
        {"receipt_id": receipt_id, "store": store_label}
    )

    if existing:
        log = frappe.get_doc("Etsy Reconciliation Log", existing)
        log.update(values)
        log.save(ignore_permissions=True)
    else:
        values.update({
            "doctype": "Etsy Reconciliation Log",
            "receipt_id": receipt_id,
            "store": store_label
        })
        frappe.get_doc(values).insert(ignore_permissions=True)

    frappe.db.commit()


def reconcile_store(store, min_created=None, max_created=None, now=None):
    """Verify every paid receipt in the window became a Sales Order, retry the ones that didn't"""
    conf = STORE_CONF.get(store)
    if not conf:
        frappe.throw(f"Unknown store: {store}")

    settings = get_settings(conf["settings_key"])
    now = now or frappe.utils.now_datetime()

    receipts = _fetch_receipts_in_window(settings, min_created, max_created)

    checked = 0
    recovered = 0
    missing = 0

    for receipt in receipts:
        receipt_id = str(receipt.get("receipt_id", ""))
        if not receipt_id:
            continue

        checked += 1
        po_no = f"ETSY-{receipt_id}"

        # Already made it through — nothing to do
        if frappe.db.exists("Sales Order", {"po_no": po_no}):
            continue

        # Retry the order once through the store's own processor
        retry_error = None
        try:
            process_order = frappe.get_attr(conf["process_module"] + ".process_order")
            process_order(receipt, settings, now)
        except Exception as e:
            retry_error = str(e)
            frappe.db.rollback()

        sales_order = frappe.db.exists("Sales Order", {"po_no": po_no})

        if sales_order:
            recovered += 1
            _log_gap(
                conf["store_label"], receipt, "Created",
                "Recovered on reconcile retry", sales_order=sales_order
            )
            continue

        # Still no Sales Order — record why
        missing += 1

        if retry_error:
            reason = "Retry failed"
            error_message = retry_error
        else:
            failed_log = frappe.db.get_value(
                conf["log_doctype"],
                {"receipt_id": receipt_id, "status": "Failed"},
                ["name", "error_message"],
                as_dict=True
            )
            if failed_log:
                reason = "Logged Failed, retry produced no SO"
                error_message = failed_log.get("error_message")
            else:
                reason = "No SO and no log row (dropped/never fetched)"
                error_message = None

        _log_gap(conf["store_label"], receipt, "Missing", reason, error_message=error_message)

    if recovered or missing:
        frappe.log_error(
            f"Etsy {conf['store_label']} reconcile: "
            f"checked={checked} recovered={recovered} missing={missing}",
            "Etsy Reconcile"
        )

    return {"checked": checked, "recovered": recovered, "missing": missing}


def reconcile_window(store, window_start, now):
    """Inline entry point: reconcile just the window the fetch task covered"""
    try:
        return reconcile_store(
            store,
            min_created=_to_unix(window_start),
            max_created=_to_unix(now),
            now=now
        )
    except Exception as e:
        frappe.log_error(f"Etsy {store} reconcile window error: {str(e)}", "Etsy Reconcile")
        return None


def run_full_scan(store):
    """Manual/console entry point: reconcile the store's entire history"""
    return reconcile_store(store)


def _update_recon_row(receipt_id, store_label, status, reason, sales_order=None, error_message=None):
    """Update an existing Etsy Reconciliation Log row after a manual retry"""
    name = frappe.db.exists(
        "Etsy Reconciliation Log",
        {"receipt_id": receipt_id, "store": store_label}
    )
    if not name:
        return

    row = frappe.get_doc("Etsy Reconciliation Log", name)
    row.status = status
    row.reason = reason
    if sales_order:
        row.sales_order = sales_order
    if error_message is not None:
        row.error_message = (error_message or "")[:500]
    row.checked_at = frappe.utils.now_datetime()
    row.save(ignore_permissions=True)
    frappe.db.commit()


@frappe.whitelist()
def retry_receipt(receipt_id, store):
    """Re-run a single Etsy receipt through its store's order processor"""
    receipt_id = str(receipt_id or "").strip()
    store_key = STORE_LABEL_TO_KEY.get(store, str(store or "").lower())

    if store_key not in STORE_CONF:
        frappe.throw(f"Unknown store: {store}")

    conf = STORE_CONF[store_key]
    settings = get_settings(conf["settings_key"])
    store_label = conf["store_label"]

    po_no = f"ETSY-{receipt_id}"

    # Someone (or an earlier run) already created it
    existing_so = frappe.db.exists("Sales Order", {"po_no": po_no})
    if existing_so:
        _update_recon_row(
            receipt_id, store_label, "Created",
            "Already existed on retry", sales_order=existing_so
        )
        return {
            "ok": True,
            "message": f"Sales Order already exists: {existing_so}",
            "sales_order": existing_so
        }

    url = f"https://openapi.etsy.com/v3/application/shops/{settings.shop_id}/receipts/{receipt_id}"
    response = requests.get(url, headers=get_headers(settings))

    if response.status_code == 401:
        refresh_token(settings)
        settings = get_settings(conf["settings_key"])
        response = requests.get(url, headers=get_headers(settings))

    if response.status_code != 200:
        _update_recon_row(
            receipt_id, store_label, "Missing",
            f"Retry fetch failed HTTP {response.status_code}",
            error_message=response.text[:500]
        )
        return {
            "ok": False,
            "message": f"Could not fetch receipt {receipt_id} from Etsy (HTTP {response.status_code})"
        }

    receipt = response.json() or {}

    if not receipt.get("receipt_id"):
        _update_recon_row(
            receipt_id, store_label, "Missing",
            "Retry fetch returned no receipt",
            error_message=response.text[:500]
        )
        return {
            "ok": False,
            "message": f"Etsy returned no receipt for {receipt_id}"
        }

    process_order = frappe.get_attr(conf["process_module"] + ".process_order")
    now = frappe.utils.now_datetime()

    retry_error = None
    try:
        process_order(receipt, settings, now)
        frappe.db.commit()
    except Exception as e:
        retry_error = str(e)
        frappe.db.rollback()

    sales_order = frappe.db.exists("Sales Order", {"po_no": po_no})

    if sales_order:
        _update_recon_row(
            receipt_id, store_label, "Created",
            "Recovered on manual retry", sales_order=sales_order
        )
        return {
            "ok": True,
            "message": f"Created Sales Order: {sales_order}",
            "sales_order": sales_order
        }

    reason = "Manual retry failed" if retry_error else "Manual retry produced no SO"
    _update_recon_row(receipt_id, store_label, "Missing", reason, error_message=retry_error)
    return {
        "ok": False,
        "message": retry_error or f"Retry produced no Sales Order for {receipt_id}"
    }
