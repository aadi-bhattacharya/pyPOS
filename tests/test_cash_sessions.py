import unittest

from base import POSTestCase
from pos import catalog, sales, cash_sessions


def make_sale(conn, pid, qty, method="CASH", amount=None):
    # products in these tests have tax_rate=0 -> exactly qty*10 due;
    # non-cash tenders must be exact (no change on card/UPI)
    return sales.checkout(
        conn, items=[{"product_id": pid, "quantity": qty}],
        payments=[{"method": method,
                   "amount": amount if amount is not None else round(qty * 10, 2)}])


class CashSessionTests(POSTestCase):
    def setUp(self):
        super().setUp()
        self.pid = self.mkproduct(SKU="P-1", price=10.0, tax_rate=0, quantity=50)

    def test_open_close_lifecycle(self):
        opened = cash_sessions.open_session(self.conn, opening_float=20)
        self.assertTrue(opened["session"]["opened_at"])
        self.assertTrue(opened["report"]["is_open"])
        with self.assertRaises(cash_sessions.SessionAlreadyOpenError):
            cash_sessions.open_session(self.conn, 5)

        closed = cash_sessions.close_session(self.conn, counted_cash=20, note="quiet day")
        self.assertFalse(closed["report"]["is_open"])
        self.assertAlmostEqual(closed["report"]["difference"], 0.0)
        with self.assertRaises(cash_sessions.NoOpenSessionError):
            cash_sessions.close_session(self.conn, counted_cash=0)

    def test_negative_float_rejected(self):
        from pos.catalog import InvalidInputError
        with self.assertRaises(InvalidInputError):
            cash_sessions.open_session(self.conn, -1)

    def test_z_report_math(self):
        cash_sessions.open_session(self.conn, opening_float=10.0)

        # +2 units cash (20), +1 unit card (10), then refund one unit of the
        # first sale (cash back 10) and void nothing.
        s1 = make_sale(self.conn, self.pid, 2)                      # CASH 20
        make_sale(self.conn, self.pid, 1, method="CARD", amount=10)  # CARD 10
        sales.refund_sale(self.conn, s1["id"],
                          lines=[{"product_id": self.pid, "quantity": 1}])

        closed = cash_sessions.close_session(self.conn, counted_cash=21)
        rep = closed["report"]
        self.assertEqual(rep["transactions"], 2)
        self.assertAlmostEqual(rep["gross_sales"], 30.0)
        self.assertAlmostEqual(rep["net_revenue"], 30.0 - 10.0)
        self.assertAlmostEqual(rep["cash_in"], 20.0)
        self.assertAlmostEqual(rep["cash_out"], 10.0)
        self.assertAlmostEqual(rep["card_net"], 10.0)
        self.assertAlmostEqual(rep["expected_cash"], 10.0 + 20.0 - 10.0)  # 20
        self.assertAlmostEqual(rep["counted_cash"], 21.0)
        self.assertAlmostEqual(rep["difference"], 1.0)

    def test_report_for_any_session_id(self):
        opened = cash_sessions.open_session(self.conn, 5)
        sid = opened["session"]["id"]
        make_sale(self.conn, self.pid, 1, method="UPI")
        rep = cash_sessions.session_report(self.conn, sid)
        self.assertAlmostEqual(rep["upi_net"], 10.0)
        self.assertTrue(rep["is_open"])
        history = cash_sessions.list_sessions(self.conn)
        self.assertEqual(history[0]["id"], sid)


if __name__ == "__main__":
    unittest.main()
