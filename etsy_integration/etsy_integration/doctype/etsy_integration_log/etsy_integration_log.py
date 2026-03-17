import json

import frappe
from frappe.model.document import Document


class EtsyIntegrationLog(Document):
    def before_insert(self):
        self._set_title()

    def _set_title(self):
        order_number = ""
        if self.request_data:
            try:
                data = json.loads(self.request_data)
                order_number = data.get("orderNumber", "")
            except Exception:
                pass
        if order_number:
            self.title = f"{self.event_type} — {order_number}"
        else:
            self.title = self.event_type or "ShipStation Event"
