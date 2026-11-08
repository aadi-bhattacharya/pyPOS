"""Financial invariants for the reliability release. All tests use in-memory DBs."""
import random
import unittest
from decimal import Decimal
from base import POSTestCase
from pos import sales, catalog, cash_sessions, catalog_import, operations


class CentAllocationTests(unittest.TestCase):
    def test_known_discount_bug(self):
        result = sales.compute_totals([{"price":.01,"quantity":1,"tax_rate":5}] * 3,
                                       "amount",.02)
        self.assertEqual(sum(r["discount_cents"] for r in result["lines"]),2)
        self.assertTrue(all(r["net_cents"]>=0 for r in result["lines"]))

    def test_20000_fuzz_cases(self):
        rng=random.Random(7062026)
        for _ in range(20000):
            gross=[rng.randint(0,50) for _ in range(rng.randint(1,12))]
            discount=rng.randint(0,sum(gross)+20)
            result=sales.compute_totals([{"price":Decimal(c)/100,"tax_rate":rng.choice([0,5,18]),"quantity":1} for c in gross],
                                       "amount",Decimal(discount)/100)
            self.assertEqual(sum(x["discount_cents"] for x in result["lines"]),min(sum(gross),discount))
            self.assertTrue(all(0<=x["discount_cents"]<=x["gross_cents"] for x in result["lines"]))


class AccountingSafetyTests(POSTestCase):
    def test_idempotent_checkout_and_reuse_rejected(self):
        pid=self.mkproduct(quantity=3,price=100,tax_rate=0)
        args=dict(items=[{"product_id":pid,"quantity":1}],payments=[{"method":"CASH","amount":100}],
                  request_id="request_123456789")
        first=sales.checkout(self.conn,**args)
        second=sales.checkout(self.conn,**args)
        self.assertEqual(first["id"],second["id"])
        self.assertEqual(catalog.get_product(self.conn,pid)["quantity"],2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0],1)
        with self.assertRaises(sales.InvalidSaleError):
            sales.checkout(self.conn,**{**args,"items":[{"product_id":pid,"quantity":2}]})

    def test_refund_linked_to_new_cash_session_not_timestamps(self):
        pid=self.mkproduct(quantity=10,price=100,tax_rate=0)
        first=cash_sessions.open_session(self.conn,100)
        r=sales.checkout(self.conn,[{"product_id":pid,"quantity":1}],payments=[{"method":"CASH","amount":100}])
        cash_sessions.close_session(self.conn,200)
        second=cash_sessions.open_session(self.conn,50)
        rep_first=cash_sessions.session_report(self.conn,first["session"]["id"])
        rep_second=cash_sessions.session_report(self.conn,second["session"]["id"])
        self.assertEqual(rep_first["cash_in"],100)
        self.assertEqual(rep_second["cash_in"],0)
        refund=sales.refund_sale(self.conn,r["id"])
        self.assertEqual(cash_sessions.session_report(self.conn,second["session"]["id"])["cash_out"],100)
        self.assertEqual(self.conn.execute("SELECT cash_session_id FROM sales WHERE id=?",(refund["id"],)).fetchone()[0],second["session"]["id"])

    def test_cash_expense_and_deposit_in_close_out(self):
        s=cash_sessions.open_session(self.conn,300)
        cash_sessions.record_cash_movement(self.conn,"PETTY_CASH",20,"Stationery","PV-14")
        cash_sessions.record_cash_movement(self.conn,"BANK_DEPOSIT",100,"Bank deposit","DEP-17")
        cash_sessions.record_cash_movement(self.conn,"CASH_IN",50,"Owner added float")
        report=cash_sessions.session_report(self.conn,s["session"]["id"])
        self.assertEqual(report["expected_cash"],230)
        self.assertEqual(len(report["cash_movements"]),3)
        self.assertEqual(cash_sessions.close_session(self.conn,230)["report"]["difference"],0)

    def test_unverified_gst_blocked_even_without_gstin(self):
        pid=self.mkproduct(quantity=1,tax_rate=0)
        self.conn.execute("INSERT INTO product_attributes(product_id,attribute_name,attribute_value) VALUES(?,?,?)",
                          (pid,"GST Review Required","unknown"))
        with self.assertRaisesRegex(sales.InvalidSaleError,"GST rate unverified"):
            sales.checkout(self.conn,[{"product_id":pid,"quantity":1}],payments=[{"method":"CASH","amount":11}])

    def test_supplier_invoice_atomic_receipt_and_payment(self):
        a=self.mkproduct(SKU="APR-1",quantity=3,price=100)
        b=self.mkproduct(SKU="APR-2",quantity=1,price=200)
        supplier=operations.new_supplier(self.conn,"Bill supplier")
        purchase=operations.receive_purchase(self.conn,supplier["id"],"INV-201",[
            {"product_id":a,"quantity":2,"unit_cost":"40.00"},
            {"product_id":b,"quantity":4,"unit_cost":"67.50"}])
        self.assertEqual(purchase["total_cents"],35000)
        self.assertEqual(catalog.get_product(self.conn,a)["quantity"],5)
        with self.assertRaises(operations.OperationError):
            operations.receive_purchase(self.conn,supplier["id"],"INV-201",[
                {"product_id":a,"quantity":7,"unit_cost":"40.00"}])
        self.assertEqual(catalog.get_product(self.conn,a)["quantity"],5)
        with self.assertRaises(operations.OperationError):
            operations.pay_supplier(self.conn,purchase["id"],"100","CASH")
        cash_sessions.open_session(self.conn,200)
        operations.pay_supplier(self.conn,purchase["id"],"100","CASH",request_id="supplierpay_778")
        operations.pay_supplier(self.conn,purchase["id"],"100","CASH",request_id="supplierpay_778")
        rec=operations.purchase_detail(self.conn,purchase["id"])
        self.assertEqual(rec["paid_cents"],10000)
        self.assertEqual(cash_sessions.current_session(self.conn)["id"],1)
        self.assertEqual(cash_sessions.session_report(self.conn,1)["expected_cash"],100)

    def test_physical_stocktake_is_explicit_and_stale_safe(self):
        a=self.mkproduct(SKU="FIRST",quantity=8)
        b=self.mkproduct(SKU="SECOND",quantity=4)
        st=operations.start_stocktake(self.conn,"Shelf A")
        operations.count_stock(self.conn,st["id"],a,5)
        self.assertEqual(catalog.get_product(self.conn,a)["quantity"],8)
        self.conn.execute("UPDATE products SET quantity=9 WHERE id=?",(a,))
        with self.assertRaisesRegex(operations.OperationError,"Stock changed"):
            operations.post_stocktake(self.conn,st["id"])
        self.assertEqual(catalog.get_product(self.conn,a)["quantity"],9)
        # A new draft captures the latest count; uncounted second SKU stays intact.
        st2=operations.start_stocktake(self.conn,"Fresh count")
        operations.count_stock(self.conn,st2["id"],a,5)
        operations.post_stocktake(self.conn,st2["id"], adjustment_reason="Verified shelf count")
        self.assertEqual(catalog.get_product(self.conn,a)["quantity"],5)
        self.assertEqual(catalog.get_product(self.conn,b)["quantity"],4)
        with self.assertRaises(operations.OperationError):
            operations.post_stocktake(self.conn,st2["id"])

    def test_add_stock_same_file_rejected(self):
        header="sku,product_name,barcode,price,quantity,cost_price,tax_rate\n"
        raw=(header+"NEW,New item,,100,2,50,5\n").encode()
        items,issues=catalog_import.parse(raw,"stock.csv")
        catalog_import.apply(self.conn,items,issues,"add_stock")
        with self.assertRaisesRegex(catalog_import.ImportError,"already received"):
            catalog_import.apply(self.conn,items,issues,"add_stock")
        quantity=self.conn.execute("SELECT quantity FROM products WHERE SKU='NEW'").fetchone()[0]
        self.assertEqual(quantity,2)
        result=catalog_import.apply(self.conn,items,issues,"add_stock",force_repeat=True,
                                     repeat_reason="Genuine second shipment of same SKU")
        self.assertEqual(result["stock_added"],2)
        self.assertEqual(self.conn.execute("SELECT quantity FROM products WHERE SKU='NEW'").fetchone()[0],4)

