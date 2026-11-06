"""Concurrency and upgrade checks on disposable file-backed databases."""
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from pos import catalog, sales
from pos.db import connect, init_db


class ConcurrentRefundTests(unittest.TestCase):
    def test_same_request_two_workers_returns_single_refund(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = str(Path(tmp) / 'pos.db')
            init_db(database)
            with connect(database) as conn:
                group = catalog.create_product_group(conn, 'Test')
                pid = catalog.add_product_to_group(conn, group_id=group,
                    SKU='CONCURRENT-1', barcode=None, price=80, quantity=10,
                    cost_price=0, tax_rate=5)
                sold = sales.checkout(conn, items=[{'product_id':pid,'quantity':2}],
                    payments=[{'method':'UPI','amount':168}])
                sale_id = sold['id']
            def run(_):
                conn = connect(database)
                try:
                    result = sales.refund_sale(conn, sale_id,
                         lines=[{'product_id':pid, 'quantity':1}],
                         reason='Duplicate submit test',
                         request_id='concurrent_refund_001')
                    return result['id']
                finally:
                    conn.close()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(run, range(2)))
            self.assertEqual(results[0], results[1])
            with connect(database) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM sales WHERE status="REFUNDED"').fetchone()[0],1)
                self.assertEqual(catalog.get_product(conn,pid)['quantity'],9)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM stock_movements WHERE kind="REFUND"').fetchone()[0],1)
                old_count = conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0]
            init_db(database)  # migration remains repeatable, sale IDs unchanged
            with connect(database) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0],old_count)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM refund_requests').fetchone()[0],1)

if __name__=='__main__':
    unittest.main()
