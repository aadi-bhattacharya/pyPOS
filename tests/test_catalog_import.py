"""Regression checks for additive, non-destructive catalog imports."""
import csv
import io
import unittest

from base import POSTestCase
from pos import catalog, catalog_import

HEADERS = ("sku", "product_name", "barcode", "price", "quantity", "cost_price",
           "tax_rate", "hsn_code", "unit_code", "attr:Size", "attr:Colour")


def csv_file(*lines):
    s = io.StringIO()
    writer = csv.writer(s)
    writer.writerow(HEADERS)
    writer.writerows(lines)
    return s.getvalue().encode('utf-8')


class ImportTests(POSTestCase):
    def parse(self, *lines):
        return catalog_import.parse(csv_file(*lines), 'catalog.csv')

    def test_new_inventory_includes_editable_attributes(self):
        items, issues = self.parse(("T1", "T Shirts", "", "999", "4", "", "12", "6109", "NOS", "M", "Blue"))
        p = catalog_import.preview(self.conn, items, issues)
        self.assertEqual((p['new'], p['existing'], p['missing_prices']), (1, 0, 0))
        self.assertTrue(p['can_import'])
        self.assertEqual(catalog_import.apply(self.conn, items, issues)['created'], 1)
        product = catalog.find_by_code(self.conn, "T1")
        self.assertEqual(product['quantity'], 4)
        self.assertEqual(product['hsn_code'], '6109')
        self.assertEqual(product['tax_rate'], 12)
        catalog.set_product_attribute(self.conn, product['id'], 'Size', 'L')
        attrs = {x['name']: x['value'] for x in catalog.get_product(self.conn, product['id'])['attributes']}
        self.assertEqual(attrs['Size'], 'L')
        self.assertEqual(attrs['Colour'], 'Blue')
        self.assertIn('Cost Review Required', attrs)

    def test_default_skips_existing_without_changing_anything(self):
        gid = self.mkgroup('Already')
        pid = catalog.add_product_to_group(self.conn, gid, 'SKU1', None, 111, 8, 23, 18)
        catalog.set_product_attribute(self.conn, pid, 'Colour', 'Original')
        items, issues = self.parse(('SKU1', 'New name', '', '500', '7', '', '0', '', 'NOS', 'XL', 'Other'))
        preview = catalog_import.preview(self.conn, items, issues)
        self.assertEqual(preview['existing'], 1)
        self.assertEqual(preview['skipped'], 1)
        result = catalog_import.apply(self.conn, items, issues)
        self.assertEqual(result['skipped'], 1)
        p = catalog.get_product(self.conn, pid)
        self.assertEqual((p['price'], p['quantity'], p['group_name']), (111, 8, 'Already'))
        attrs = {x['name']: x['value'] for x in p['attributes']}
        self.assertEqual(attrs, {'Colour': 'Original'})

    def test_add_stock_is_additive_and_preserves_existing_fields(self):
        gid = self.mkgroup('Already')
        pid = catalog.add_product_to_group(self.conn, gid, 'S1', None, 100, 6, 40, 5)
        items, issues = self.parse(('S1', 'Other', '', '250', '3', '', '', '', '', 'L', 'Navy'))
        p = catalog_import.preview(self.conn, items, issues, mode='add_stock')
        self.assertEqual(p['stock_to_add'], 3)
        result = catalog_import.apply(self.conn, items, issues, mode='add_stock')
        self.assertEqual(result['stock_added'], 3)
        product = catalog.get_product(self.conn, pid)
        self.assertEqual(product['quantity'], 9)
        self.assertEqual(product['price'], 100)
        self.assertEqual(product['tax_rate'], 5)
        self.assertEqual(product['attributes'], [])

    def test_merge_only_supplied_values(self):
        gid = self.mkgroup('Existing')
        pid = catalog.add_product_to_group(self.conn, gid, 'S1', None, 100, 2, 35, 12)
        items, issues = self.parse(('S1', 'Existing', '', '250', '1', '', '', '', '', 'XL', 'Red'))
        result = catalog_import.apply(self.conn, items, issues, mode='merge')
        product = catalog.get_product(self.conn, pid)
        self.assertEqual(result['updated'], 1)
        self.assertEqual(product['price'], 250)
        self.assertEqual(product['cost_price'], 35)
        self.assertEqual(product['tax_rate'], 12)
        self.assertEqual(product['quantity'], 3)
        self.assertEqual({x['name']: x['value'] for x in product['attributes']},
                         {'Size': 'XL', 'Colour': 'Red'})

    def test_missing_price_blocks_unless_explicit_opt_in(self):
        items, issues = self.parse(('NO_PRICE', 'November stock', '', '', '9', '', '', '', '', 'M', 'Black'))
        self.assertFalse(catalog_import.preview(self.conn, items, issues)['can_import'])
        with self.assertRaises(catalog_import.ImportError):
            catalog_import.apply(self.conn, items, issues)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM products').fetchone()[0], 0)
        self.assertTrue(catalog_import.preview(self.conn, items, issues, allow_zero_prices=True)['can_import'])
        result = catalog_import.apply(self.conn, items, issues, allow_zero_prices=True)
        self.assertEqual(result['no_price_created'], 1)
        product = catalog.find_by_code(self.conn, 'NO_PRICE')
        self.assertEqual(product['price'], 0)
        attrs = {x['name']: x['value'] for x in product['attributes']}
        self.assertIn('Price Review Required', attrs)
        self.assertIn('GST Review Required', attrs)
        catalog.update_product(self.conn, product['id'], price=799, tax_rate=5)
        catalog.remove_product_attribute(self.conn, product['id'], 'Price Review Required')

    def test_duplicate_skus_in_file_never_partially_write(self):
        items, errors = self.parse(('S1', 'Clothes', '', '100', '1', '', '5', '', '', 'L', 'Red'),
                                   ('S1', 'Clothes', '', '150', '3', '', '5', '', '', 'M', 'Red'))
        self.assertEqual(len(errors), 1)
        self.assertFalse(catalog_import.preview(self.conn, items, errors)['can_import'])
        with self.assertRaises(catalog_import.ImportError):
            catalog_import.apply(self.conn, items, errors)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM products').fetchone()[0], 0)

    def test_barcode_collision_rolls_back_whole_batch(self):
        items, errors = self.parse(('S1', 'G', '111', '200', '1', '', '5', '', '', 'L', 'Red'),
                                   ('S2', 'G', '111', '200', '1', '', '5', '', '', 'M', 'Red'))
        with self.assertRaises(Exception):
            catalog_import.apply(self.conn, items, errors)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM products').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM product_groups').fetchone()[0], 0)

    def test_rejects_bad_numeric_and_unknown_header(self):
        items, errors = self.parse(('S1', 'Shirts', '', 'a lot', '3', '', '', '', '', 'S', 'Red'))
        self.assertEqual(len(errors), 1)
        bad = b'sku,product_name,price,quantity,strange\nX,Name,20,2,oops\n'
        with self.assertRaises(catalog_import.ImportError):
            catalog_import.parse(bad, 'c.csv')

    def test_large_catalog_preserves_editable_attrs(self):
        lines = [(f"B{i:04}", "Batch Import", "", "100", "1", "", "5", "", "NOS", "S", "Black")
                 for i in range(1105)]
        items, issues = self.parse(*lines)
        r = catalog_import.apply(self.conn, items, issues)
        self.assertEqual(r["created"], 1105)
        products = catalog.list_products(self.conn)
        self.assertEqual(len(products), 1105)
        self.assertEqual(len(products[0]["attributes"]), 3)  # includes Cost Review Required
        catalog.set_product_attribute(self.conn, products[-1]["id"], "Colour", "Navy")
        self.assertEqual({a["name"]: a["value"] for a in catalog.get_product(
            self.conn, products[-1]["id"])["attributes"]}["Colour"], "Navy")

    def test_accepts_packaged_excel_template(self):
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / 'pos/static/templates/pyPOS_Import_Template.xlsx'
        with self.assertRaisesRegex(catalog_import.ImportError, 'No product rows found'):
            catalog_import.parse(path.read_bytes(), 'template.xlsx')


if __name__ == '__main__':
    unittest.main()
