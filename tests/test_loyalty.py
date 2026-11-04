"""Loyalty ledger, tax-safe store-credit redemption, reversals, and migration."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from base import POSTestCase
from pos import catalog, customers, loyalty, sales, settings
from pos.db import init_db, connect


class LoyaltyLedgerTests(POSTestCase):
    def setUp(self):
        super().setUp()
        self.cust = customers.create_customer(self.conn, "Customer", phone="9876543210")
        self.pid = self.mkproduct(price=100, quantity=50, tax_rate=0)

    def buy(self, quantity=1, use_credit=False, payments=None):
        if payments is None:
            payments = [{"method": "UPI", "amount": 100 * quantity}]
        return sales.checkout(
            self.conn, items=[{"product_id": self.pid, "quantity": quantity}],
            customer_id=self.cust["id"], payments=payments, use_credit=use_credit)

    def pts(self):
        return customers.get_customer(self.conn, self.cust["id"])["loyalty_points"]

    def test_award_and_history(self):
        r = self.buy(quantity=3)
        self.assertEqual(r["loyalty_points_earned"], 3)
        self.assertEqual(self.pts(), 3)
        ev = loyalty.events(self.conn, self.cust["id"])
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["event_type"], ev[0]["delta_points"], ev[0]["sale_id"]),
                         ("EARN", 3, r["id"]))

    def test_policy_disabled_then_enabled(self):
        settings.save_settings(self.conn, {"loyalty_enabled": "false"})
        self.assertEqual(self.buy()["loyalty_points_earned"], 0)
        settings.save_settings(self.conn, {"loyalty_enabled": "true", "loyalty_earn_spend_rupees": "150",
                                           "loyalty_earn_points": "2"})
        self.assertEqual(self.buy(quantity=4)["loyalty_points_earned"], 4)

    def test_admin_adjust_set_deduct_and_validation(self):
        cid = self.cust["id"]
        self.assertEqual(loyalty.adjust(self.conn, cid, delta=12, reason="Opening points"), 12)
        self.assertEqual(loyalty.adjust(self.conn, cid, delta=-5, reason="Correction"), 7)
        self.assertEqual(loyalty.adjust(self.conn, cid, set_to=2, reason="Reconcile"), 2)
        with self.assertRaises(loyalty.LoyaltyError):
            loyalty.adjust(self.conn, cid, delta=-3, reason="Too much")
        with self.assertRaises(loyalty.LoyaltyError):
            loyalty.adjust(self.conn, cid, delta=1, reason="")
        with self.assertRaises(loyalty.LoyaltyError):
            loyalty.adjust(self.conn, cid, delta=True, reason="boolean")
        with self.assertRaises(loyalty.LoyaltyError):
            loyalty.adjust(self.conn, cid, delta=1, set_to=1, reason="invalid")
        self.assertEqual(self.pts(), 2)
        self.assertEqual(len(loyalty.events(self.conn, cid)), 3)

    def test_redeem_store_credit_atomically_and_no_loyalty_on_credit_spend(self):
        cid = self.cust["id"]
        self.buy(quantity=2)
        settings.save_settings(self.conn, {"loyalty_redeem_paise": "150"})
        x = loyalty.redeem(self.conn, cid, 2)
        self.assertEqual(x["credited_paise"], 300)
        self.assertEqual(x["loyalty_points"], 0)
        self.assertEqual(x["store_credit_cents"], 300)
        with self.assertRaises(loyalty.LoyaltyError):
            loyalty.redeem(self.conn, cid, 1)
        self.assertEqual(customers.get_customer(self.conn, cid)["store_credit_cents"], 300)
        r = self.buy(quantity=1, use_credit=True,
                     payments=[{"method": "UPI", "amount": 97}])
        self.assertEqual(r["store_credit_used"], 3)
        self.assertEqual(r["loyalty_points_earned"], 0)  # 97 rupees, below 100 threshold
        self.assertEqual(self.pts(), 0)

    def test_partial_refund_prorates_then_full_reconciles(self):
        r = self.buy(quantity=3)
        cid = self.cust["id"]
        refund1 = sales.refund_sale(self.conn, r["id"],
                                    lines=[{"product_id": self.pid, "quantity": 1}])
        self.assertEqual(refund1["loyalty_points_reversed"], 1)
        self.assertEqual(self.pts(), 2)
        refund2 = sales.refund_sale(self.conn, r["id"])
        self.assertEqual(refund2["loyalty_points_reversed"], 2)
        self.assertEqual(self.pts(), 0)
        self.assertEqual(sum(ev["delta_points"] for ev in loyalty.events(self.conn, cid)), 0)

    def test_refund_after_redeem_creates_debt_instead_of_blocking_refund(self):
        r = self.buy(quantity=1)
        loyalty.redeem(self.conn, self.cust["id"], 1)
        refund = sales.refund_sale(self.conn, r["id"])
        self.assertEqual(refund["loyalty_points_reversed"], 1)
        self.assertEqual(self.pts(), -1)
        self.buy(quantity=2)
        self.assertEqual(self.pts(), 1)

    def test_void_returns_stock_and_reverses_points(self):
        r = self.buy(quantity=1)
        before = catalog.get_product(self.conn, self.pid)["quantity"]
        v = sales.void_sale(self.conn, r["id"])
        self.assertEqual(v["status"], "VOID")
        self.assertEqual(v["loyalty_points_reversed"], 1)
        self.assertEqual(self.pts(), 0)
        self.assertEqual(catalog.get_product(self.conn, self.pid)["quantity"], before + 1)
        with self.assertRaises(sales.AlreadyProcessedError):
            sales.void_sale(self.conn, r["id"])
        self.assertEqual(self.pts(), 0)

    def test_loyalty_history_prevents_customer_deletion_even_at_zero(self):
        cid = self.cust["id"]
        loyalty.adjust(self.conn, cid, delta=4, reason="Initial")
        loyalty.adjust(self.conn, cid, delta=-4, reason="Corrected")
        self.assertEqual(self.pts(), 0)
        with self.assertRaises(customers.CustomersError):
            customers.delete_customer(self.conn, cid)

    def test_bad_settings_cannot_corrupt_reward_policy(self):
        with self.assertRaises(ValueError):
            settings.save_settings(self.conn, {"loyalty_earn_spend_rupees": "0"})
        with self.assertRaises(ValueError):
            settings.save_settings(self.conn, {"loyalty_redeem_paise": "1.5"})
        with self.assertRaises(ValueError):
            settings.save_settings(self.conn, {"loyalty_enabled": "maybe"})
        self.assertEqual(loyalty.policy(self.conn)["spend_paise"], 10000)

    def test_checkout_rollback_not_earning_on_failure(self):
        with self.assertRaises(sales.InsufficientPaymentError):
            self.buy(quantity=1, payments=[{"method": "UPI", "amount": 1}])
        self.assertEqual(self.pts(), 0)
        self.assertEqual(loyalty.events(self.conn, self.cust["id"]), [])

    def test_migration_from_original_schema_and_repeatability(self):
        # Old user data (customer table without the new column) is retained.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "pos.db"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            db.execute("INSERT INTO customers(id,name) VALUES(1,'Existing')")
            db.commit(); db.close()
            # Full minimal old customers schema is sufficient for the new migration,
            # but missing other tables requires the new schema to create them first.
            init_db(path)
            init_db(path)
            db = connect(path)
            self.assertEqual(db.execute("SELECT name, loyalty_points FROM customers WHERE id=1")
                             .fetchone()["loyalty_points"], 0)
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='loyalty_events'").fetchone())
            db.close()


if __name__ == '__main__':
    unittest.main()
