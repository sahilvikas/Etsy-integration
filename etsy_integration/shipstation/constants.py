# ShipStation Settings DocType name (your existing settings page)
SETTING_DOCTYPE = "ShipStation Settings"
MODULE_NAME = "shipstation"

# Maps ShipStation resource_type to the handler function
# Only ORDER_NOTIFY is handled — new orders only
EVENT_MAPPER = {
    "ORDER_NOTIFY": "etsy_integration.shipstation.order.sync_sales_order",
}

# Custom fields on Sales Order
SS_ORDER_ID_FIELD = "custom_shipstation_order_id"
SS_STATUS_FIELD = "custom_shipstation_status"

# Maps ShipStation storeId to custom_sales_channel on Sales Order
# Add a Small Text field called store_channel_map on ShipStation Settings
# via Customize Form, then fill it with JSON like:
# {"12345": "Etsy Maria", "67890": "Etsy ZipCushions"}
STORE_CHANNEL_MAP_FIELD = "store_channel_map"
