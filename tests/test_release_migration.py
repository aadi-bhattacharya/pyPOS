"""Upgrade and concurrent retry checks using disposable SQLite files."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import sqlite3
import unittest

from pos.db import connect, init_db, schema_sql
from pos import catalog, sales


class RetailUpgradeTests(unittest.TestCase):
    def test_upgrade_preserves_exact_existing_product_and_sale(self):
        with tempfile.TemporaryDirectory() as work:
            dbfile = Path(work) / 'previous.db'
            conn = connect(dbfile)
            # Emulate the earlier release without session-ID attribution columns.
            legacy_schema = (schema_sql()
                .replace(',\n  cash_session_id INTEGER REFERENCES cash_sessions(id) ON DELETE SET NULL,\n  cash_session_explicit INTEGER NOT NULL DEFAULT 0 CHECK(cash_session_explicit IN (0,1))', '')
                .replace('CREATE INDEX IF NOT EXISTS idx_sales_cash_session ON sales(cash_session_id);', ''))
            conn.executescript(legacy_schema)
            group_id = catalog.create_product_group(conn,'Old inventory')
            pid = catalog.add_product_to_group(conn,group_id,'OLD-123','8901326190401',179.0,5,40.0,5.0)
            # Construct an actual legacy sale row using the pre-upgrade columns.
            cur=conn.execute("INSERT INTO sales(subtotal,tax_total,discount_total,total,status,invoice_number) VALUES(?,?,?,?,?,?)",
                             (358,0,0,358,'COMPLETED','26-27/LEGACY-01'))
            old_sale_id=cur.lastrowid
            conn.execute("INSERT INTO sale_items(sale_id,product_id,quantity,unit_price,description_at_sale,sku_at_sale) VALUES(?,?,?,?,?,?)",
                         (old_sale_id,pid,2,179,'Old inventory','OLD-123'))
            conn.execute("INSERT INTO payments(sale_id,method,amount) VALUES(?,?,?)",(old_sale_id,'CASH',358))
            conn.execute("UPDATE products SET quantity=3 WHERE id=?",(pid,))
            self.assertEqual(conn.execute('SELECT quantity FROM products WHERE id=?',(pid,)).fetchone()[0],3)
            conn.close()
            init_db(dbfile)
            init_db(dbfile)
            conn = connect(dbfile)
            try:
                self.assertEqual(conn.execute('SELECT SKU,barcode,quantity FROM products WHERE id=?',(pid,)).fetchone()[:],
                                 ('OLD-123','8901326190401',3))
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0],1)
                self.assertEqual(sales.get_sale_detail(conn,old_sale_id)['total'],358)
                self.assertEqual(conn.execute('SELECT cash_session_explicit FROM sales WHERE id=?',(old_sale_id,)).fetchone()[0],0)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM stock_movements WHERE kind="OPENING"').fetchone()[0],1)
            finally:
                conn.close()

    def test_concurrent_repeated_checkout_request_writes_once(self):
        with tempfile.TemporaryDirectory() as work:
            dbfile = Path(work) / 'concurrent.db'
            init_db(dbfile)
            conn = connect(dbfile)
            group_id = catalog.create_product_group(conn,'Concurrent inventory')
            pid = catalog.add_product_to_group(conn,group_id,'CON-123',None,200.0,5,100.0,5.0)
            conn.close()
            def checkout_once(_):
                db = connect(dbfile)
                try:
                    return sales.checkout(db,[{'product_id':pid,'quantity':1}],
                        payments=[{'method':'CASH','amount':210}],
                        request_id='same_sale_request_112233')['id']
                finally:
                    db.close()
            with ThreadPoolExecutor(max_workers=8) as pool:
                sale_ids = list(pool.map(checkout_once,range(12)))
            self.assertEqual(len(set(sale_ids)),1)
            conn=connect(dbfile)
            try:
                self.assertEqual(conn.execute('SELECT quantity FROM products WHERE id=?',(pid,)).fetchone()[0],4)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0],1)
            finally:
                conn.close()
