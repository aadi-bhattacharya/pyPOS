import unittest

from base import POSTestCase
from pos import catalog, sales, settings as settings_mod


def enable(conn, **over):
    values = {
        "tax_breakup": "true",
        "tax_comp1_enabled": "true", "tax_comp1_name": "CGST", "tax_comp1_share": "50",
        "tax_comp2_enabled": "true", "tax_comp2_name": "SGST", "tax_comp2_share": "50",
    }
    values.update(over)
    settings_mod.save_settings(conn, values)


class TaxBreakupTests(POSTestCase):
    def setUp(self):
        super().setUp()
        self.pid5 = self.mkproduct(SKU="R5", price=100.0, tax_rate=5, quantity=50)
        self.pid12 = self.mkproduct(SKU="R12", price=50.0, tax_rate=12, quantity=50)

    def test_disabled_by_default(self):
        r = sales.checkout(self.conn,
                           items=[{"product_id": self.pid5, "quantity": 2}],
                           payments=[{"method": "CASH", "amount": 500}])
        self.assertEqual(r["tax_breakup"], [])

    def test_gst_split_sums_exactly_to_tax_total(self):
        enable(self.conn)
        r = sales.checkout(self.conn,
                           items=[{"product_id": self.pid5, "quantity": 2}],
                           payments=[{"method": "CASH", "amount": 500}])
        self.assertAlmostEqual(r["tax_total"], 10.0)
        self.assertEqual(
            [(b["name"], b["rate"], b["amount"]) for b in r["tax_breakup"]],
            [("CGST", 2.5, 5.0), ("SGST", 2.5, 5.0)])
        self.assertAlmostEqual(
            sum(b["amount"] for b in r["tax_breakup"]), r["tax_total"])

    def test_single_component_igst(self):
        enable(self.conn, tax_comp2_enabled="false",
               tax_comp1_name="IGST", tax_comp1_share="100")
        r = sales.checkout(self.conn,
                           items=[{"product_id": self.pid5, "quantity": 1}],
                           payments=[{"method": "CASH", "amount": 500}])
        self.assertEqual(r["tax_breakup"],
                         [{"name": "IGST", "rate": 5.0, "amount": 5.0}])

    def test_multi_rate_grouped_per_rate(self):
        enable(self.conn)
        r = sales.checkout(self.conn,
                           items=[{"product_id": self.pid5, "quantity": 1},
                                  {"product_id": self.pid12, "quantity": 1}],
                           payments=[{"method": "CASH", "amount": 500}])
        self.assertEqual([(b["name"], b["rate"]) for b in r["tax_breakup"]],
                         [("CGST", 2.5), ("SGST", 2.5), ("CGST", 6.0), ("SGST", 6.0)])
        self.assertAlmostEqual(
            sum(b["amount"] for b in r["tax_breakup"]), r["tax_total"])

    def test_uneven_split_keeps_cent_exactness(self):
        # 3 components worth of share on a 2-slot config: 33.33/66.67 split
        # of an odd tax total must still sum to the exact tax cents.
        enable(self.conn, tax_comp1_share="33.33", tax_comp2_share="66.67")
        self.mkproduct(SKU="ODD", price=3.33, tax_rate=5, quantity=10)
        odd = [p for p in catalog.list_products(self.conn) if p["SKU"] == "ODD"][0]
        r = sales.checkout(self.conn,
                           items=[{"product_id": odd["id"], "quantity": 1}],
                           payments=[{"method": "CASH", "amount": 100}])
        self.assertAlmostEqual(
            sum(b["amount"] for b in r["tax_breakup"]), r["tax_total"])

    def test_refund_receipt_carries_breakup(self):
        enable(self.conn)
        r = sales.checkout(self.conn,
                           items=[{"product_id": self.pid5, "quantity": 4}],
                           payments=[{"method": "CASH", "amount": 500}])
        refund = sales.refund_sale(
            self.conn, r["id"], lines=[{"product_id": self.pid5, "quantity": 1}])
        self.assertEqual(len(refund["tax_breakup"]), 2)
        self.assertAlmostEqual(
            sum(b["amount"] for b in refund["tax_breakup"]), refund["tax_total"])

    def test_all_components_disabled_means_no_breakup(self):
        enable(self.conn, tax_comp1_enabled="false", tax_comp2_enabled="false")
        r = sales.checkout(self.conn,
                           items=[{"product_id": self.pid5, "quantity": 1}],
                           payments=[{"method": "CASH", "amount": 500}])
        self.assertEqual(r["tax_breakup"], [])
        self.assertAlmostEqual(r["tax_total"], 5.0)  # tax itself unchanged

    def test_settings_roundtrip_defaults(self):
        s = settings_mod.get_settings(self.conn)
        self.assertEqual(s["tax_breakup"], "false")
        self.assertEqual(s["tax_comp1_name"], "CGST")
        self.assertEqual(s["tax_comp2_share"], "50")


if __name__ == "__main__":
    unittest.main()
