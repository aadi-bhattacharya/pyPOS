"""Safeguards for imported textile stock, legacy invoices and partial returns."""
import unittest

from base import POSTestCase
from pos import catalog, catalog_import, sales, settings
from pos.product_display import receipt_parts


class BillingAndImportRepairTests(POSTestCase):
    def stock_file(self, tax="", cost="", qty="3", sku="CLO-1"):
        header = "sku,product_name,barcode,price,quantity,cost_price,tax_rate,hsn_code,unit_code,attr:Colour,attr:Size,attr:Source Sheet,notes\n"
        line = f"{sku},Clovia Lounge set,,1499,{qty},{cost},{tax},,NOS,Blue Print,FS,Others,Snapshot import from old workbook\n"
        items, issues = catalog_import.parse((header + line).encode(), "stock.csv")
        self.assertFalse(issues)
        return items, issues

    def do_sale(self, pid, qty=1, **kwargs):
        return sales.checkout(self.conn, [{"product_id": pid, "quantity": qty}],
                              [{"method": "CASH", "amount": 99999}], **kwargs)

    def test_old_receipt_is_cleaned_without_mutating_original_sale(self):
        items, errors = self.stock_file()
        catalog_import.apply(self.conn, items, errors)
        pid = catalog.list_products(self.conn)[0]["id"]
        # Before the patch, historical sales snapshot all attributes in one name.
        raw = ("Clovia Lounge set (Colour: Blue Print, Cost Review Required: No purchase cost "
               "supplied; stored as ₹0, GST Review Required: No GST rate supplied; stored as 0%, "
               "Size: FS, Source Row: 105, Source Sheet: Others)")
        catalog.update_product(self.conn, pid, tax_rate=0, verified_gst=True)
        sale = self.do_sale(pid)
        self.conn.execute("UPDATE sale_items SET description_at_sale=? WHERE sale_id=?", (raw, sale["id"]))
        self.conn.commit()
        detail = sales.get_sale_detail(self.conn, sale["id"])
        self.assertEqual(detail["items"][0]["receipt_name"], "Clovia Lounge set")
        self.assertEqual(detail["items"][0]["receipt_variant"], "Colour: Blue Print, Size: FS")
        self.assertTrue(detail["tax_review_warning"])  # Legacy snapshot still contains the historical review note.
        self.assertNotIn("Cost Review", detail["items"][0]["name"])
        self.assertNotIn("Source", detail["items"][0]["name"])
        self.assertEqual(self.conn.execute(
            "SELECT description_at_sale FROM sale_items WHERE sale_id=?", (sale["id"],)
        ).fetchone()[0], raw)
        # Independent financial record is unchanged.
        self.assertEqual(sales.get_sale_detail(self.conn, sale["id"])["total"], 1499)

    def test_new_receipt_only_printable_variant_fields(self):
        items, errors = self.stock_file(tax="5")
        catalog_import.apply(self.conn, items, errors)
        pid = catalog.list_products(self.conn)[0]["id"]
        sale = self.do_sale(pid)
        item = sale["items"][0]
        self.assertEqual(item["receipt_name"], "Clovia Lounge set")
        self.assertEqual(item["receipt_variant"], "Colour: Blue Print, Size: FS")
        self.assertFalse(sale["tax_review_warning"])
        self.assertNotIn("Source Sheet", item["name"])
        self.assertNotIn("Import Notes", item["name"])
        self.assertEqual(item["tax_rate"], 5)
        self.assertTrue(any(a["name"] == "Import Notes" for a in
                            catalog.get_product(self.conn, pid)["attributes"]))

    def test_registered_shop_cannot_bill_unverified_gst(self):
        items, errors = self.stock_file()
        catalog_import.apply(self.conn, items, errors)
        pid = catalog.list_products(self.conn)[0]["id"]
        settings.save_settings(self.conn, {"store_gstin": "19ABCDE1234F1Z5", "store_state_code": "19"})
        with self.assertRaisesRegex(sales.InvalidSaleError, "GST rate unverified"):
            self.do_sale(pid)
        self.assertEqual(catalog.get_product(self.conn, pid)["quantity"], 3)
        # Editing without an explicit review acknowledgement must not hide the flag.
        catalog.update_product(self.conn, pid, tax_rate=5)
        with self.assertRaisesRegex(sales.InvalidSaleError, "GST rate unverified"):
            self.do_sale(pid)
        catalog.update_product(self.conn, pid, tax_rate=5, verified_gst=True)
        self.assertFalse(any(a["name"] == "GST Review Required" for a in
                             catalog.get_product(self.conn, pid)["attributes"]))
        self.assertGreater(self.do_sale(pid)["tax_total"], 0)

    def test_legitimate_zero_rate_can_be_explicitly_confirmed(self):
        items, errors = self.stock_file()
        catalog_import.apply(self.conn, items, errors)
        pid = catalog.list_products(self.conn)[0]["id"]
        settings.save_settings(self.conn, {"store_gstin": "19ABCDE1234F1Z5"})
        catalog.update_product(self.conn, pid, tax_rate=0, verified_gst=True)
        self.assertEqual(self.do_sale(pid)["tax_total"], 0)

    def test_price_review_required_blocks_sale_until_acknowledged(self):
        items, errors = self.stock_file()
        items[0]["price"] = None
        catalog_import.apply(self.conn, items, errors, allow_zero_prices=True)
        pid = catalog.list_products(self.conn)[0]["id"]
        with self.assertRaisesRegex(sales.InvalidSaleError, "Selling price needs review"):
            self.do_sale(pid)
        catalog.update_product(self.conn, pid, price=1499, verified_price=True)
        with self.assertRaisesRegex(sales.InvalidSaleError, "GST rate unverified"):
            self.do_sale(pid)
        catalog.update_product(self.conn, pid, tax_rate=0, verified_gst=True)
        self.assertEqual(self.do_sale(pid)["total"], 1499)

    def test_explicit_gst_rate_in_additive_merge_clears_flag(self):
        items, errors = self.stock_file()
        catalog_import.apply(self.conn, items, errors)
        pid = catalog.list_products(self.conn)[0]["id"]
        items2, errors2 = self.stock_file(tax="5", qty="0")
        catalog_import.apply(self.conn, items2, errors2, mode="merge")
        p = catalog.get_product(self.conn, pid)
        self.assertEqual(p["quantity"], 3)
        self.assertEqual(p["tax_rate"], 5)
        self.assertFalse(any(a["name"] == "GST Review Required" for a in p["attributes"]))

    def test_refund_preview_is_exact_readonly_and_matches_actual(self):
        g = catalog.create_product_group(self.conn, "Mixed basket")
        a = catalog.add_product_to_group(self.conn, g, "SKU-A", None, 1299, 10, 0, 5)
        b = catalog.add_product_to_group(self.conn, g, "SKU-B", None, 499, 10, 0, 18)
        settings.save_settings(self.conn, {"price_tax_mode": "inclusive"})
        original = sales.checkout(self.conn,
            [{"product_id": a, "quantity": 3}, {"product_id": b, "quantity": 2}],
            [{"method": "CASH", "amount": 99999}],
            discount_mode="percent", discount_value=5)
        before_stock = catalog.get_product(self.conn, a)["quantity"]
        before_sales = self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        lines = [{"product_id": a, "quantity": 1}, {"product_id": b, "quantity": 1}]
        preview = sales.preview_refund(self.conn, original["id"], lines)
        self.assertEqual(before_stock, catalog.get_product(self.conn, a)["quantity"])
        self.assertEqual(before_sales, self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0])
        result = sales.refund_sale(self.conn, original["id"], lines=lines)
        self.assertEqual(preview["total"], result["total"])
        self.assertEqual(preview["tax_total"], result["tax_total"])
        self.assertEqual(preview["discount_total"], result["discount_total"])
        self.assertEqual(result["parent_invoice_number"], original["invoice_number"])
        self.assertEqual(result["items"][0]["quantity"], 1)
        second = sales.preview_refund(self.conn, original["id"],
                                      [{"product_id": a, "quantity": 2}])
        result2 = sales.refund_sale(self.conn, original["id"],
                                    lines=[{"product_id": a, "quantity": 2}])
        self.assertEqual(second["total"], result2["total"])

    def test_legacy_non_variant_name_preserved(self):
        self.assertEqual(receipt_parts("Cotton Top (Classic)"), ("Cotton Top (Classic)", ""))
        self.assertEqual(receipt_parts("VH SOCKS (Colour: ASSTD, Source Row: 193)"),
                         ("VH SOCKS", "Colour: ASSTD"))
        self.assertEqual(receipt_parts("Clovia set (Colour: Blue, White, Size: FS, Source Row: 4)"),
                         ("Clovia set", "Colour: Blue, White, Size: FS"))


if __name__ == "__main__":
    unittest.main()
