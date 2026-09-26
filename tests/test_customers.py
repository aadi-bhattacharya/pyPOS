import unittest

from base import POSTestCase
from pos import catalog, sales, customers


class CustomerCRUDTests(POSTestCase):
    def test_create_and_get(self):
        c = customers.create_customer(self.conn, "Asha Verma", phone="555-0100",
                                      email="asha@example.com")
        self.assertEqual(c["name"], "Asha Verma")
        self.assertEqual(c["store_credit"], 0)
        got = customers.get_customer(self.conn, c["id"])
        self.assertEqual(got["phone"], "555-0100")

    def test_name_required(self):
        with self.assertRaises(catalog.InvalidInputError):
            customers.create_customer(self.conn, "   ")

    def test_duplicate_phone_rejected(self):
        customers.create_customer(self.conn, "One", phone="123")
        from pos.customers import DuplicatePhoneError
        with self.assertRaises(DuplicatePhoneError):
            customers.create_customer(self.conn, "Two", phone="123")

    def test_update_phone_uniqueness(self):
        a = customers.create_customer(self.conn, "A", phone="1")
        b = customers.create_customer(self.conn, "B", phone="2")
        from pos.customers import DuplicatePhoneError
        with self.assertRaises(DuplicatePhoneError):
            customers.update_customer(self.conn, b["id"], phone=a["phone"])
        updated = customers.update_customer(self.conn, b["id"], name="Bee")
        self.assertEqual(updated["name"], "Bee")

    def test_delete_with_credit_blocked(self):
        c = customers.create_customer(self.conn, "Creditor")
        customers.adjust_credit(self.conn, c["id"], 100, "gift")
        from pos.customers import CustomerHasCreditError
        with self.assertRaises(CustomerHasCreditError):
            customers.delete_customer(self.conn, c["id"])
        customers.adjust_credit(self.conn, c["id"], -100, "clearing")
        customers.delete_customer(self.conn, c["id"])
        from pos.customers import CustomerNotFoundError
        with self.assertRaises(CustomerNotFoundError):
            customers.get_customer(self.conn, c["id"])

    def test_list_search(self):
        customers.create_customer(self.conn, "Zara", phone="900")
        customers.create_customer(self.conn, "Liam", email="liam@x.io")
        names = [c["name"] for c in customers.list_customers(self.conn)]
        self.assertEqual(sorted(names), ["Liam", "Zara"])
        found = customers.list_customers(self.conn, query="900")
        self.assertEqual([c["name"] for c in found], ["Zara"])


class StoreCreditTests(POSTestCase):
    def setUp(self):
        super().setUp()
        self.cust = customers.create_customer(self.conn, "Asha")

    def test_adjust_and_event_log(self):
        bal = customers.adjust_credit(self.conn, self.cust["id"], 500, "topup")
        self.assertEqual(bal, 500)
        bal = customers.adjust_credit(self.conn, self.cust["id"], -200, "spend")
        self.assertEqual(bal, 300)
        events = customers.credit_events(self.conn, self.cust["id"])
        self.assertEqual([e["delta_cents"] for e in events], [-200, 500])
        self.assertEqual(customers.get_customer(self.conn, self.cust["id"])["store_credit"], 3.0)

    def test_cannot_go_negative(self):
        from pos.customers import InsufficientCreditError
        with self.assertRaises(InsufficientCreditError):
            customers.adjust_credit(self.conn, self.cust["id"], -1)
        self.assertEqual(
            customers.get_customer(self.conn, self.cust["id"])["store_credit_cents"], 0)

    def test_zero_delta_rejected(self):
        with self.assertRaises(catalog.InvalidInputError):
            customers.adjust_credit(self.conn, self.cust["id"], 0)


class CheckoutWithCreditTests(POSTestCase):
    def setUp(self):
        super().setUp()
        self.pid = self.mkproduct(price=10.0, tax_rate=10, quantity=10)
        self.cust = customers.create_customer(self.conn, "Asha")
        customers.adjust_credit(self.conn, self.cust["id"], 1000, "gift")

    def checkout(self, qty, payments=None, **kw):
        return sales.checkout(
            self.conn,
            items=[{"product_id": self.pid, "quantity": qty}],
            payments=payments, customer_id=self.cust["id"],
            use_credit=True, **kw)

    def test_partial_credit_split_with_cash_change(self):
        # 2 x 10 + 10% tax = 22; credit covers 10 -> due 12, cash 20 gives change
        r = self.checkout(2, payments=[{"method": "CASH", "amount": 20}])
        self.assertAlmostEqual(r["total"], 22.0)
        self.assertAlmostEqual(r["store_credit_used"], 10.0)
        self.assertEqual([(p["method"], p["amount"]) for p in r["payments"]],
                         [("CASH", 12.0)])
        self.assertAlmostEqual(r["change"], 8.0)
        self.assertEqual(
            customers.get_customer(self.conn, self.cust["id"])["store_credit_cents"], 0)

    def test_fully_covered_by_credit_needs_no_payment(self):
        customers.adjust_credit(self.conn, self.cust["id"], 1000)  # now 20.00
        r = self.checkout(1)  # 11.00 fully covered
        self.assertAlmostEqual(r["store_credit_used"], 11.0)
        self.assertEqual(r["payments"], [])
        self.assertEqual(
            customers.get_customer(self.conn, self.cust["id"])["store_credit_cents"],
            900)

    def test_insufficient_credit_requires_payment(self):
        from pos.sales import InsufficientPaymentError
        customers.adjust_credit(self.conn, self.cust["id"], -1000)
        customers.adjust_credit(self.conn, self.cust["id"], 500)
        with self.assertRaises(InsufficientPaymentError):
            self.checkout(2, payments=None)  # 22 due, only 5 credit

    def test_credit_sale_records_customer_on_receipt_and_list(self):
        r = self.checkout(1, payments=[{"method": "CASH", "amount": 100}])
        self.assertEqual(r["customer_id"], self.cust["id"])
        self.assertEqual(r["customer_name"], "Asha")
        rows = sales.list_sales(self.conn)
        self.assertEqual(rows[0]["customer_name"], "Asha")

    def test_unknown_customer_rejected(self):
        from pos.customers import CustomerNotFoundError
        with self.assertRaises(CustomerNotFoundError):
            sales.checkout(self.conn,
                           items=[{"product_id": self.pid, "quantity": 1}],
                           payments=[{"method": "CASH", "amount": 50}],
                           customer_id=9999)


if __name__ == "__main__":
    unittest.main()
