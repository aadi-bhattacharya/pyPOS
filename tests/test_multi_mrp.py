"""One EAN, independently counted MRPs: non-destructive import, till and refunds."""
import csv
import io
from decimal import Decimal

from base import POSTestCase
from pos import catalog, catalog_import, sales, settings, db


def upload(*rows):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['sku','product_name','barcode','price','quantity','cost_price','tax_rate',
                'attr:Colour','attr:Size'])
    w.writerows(rows)
    return catalog_import.parse(out.getvalue().encode(), 'prices.csv')


class MultiMRPTests(POSTestCase):
    def load(self):
        items, errors = upload(
            ('EAN1-P399','UL04','8901326156094','399','2','','5','ICRSH','M'),
            ('EAN1-P429','UL04','8901326156094','429','3','','5','ICRSH','M'),
            ('EAN1-P449','UL04','8901326156094','449','1','','5','ICRSH','M'))
        self.assertEqual(errors, [])
        self.assertTrue(catalog_import.preview(self.conn,items,errors)['can_import'])
        result = catalog_import.apply(self.conn,items,errors)
        self.assertEqual(result['created'],3)
        return items,errors

    def test_each_price_has_its_own_stock_and_exact_sku_lookup(self):
        self.load()
        match = catalog.find_by_code(self.conn,'8901326156094')
        self.assertEqual([v['price'] for v in match['matches']], [399,429,449])
        self.assertEqual([v['quantity'] for v in match['matches']], [2,3,1])
        self.assertEqual([v['barcode'] for v in match['matches']], ['8901326156094']*3)
        self.assertEqual(catalog.find_by_code(self.conn,'EAN1-P429')['price'],429)
        self.assertEqual(len(catalog.list_products(self.conn,query='8901326156094')),3)

    def test_exact_mrp_sale_and_partial_refund_restores_only_one_edition(self):
        self.load()
        self.conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('price_tax_mode','inclusive')")
        options = catalog.find_by_code(self.conn,'8901326156094')['matches']
        old,new,high = options
        sale = sales.checkout(self.conn,[{'product_id':new['id'],'quantity':2}],
                              payments=[{'method':'CASH','amount':858}])
        self.assertEqual(sale['total'],858)
        self.assertEqual(sale['items'][0]['unit_price'],429)
        self.assertEqual(catalog.get_product(self.conn,new['id'])['quantity'],1)
        self.assertEqual(catalog.get_product(self.conn,old['id'])['quantity'],2)
        self.assertEqual(catalog.get_product(self.conn,high['id'])['quantity'],1)
        ref = sales.refund_sale(self.conn,sale['id'],
                                lines=[{'product_id':new['id'],'quantity':1}],
                                request_id='mrp-test-refund-1')
        self.assertEqual(ref['total'],429)
        self.assertEqual(catalog.get_product(self.conn,new['id'])['quantity'],2)
        self.assertEqual(catalog.get_product(self.conn,old['id'])['quantity'],2)
        self.assertEqual(sales.refund_sale(self.conn,sale['id'],
                                         lines=[{'product_id':new['id'],'quantity':1}],
                                         request_id='mrp-test-refund-1')['id'],ref['id'])
        self.assertEqual(catalog.get_product(self.conn,new['id'])['quantity'],2)

    def test_skip_repeat_does_not_add_stock(self):
        items,errors=self.load()
        counts=catalog_import.preview(self.conn,items,errors)
        self.assertEqual(counts['existing'],3)
        self.assertEqual(counts['new'],0)
        self.assertEqual(catalog_import.apply(self.conn,items,errors)['skipped'],3)
        self.assertEqual(sum(v['quantity'] for v in catalog.list_products(self.conn)),6)

    def test_same_ean_different_size_is_rejected_transactionally(self):
        items,errs=upload(('A','UL04','1234567890123','399','1','','5','BLACK','S'),
                           ('B','UL04','1234567890123','499','1','','5','BLACK','M'))
        self.assertFalse(catalog_import.preview(self.conn,items,errs)['can_import'])
        with self.assertRaises(catalog_import.ImportError):
            catalog_import.apply(self.conn,items,errs)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM products').fetchone()[0],0)

    def test_manual_add_and_edit_still_possible_with_duplicate_ean(self):
        gid=catalog.create_product_group(self.conn,'Style X')
        pid1=catalog.add_product_to_group(self.conn,gid,'P1','1234',100,3,0,5)
        pid2=catalog.add_product_to_group(self.conn,gid,'P2','1234',120,2,0,5)
        self.assertEqual(len(catalog.find_by_code(self.conn,'1234')['matches']),2)
        catalog.update_product(self.conn,pid2,price=125)
        self.assertEqual(catalog.get_product(self.conn,pid2)['price'],125)
        catalog.update_product(self.conn,pid2,barcode='5678')
        self.assertEqual(catalog.find_by_code(self.conn,'5678')['id'],pid2)
        self.assertEqual(catalog.find_by_code(self.conn,'1234')['id'],pid1)
        with self.assertRaises(catalog.DuplicateBarcodeError):
            catalog.add_product_to_group(self.conn,gid,'P3','1234',100,1,0,5)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM products').fetchone()[0],2)

    def test_existing_id_and_invoice_data_survives_schema_upgrade(self):
        gid=catalog.create_product_group(self.conn,'Legacy')
        pid=catalog.add_product_to_group(self.conn,gid,'LEG','990011',100,2,0,5)
        sale=sales.checkout(self.conn,[{'product_id':pid,'quantity':1}],
                            payments=[{'method':'CASH','amount':105}])
        before=(sale['invoice_number'],sale['total'])
        # simulate the repeatable upgrade step without altering invoice rows
        db.migrate(self.conn)
        self.assertEqual(catalog.get_product(self.conn,pid)['quantity'],1)
        saved=sales.get_sale_detail(self.conn,sale['id'])
        self.assertEqual((saved['invoice_number'],saved['total']),before)

