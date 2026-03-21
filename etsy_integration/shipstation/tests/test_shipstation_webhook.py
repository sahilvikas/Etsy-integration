import unittest
from unittest.mock import patch


class TestShipStationOrderParsing(unittest.TestCase):
	def _real_payload(self):
		return {
			"orderId": 230668473,
			"orderNumber": "3899306564",
			"orderStatus": "shipped",
			"customerUsername": "27653963",
			"customerEmail": "jill.a.hennessy@gmail.com",
			"billTo": {"name": "Jill Hennessy"},
			"shipTo": {
				"name": "Jill Hennessy",
				"street1": "264 E BROADWAY APT C507",
				"street2": "",
				"city": "NEW YORK",
				"state": "NY",
				"postalCode": "10002-6090",
				"country": "US",
				"phone": None,
			},
			"items": [
				{
					"lineItemKey": "4878147615",
					"sku": "4419894329",
					"name": "Custom Cushion",
					"quantity": 1,
					"unitPrice": 442.98,
					"options": [],
					"adjustment": False,
				},
				{
					"lineItemKey": "Discount",
					"sku": "",
					"name": "Discount",
					"quantity": 1,
					"unitPrice": -44.30,
					"options": [],
					"adjustment": True,
				},
			],
			"advancedOptions": {"storeId": 3673870},
			"customerNotes": None,
			"internalNotes": None,
		}

	def test_get_receipt_id_without_prefix(self):
		from etsy_integration.shipstation.order import _get_receipt_id
		self.assertEqual(_get_receipt_id("3899306564"), "3899306564")

	@patch("etsy_integration.shipstation.order._ensure_item_exists")
	def test_build_so_items_skips_adjustments(self, ensure_item):
		from etsy_integration.shipstation.order import _build_so_items
		items = self._real_payload()["items"]
		out = _build_so_items(items, "2026-01-08")
		self.assertEqual(len(out), 1)
		self.assertEqual(out[0]["item_code"], "4419894329")
		ensure_item.assert_called_once()

	@patch("etsy_integration.shipstation.order.frappe.db.set_value")
	@patch("etsy_integration.shipstation.order.frappe.db.get_value", return_value=None)
	@patch("etsy_integration.shipstation.order.frappe.db.exists", return_value=True)
	def test_sync_customer_uses_real_name_not_numeric_username(self, _exists, _get_value, _set_value):
		from etsy_integration.shipstation.order import _sync_customer
		name = _sync_customer(self._real_payload())
		self.assertEqual(name, "Jill Hennessy")

	def test_null_notes_become_empty_strings(self):
		payload = self._real_payload()
		self.assertEqual((payload.get("customerNotes") or ""), "")
		self.assertEqual((payload.get("internalNotes") or ""), "")
