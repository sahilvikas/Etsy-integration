app_name = "etsy_integration"
app_title = "Etsy Integration"
app_publisher = "Harshit"
app_description = "Custom integration for Etsy orders via ShipStation"
app_email = "harshitv998001@gmail.com"
app_license = "mit"

override_whitelisted_methods = {
    # -------------------------------------------------------------------------
    # DEPRECATED — Make.com scenarios pointed to these.
    # Keep alive until Make.com scenarios are fully disabled.
    # After 7 days of parallel running, delete these two lines.
    # -------------------------------------------------------------------------
    "etsy_integration.api.etsy_webhook.receive_order": "etsy_integration.api.etsy_webhook.receive_order",
    "etsy_integration.api.etsy_webhook.update_address": "etsy_integration.api.etsy_webhook.update_address",

    # -------------------------------------------------------------------------
    # NEW — Single ShipStation webhook endpoint (replaces all Make.com)
    # Register this URL in ShipStation for ORDER_NOTIFY only:
    # https://your-erp.com/api/method/etsy_integration.shipstation.connection.store_request_data
    # -------------------------------------------------------------------------
    "etsy_integration.shipstation.connection.store_request_data": "etsy_integration.shipstation.connection.store_request_data",
}

# No scheduler needed — ShipStation pushes to us via webhooks
scheduler_events = {
    "cron": {
        "*/30 * * * *": [
            "etsy_integration.tasks.poll_cancelled_orders"
        ],
    }
}

# No doc events needed — cancellation handled via Server Scripts
doc_events = {}



