import unittest

from base import POSTestCase

from pos import catalog


class CatalogTests(POSTestCase):
    def test_create_and_rename_group(self):
        gid = self.mkgroup("Drinks")
        catalog.rename_product_group(self.conn, gid, "Beverages")
        names = [g["name"] for g in catalog.list_groups(self.conn)]
        self.assertEqual(names, ["Beverages"])

    def test_group_name_validation(self):
        with self.assertRaises(catalog.InvalidInputError):
            catalog.create_product_group(self.conn, "   ")
        gid = self.mkgroup()
        with self.assertRaises(catalog.InvalidInputError):
            catalog.rename_product_group(self.conn, gid, "")

    def test_delete_group_with_products_blocked(self):
        gid = self.mkgroup()
        self.mkproduct(group_id=gid)
        with self.assertRaises(catalog.GroupNotEmptyError):
            catalog.delete_product_group(self.conn, gid)

    def test_delete_missing_group(self):
        with self.assertRaises(catalog.GroupNotFoundError):
            catalog.delete_product_group(self.conn, 999)

    def test_add_product_defaults(self):
        pid = self.mkproduct(quantity=7)
        p = catalog.get_product(self.conn, pid)
        self.assertEqual(p["quantity"], 7)
        self.assertEqual(p["price"], 10.0)
        self.assertEqual(p["tax_rate"], 10)
        self.assertEqual(p["attributes"], [])

    def test_duplicate_sku(self):
        self.mkproduct(SKU="X1")
        with self.assertRaises(catalog.DuplicateSKUError):
            self.mkproduct(SKU="X1")

    def test_duplicate_barcode(self):
        self.mkproduct(barcode="12345")
        with self.assertRaises(catalog.DuplicateBarcodeError):
            self.mkproduct(SKU="OTHER", barcode="12345")

    def test_invalid_inputs(self):
        gid = self.mkgroup()
        with self.assertRaises(catalog.InvalidInputError):
            self.mkproduct(group_id=gid, SKU="  ")
        with self.assertRaises(catalog.InvalidInputError):
            self.mkproduct(group_id=gid, price=-1)
        with self.assertRaises(catalog.InvalidInputError):
            self.mkproduct(group_id=gid, quantity=-3)
        with self.assertRaises(catalog.InvalidInputError):
            self.mkproduct(group_id=gid, tax_rate=-2)

    def test_update_product_fields(self):
        pid = self.mkproduct()
        catalog.update_product(self.conn, pid, price=12.5, tax_rate=5,
                               barcode="999", SKU="SKU-1B")
        p = catalog.get_product(self.conn, pid)
        self.assertEqual(p["price"], 12.5)
        self.assertEqual(p["tax_rate"], 5)
        self.assertEqual(p["barcode"], "999")

    def test_update_product_rejects_bad_field_and_values(self):
        pid = self.mkproduct()
        with self.assertRaises(catalog.InvalidInputError):
            catalog.update_product(self.conn, pid, quantity=99)  # use stock endpoint
        with self.assertRaises(catalog.InvalidInputError):
            catalog.update_product(self.conn, pid, price=-5)
        with self.assertRaises(catalog.InvalidInputError):
            catalog.update_product(self.conn, pid, SKU=" ")

    def test_update_sku_conflict(self):
        self.mkproduct(SKU="A")
        pid = self.mkproduct(SKU="B")
        with self.assertRaises(catalog.DuplicateSKUError):
            catalog.update_product(self.conn, pid, SKU="A")

    def test_attributes_upsert_remove(self):
        pid = self.mkproduct()
        catalog.set_product_attribute(self.conn, pid, "Size", "M")
        catalog.set_product_attribute(self.conn, pid, "Color", "Red")
        catalog.set_product_attribute(self.conn, pid, "Size", "L")  # upsert
        p = catalog.get_product(self.conn, pid)
        attrs = {a["name"]: a["value"] for a in p["attributes"]}
        self.assertEqual(attrs, {"Size": "L", "Color": "Red"})
        catalog.remove_product_attribute(self.conn, pid, "Color")
        with self.assertRaises(catalog.AttributeNotFoundError):
            catalog.remove_product_attribute(self.conn, pid, "Color")
        with self.assertRaises(catalog.InvalidInputError):
            catalog.set_product_attribute(self.conn, pid, "", "x")

    def test_adjust_stock(self):
        pid = self.mkproduct(quantity=10)
        self.assertEqual(catalog.adjust_stock(self.conn, pid, -4), 6)
        self.assertEqual(catalog.adjust_stock(self.conn, pid, +2), 8)
        with self.assertRaises(catalog.InvalidInputError):
            catalog.adjust_stock(self.conn, pid, -100)
        with self.assertRaises(catalog.ProductNotFoundError):
            catalog.adjust_stock(self.conn, 999, 1)

    def test_delete_product_without_sales(self):
        pid = self.mkproduct()
        catalog.delete_product(self.conn, pid)
        self.assertIsNone(catalog.get_product(self.conn, pid))
        with self.assertRaises(catalog.ProductNotFoundError):
            catalog.delete_product(self.conn, pid)

    def test_list_products_search(self):
        gid = self.mkgroup("Drinks")
        self.mkproduct(group_id=gid, SKU="COLA", barcode="555")
        catalog.set_product_attribute(self.conn, 1, "Flavor", "Cherry")
        by_name = catalog.list_products(self.conn, query="drinks")  # group name
        by_attr = catalog.list_products(self.conn, query="cherry")
        by_sku = catalog.list_products(self.conn, query="col")
        self.assertTrue(by_name and by_attr and by_sku)

    def test_find_by_code(self):
        pid = self.mkproduct(SKU="TEA", barcode="424242")
        self.assertEqual(catalog.find_by_code(self.conn, "424242")["id"], pid)
        self.assertEqual(catalog.find_by_code(self.conn, "TEA")["id"], pid)
        self.assertIsNone(catalog.find_by_code(self.conn, "nope"))


if __name__ == "__main__":
    unittest.main()
