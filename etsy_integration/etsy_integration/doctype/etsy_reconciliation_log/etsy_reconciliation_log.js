frappe.ui.form.on("Etsy Reconciliation Log", {
    refresh(frm) {
        if (frm.is_new()) {
            return;
        }

        if (frm.doc.status !== "Missing") {
            return;
        }

        frm.add_custom_button(__("Retry This Order"), function () {
            frappe.confirm(
                __("Retry creating a Sales Order for receipt {0}?", [frm.doc.receipt_id]),
                function () {
                    frappe.call({
                        method: "etsy_integration.tasks.reconcile.retry_receipt",
                        args: {
                            receipt_id: frm.doc.receipt_id,
                            store: frm.doc.store
                        },
                        freeze: true,
                        freeze_message: __("Retrying order..."),
                        callback: function (r) {
                            if (r.message && r.message.ok) {
                                frappe.show_alert({
                                    message: r.message.message,
                                    indicator: "green"
                                });
                            } else {
                                frappe.msgprint({
                                    title: __("Retry Failed"),
                                    message: (r.message && r.message.message) || __("Retry failed"),
                                    indicator: "red"
                                });
                            }
                            frm.reload_doc();
                        }
                    });
                }
            );
        }).addClass("btn-primary");
    }
});