class ExistingEANUpgradeTests(POSTestCase):
    def test_new_price_edition_adds_to_legacy_product_without_changing_it(self):
        gid=catalog.create_product_group(self.conn,'MV20')
        pid=catalog.add_product_to_group(self.conn,gid,'EAN-8901326366189',
                                        '8901326366189',1599,4,0,5)
        catalog.set_product_attribute(self.conn,pid,'Colour','BLACK')
        catalog.set_product_attribute(self.conn,pid,'Size','S')
        items,errs=upload(('EAN-8901326366189-MRP148900','MV20',
                           '8901326366189','1489','1','','5','BLACK','S'))
        self.assertTrue(catalog_import.preview(self.conn,items,errs)['can_import'])
        result=catalog_import.apply(self.conn,items,errs)
        self.assertEqual(result['created'],1)
        options=catalog.find_by_code(self.conn,'8901326366189')['matches']
        self.assertEqual([(o['price'],o['quantity']) for o in options],[(1489,1),(1599,4)])
        self.assertEqual(catalog.get_product(self.conn,pid)['quantity'],4)
        self.assertEqual(catalog.get_product(self.conn,pid)['price'],1599)
        self.assertEqual(catalog_import.apply(self.conn,items,errs)['skipped'],1)
        self.assertEqual(catalog.get_product(self.conn,pid)['quantity'],4)

    def test_upgrade_recreates_only_missing_price_options_table(self):
        import tempfile,os
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,'legacy.db')
            db.init_db(path)
            conn=db.connect(path)
            gid=catalog.create_product_group(conn,'Unchanged')
            pid=catalog.add_product_to_group(conn,gid,'LEG','54321',100,2,0,5)
            sale=sales.checkout(conn,[{'product_id':pid,'quantity':1}],
                                payments=[{'method':'CASH','amount':105}])
            conn.execute('DROP TABLE barcode_price_options')
            conn.close()
            db.init_db(path)
            conn=db.connect(path)
            self.assertEqual(catalog.get_product(conn,pid)['quantity'],1)
            self.assertEqual(sales.get_sale_detail(conn,sale['id'])['total'],105)
            self.assertEqual(conn.execute('SELECT count(*) FROM barcode_price_options').fetchone()[0],0)
            conn.close()