if __name__=="__main__": unittest.main()

class ClosedDaySafetyTests(POSTestCase):
    def test_void_old_day_blocked_but_refund_allowed(self):
        pid=self.mkproduct(price=50,quantity=5,tax_rate=0)
        cash_sessions.open_session(self.conn,100)
        r=sales.checkout(self.conn,[{"product_id":pid,"quantity":1}],payments=[{"method":"CASH","amount":50}])
        cash_sessions.close_session(self.conn,150)
        with self.assertRaises(sales.AlreadyProcessedError):
            sales.void_sale(self.conn,r['id'])
        self.assertEqual(catalog.get_product(self.conn,pid)['quantity'],4)
        cash_sessions.open_session(self.conn,100)
        refund=sales.refund_sale(self.conn,r['id'])
        self.assertEqual(refund['status'],'REFUNDED')
        self.assertEqual(catalog.get_product(self.conn,pid)['quantity'],5)


class CashSessionAttributionTests(POSTestCase):
    def test_new_sale_outside_session_not_attributed_by_same_second_timestamp(self):
        pid=self.mkproduct(SKU="SESSION-OPEN",quantity=4,price=30,tax_rate=0)
        outside=sales.checkout(self.conn,[{"product_id":pid,"quantity":1}],
            payments=[{"method":"CASH","amount":30}])
        row=self.conn.execute("SELECT cash_session_id,cash_session_explicit FROM sales WHERE id=?",(outside["id"],)).fetchone()
        self.assertEqual(tuple(row),(None,1))
        day=cash_sessions.open_session(self.conn,0)
        inside=sales.checkout(self.conn,[{"product_id":pid,"quantity":1}],
            payments=[{"method":"CASH","amount":30}])
        self.assertEqual(cash_sessions.session_report(self.conn,day["session"]["id"])["cash_in"],30)
        self.assertEqual(self.conn.execute("SELECT cash_session_id,cash_session_explicit FROM sales WHERE id=?",(inside["id"],)).fetchone()[1],1)
