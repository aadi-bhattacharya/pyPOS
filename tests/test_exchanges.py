"""End-to-end exchange accounting; no live store databases involved."""
from base import POSTestCase
from pathlib import Path
import tempfile
from unittest.mock import patch

from pos import catalog, customers, exchanges, sales
from pos.catalog import InsufficientStockError
from pos.customers import CustomerNotFoundError


class ExchangeTests(POSTestCase):
    def setUp(self):
        super().setUp()
        self.old = self.mkproduct(SKU="OLD", price=100, tax_rate=5, quantity=2)
        self.new = self.mkproduct(SKU="NEW", price=150, tax_rate=5, quantity=5)
        self.cheap = self.mkproduct(SKU="CHEAP", price=50, tax_rate=5, quantity=5)
        self.buyer = customers.create_customer(self.conn, "Exchange Buyer")
        self.original = sales.checkout(
            self.conn, [{"product_id": self.old, "quantity": 2}],
            [{"method": "CASH", "amount": 210}])
        self.ret = [{"product_id": self.old, "quantity": 1}]

    def exchange(self, new_product=None, payments=None, request_id="exchange_case_0001", **over):
        return exchanges.exchange_sale(
            self.conn, self.original["id"], self.ret,
            [{"product_id": new_product or self.new, "quantity": 1}],
            customer_id=self.buyer["id"], payments=payments,
            reason="Size / fit issue", request_id=request_id, **over)

    def test_more_expensive_difference_only_collected(self):
        preview = exchanges.preview_exchange(self.conn, self.original["id"], self.ret,
            [{"product_id": self.new, "quantity": 1}], customer_id=self.buyer["id"])
        self.assertEqual((preview["refund_total"], preview["replacement_total"],
                          preview["additional_due"], preview["remaining_credit"]),
                         (105, 157.50, 52.50, 0))
        exchange = self.exchange(payments=[{"method": "UPI", "amount": 52.5}])
        self.assertEqual(exchange["refund"]["refund_number"].split("/")[-1].startswith("R"), True)
        self.assertEqual(exchange["refund"]["store_credit_refunded"], 105)
        self.assertEqual(exchange["replacement"]["store_credit_used"], 105)
        self.assertEqual(exchange["additional_due"], 52.5)
        self.assertEqual(exchange["replacement"]["payments"][0]["amount"], 52.5)
        self.assertEqual(customers.get_customer(self.conn, self.buyer["id"])["store_credit_cents"], 0)
        self.assertEqual(catalog.get_product(self.conn, self.old)["quantity"], 1)
        self.assertEqual(catalog.get_product(self.conn, self.new)["quantity"], 4)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM exchanges").fetchone()[0], 1)

    def test_cheaper_replacement_leaves_customer_credit(self):
        result = self.exchange(new_product=self.cheap, payments=[], request_id="exchange_cheap_01")
        self.assertEqual(result["additional_due"], 0)
        self.assertEqual(result["remaining_credit"], 52.5)
        self.assertEqual(result["replacement"]["store_credit_used"], 52.5)
        self.assertEqual(customers.get_customer(self.conn, self.buyer["id"])["store_credit_cents"], 5250)
        self.assertEqual(result["replacement"]["payments"], [])

    def test_existing_credit_not_accidentally_spent(self):
        customers.adjust_credit(self.conn, self.buyer["id"], 7000, "Old gift")
        self.exchange(payments=[{"method": "CASH", "amount": 52.5}], request_id="exchange_oldcredit1")
        self.assertEqual(customers.get_customer(self.conn, self.buyer["id"])["store_credit_cents"], 7000)

    def test_idempotent_retry_and_changed_key_rejected(self):
        payload = [{"method": "CASH", "amount": 52.5}]
        first = self.exchange(payments=payload, request_id="exchange_retry_01")
        second = self.exchange(payments=payload, request_id="exchange_retry_01")
        self.assertEqual((first["refund"]["id"], first["replacement"]["id"]),
                         (second["refund"]["id"], second["replacement"]["id"]))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 3)
        with self.assertRaisesRegex(sales.InvalidSaleError, "reused"):
            self.exchange(new_product=self.cheap, payments=payload,
                          request_id="exchange_retry_01")

    def test_failed_tender_rolls_back_all_inventory_credit_and_refund(self):
        before = self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        with self.assertRaises(sales.InsufficientPaymentError):
            self.exchange(payments=[{"method": "CASH", "amount": 1}],
                          request_id="exchange_shortpay1")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM exchanges").fetchone()[0], 0)
        self.assertEqual(catalog.get_product(self.conn, self.old)["quantity"], 0)
        self.assertEqual(customers.get_customer(self.conn, self.buyer["id"])["store_credit_cents"], 0)

    def test_invalid_replacement_or_customer_never_refunds(self):
        with self.assertRaises(InsufficientStockError):
            exchanges.exchange_sale(self.conn, self.original["id"], self.ret,
                 [{"product_id": self.new, "quantity": 50}], customer_id=self.buyer["id"],
                 payments=[], reason="Size", request_id="exchange_outofstock")
        with self.assertRaises(CustomerNotFoundError):
            exchanges.exchange_sale(self.conn, self.original["id"], self.ret,
                 [{"product_id": self.new, "quantity": 1}], customer_id=9999,
                 payments=[], reason="Size", request_id="exchange_bad_customer")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 1)

    def test_replacing_same_last_item_uses_returned_stock(self):
        result = self.exchange(new_product=self.old, payments=[], request_id="exchange_sameitem1")
        self.assertEqual(result["refund_total"], result["replacement"]["total"])
        self.assertEqual(catalog.get_product(self.conn, self.old)["quantity"], 0)

    def test_failure_after_both_document_inserts_rolls_back_everything(self):
        actual_checkout = sales.checkout
        def fail_after_inserts(*args, **kwargs):
            actual_checkout(*args, **kwargs)
            raise RuntimeError("Simulated failure after refund and replacement")
        with patch("pos.exchanges.sales.checkout", side_effect=fail_after_inserts):
            with self.assertRaisesRegex(RuntimeError, "Simulated"):
                self.exchange(payments=[{"method": "CASH", "amount": 52.5}],
                              request_id="exchange_atomic_error")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM exchanges").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM refund_sequences").fetchone()[0], 0)
        self.assertEqual(customers.get_customer(self.conn, self.buyer["id"])["store_credit_cents"], 0)
        self.assertEqual(catalog.get_product(self.conn, self.old)["quantity"], 0)
        self.assertEqual(catalog.get_product(self.conn, self.new)["quantity"], 5)

    def test_day_closed_blocks_exchange(self):
        with self.assertRaisesRegex(sales.InvalidSaleError, "Start Day"):
            self.exchange(payments=[{"method": "CASH", "amount": 52.5}],
                          request_id="exchange_closedday1", require_open_session=True)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 1)

    def test_refund_to_credit_anonymous_invoice_and_retry(self):
        p = sales.preview_refund(self.conn, self.original["id"], self.ret,
                to_credit=True, credit_customer_id=self.buyer["id"])
        self.assertEqual(p["refund_store_credit"], 105)
        r = sales.refund_sale(self.conn, self.original["id"], self.ret,
                 to_credit=True, credit_customer_id=self.buyer["id"],
                 reason="Credit", request_id="anon_credit_issue01")
        self.assertEqual(r["customer_id"], self.buyer["id"])
        self.assertEqual(customers.get_customer(self.conn, self.buyer["id"])["store_credit_cents"], 10500)
        r2 = sales.refund_sale(self.conn, self.original["id"], self.ret,
                 to_credit=True, credit_customer_id=self.buyer["id"],
                 reason="Credit", request_id="anon_credit_issue01")
        self.assertEqual(r["id"], r2["id"])
        self.assertIsNone(sales.get_sale(self.conn, self.original["id"])["customer_id"])

    def test_redirect_named_customers_credit_is_blocked(self):
        named = sales.checkout(self.conn,
            [{"product_id": self.new, "quantity": 1}],
            [{"method": "CASH", "amount": 157.5}], customer_id=self.buyer["id"])
        other = customers.create_customer(self.conn, "Other")
        with self.assertRaisesRegex(sales.InvalidSaleError, "original invoice customer"):
            sales.refund_sale(self.conn, named["id"], to_credit=True,
                              credit_customer_id=other["id"])


    def test_anonymous_exchange_without_customer_uses_offset_not_wallet_or_payout(self):
        preview = exchanges.preview_exchange(self.conn, self.original["id"], self.ret,
            [{"product_id": self.new, "quantity": 1}])
        self.assertIsNone(preview["customer_id"])
        self.assertEqual(preview["settlement_mode"], "offset")
        self.assertEqual((preview["exchange_offset"], preview["additional_due"]), (105, 52.5))
        result = exchanges.exchange_sale(self.conn, self.original["id"], self.ret,
            [{"product_id": self.new, "quantity": 1}],
            payments=[{"method": "CASH", "amount": 52.5}],
            request_id="exchange_anon_offset1", reason="Size")
        self.assertIsNone(result["customer_id"])
        self.assertEqual(result["refund"]["payments"], [])
        self.assertEqual(result["refund"]["store_credit_refunded"], 0)
        self.assertEqual(result["replacement"]["exchange_offset_used"], 105)
        self.assertEqual(result["replacement"]["store_credit_used"], 0)
        self.assertEqual(result["replacement"]["payments"][0]["amount"], 52.5)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM credit_events").fetchone()[0], 0)
        export = sales.refund_export_rows(self.conn)
        self.assertEqual(len(export), 1)
        self.assertEqual(export[0][14], "EXCHANGE_OFFSET")
        self.assertEqual(export[0][15:17], (105, 0))
        self.assertEqual(export[0][17], result["replacement"]["invoice_number"])
        self.assertEqual(self.conn.execute("SELECT settlement_mode, offset_cents, forfeited_cents FROM exchanges")
                         .fetchone()["offset_cents"], 10500)
        retried = exchanges.exchange_sale(self.conn, self.original["id"], self.ret,
            [{"product_id": self.new, "quantity": 1}],
            payments=[{"method": "CASH", "amount": 52.5}],
            request_id="exchange_anon_offset1", reason="Size")
        self.assertEqual(result["replacement"]["id"], retried["replacement"]["id"])

    def test_anonymous_equal_price_exchange_without_money(self):
        result = exchanges.exchange_sale(self.conn, self.original["id"], self.ret,
            [{"product_id": self.old, "quantity": 1}], payments=[],
            reason="Size", request_id="exchange_anon_equal1")
        self.assertEqual(result["additional_due"], 0)
        self.assertEqual(result["replacement"]["payments"], [])
        self.assertEqual(result["replacement"]["exchange_offset_used"], 105)
        self.assertEqual(catalog.get_product(self.conn, self.old)["quantity"], 0)

    def test_anonymous_cheaper_replacement_blocks_without_forfeit_policy(self):
        with self.assertRaisesRegex(sales.InvalidSaleError, "Replacement costs less"):
            exchanges.preview_exchange(self.conn, self.original["id"], self.ret,
                [{"product_id": self.cheap, "quantity": 1}])
        with self.assertRaisesRegex(sales.InvalidSaleError, "Replacement costs less"):
            exchanges.exchange_sale(self.conn, self.original["id"], self.ret,
                [{"product_id": self.cheap, "quantity": 1}],
                reason="Size", request_id="exchange_anon_cheaper1")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 1)

    def test_forfeit_policy_requires_per_transaction_ack_and_audits_difference(self):
        from pos import settings
        settings.save_settings(self.conn, {"exchange_allow_forfeit_difference": "true"})
        args = dict(original_id=self.original["id"], returns=self.ret,
            replacements=[{"product_id": self.cheap, "quantity": 1}],
            request_id="exchange_anon_forfeit1", reason="Customer accepted")
        preview = exchanges.preview_exchange(self.conn, args["original_id"], args["returns"], args["replacements"])
        self.assertEqual(preview["forfeited_amount"], 52.5)
        with self.assertRaisesRegex(sales.InvalidSaleError, "Explicitly acknowledge"):
            exchanges.exchange_sale(self.conn, **args)
        result = exchanges.exchange_sale(self.conn, **args, accept_forfeit=True)
        self.assertEqual(result["forfeited_amount"], 52.5)
        self.assertEqual(result["remaining_credit"], 0)
        self.assertEqual(result["replacement"]["exchange_offset_used"], 52.5)
        self.assertEqual(self.conn.execute("SELECT forfeited_cents FROM exchanges").fetchone()[0], 5250)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM credit_events").fetchone()[0], 0)
        self.assertEqual(exchanges.exchange_sale(self.conn, **args, accept_forfeit=True)["request_id"],
                         result["request_id"])
        with self.assertRaisesRegex(sales.InvalidSaleError, "reused"):
            exchanges.exchange_sale(self.conn, **args, accept_forfeit=False)

    def test_forfeit_cannot_be_true_without_admin_policy(self):
        with self.assertRaisesRegex(sales.InvalidSaleError, "Replacement costs less"):
            exchanges.exchange_sale(self.conn, self.original["id"], self.ret,
                [{"product_id": self.cheap, "quantity": 1}],
                request_id="exchange_forfeit_denied", reason="Size", accept_forfeit=True)

    def test_owner_can_disable_refund_payout_without_disabling_exchange(self):
        from pos import settings
        settings.save_settings(self.conn, {"refund_allow_original": "false"})
        with self.assertRaisesRegex(sales.InvalidSaleError, "disabled by store policy"):
            sales.preview_refund(self.conn, self.original["id"], self.ret)
        with self.assertRaisesRegex(sales.InvalidSaleError, "disabled by store policy"):
            sales.refund_sale(self.conn, self.original["id"], self.ret, reason="Not allowed")
        result = exchanges.exchange_sale(self.conn, self.original["id"], self.ret,
            [{"product_id": self.new, "quantity": 1}],
            payments=[{"method": "CASH", "amount": 52.5}],
            request_id="exchange_no_cash_refund1", reason="Size")
        self.assertEqual(result["exchange_offset"], 105)
        self.assertEqual(result["refund"]["payments"], [])

    def test_disable_customer_credit_still_allows_a_direct_exchange(self):
        from pos import settings
        settings.save_settings(self.conn, {"refund_allow_credit": "false"})
        result = self.exchange(payments=[{"method": "CASH", "amount": 52.5}],
                               request_id="exchange_no_wallet1")
        self.assertEqual(result["settlement_mode"], "offset")
        self.assertEqual(result["credit_applied"], 0)
        self.assertEqual(result["exchange_offset"], 105)
        self.assertEqual(customers.get_customer(self.conn, self.buyer["id"])["store_credit_cents"], 0)
        with self.assertRaisesRegex(sales.InvalidSaleError, "disabled by store policy"):
            sales.refund_sale(self.conn, self.original["id"], self.ret, to_credit=True,
                              credit_customer_id=self.buyer["id"], reason="Denied")

    def test_anonymous_exchange_toggle_is_enforced_on_commit(self):
        from pos import settings
        settings.save_settings(self.conn, {"exchange_allow_anonymous": "false"})
        with self.assertRaisesRegex(sales.InvalidSaleError, "requires a customer"):
            exchanges.exchange_sale(self.conn, self.original["id"], self.ret,
                [{"product_id": self.new, "quantity": 1}],
                payments=[{"method": "CASH", "amount": 52.5}],
                request_id="exchange_anon_disabled1", reason="Size")
        result = self.exchange(payments=[{"method": "CASH", "amount": 52.5}],
                               request_id="exchange_known_allowed1")
        self.assertEqual(result["customer_id"], self.buyer["id"])

    def test_exchange_disable_policy_is_enforced_server_side(self):
        from pos import settings
        settings.save_settings(self.conn, {"exchange_allow": "false"})
        with self.assertRaisesRegex(sales.InvalidSaleError, "disabled by store policy"):
            self.exchange(payments=[{"method": "CASH", "amount": 52.5}],
                          request_id="exchange_policy_disabled1")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 1)

    def test_settings_reject_invalid_policy_values(self):
        from pos import settings
        with self.assertRaisesRegex(ValueError, "must be true or false"):
            settings.save_settings(self.conn, {"refund_allow_original": "off"})
        self.assertEqual(settings.get_settings(self.conn)["refund_allow_original"], "true")

    def test_anonymous_failed_sale_does_not_leave_exchange_return_or_offset(self):
        before = self.conn.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
        with self.assertRaises(sales.InsufficientPaymentError):
            exchanges.exchange_sale(self.conn, self.original["id"], self.ret,
                [{"product_id": self.new, "quantity": 1}],
                payments=[{"method": "CASH", "amount": 3}],
                request_id="exchange_anon_rollback1", reason="Size")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM exchanges").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0], before)
        self.assertEqual(catalog.get_product(self.conn, self.old)["quantity"], 0)

    def test_later_original_refund_not_count_exchange_offset_as_cash_payout(self):
        exchanges.exchange_sale(self.conn, self.original["id"], self.ret,
            [{"product_id": self.new, "quantity": 1}],
            payments=[{"method": "CASH", "amount": 52.5}],
            request_id="exchange_then_refund1", reason="Size")
        refunded = sales.refund_sale(self.conn, self.original["id"], self.ret,
                                    reason="Remaining item")
        self.assertEqual(refunded["payments"][0]["amount"], 105)
        self.assertEqual(self.conn.execute("SELECT SUM(amount) FROM payments WHERE sale_id=?",
            (refunded["id"],)).fetchone()[0], 105)


class ExchangeMigrationTests(POSTestCase):
    def test_upgrade_existing_db_adds_exchange_link_table_without_replacing_sales(self):
        from pos.db import connect, init_db
        schema = Path(__file__).resolve().parents[1].joinpath("pos/schema.sql").read_text(encoding="utf-8")
        start = schema.index("-- A single atomic exchange")
        end = schema.index("CREATE TABLE IF NOT EXISTS sale_items", start)
        legacy_schema = schema[:start] + schema[end:]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "shop.db"
            conn = connect(path)
            try:
                conn.executescript(legacy_schema)
                conn.execute("INSERT INTO customers(name) VALUES('Historic')")
                conn.execute("INSERT INTO sales(subtotal,tax_total,total,status) VALUES(5,0,5,'COMPLETED')")
            finally:
                conn.close()
            init_db(path)
            verify = connect(path)
            try:
                self.assertEqual(verify.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 1)
                self.assertEqual(verify.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 1)
                self.assertIsNotNone(verify.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='exchanges'").fetchone())
            finally:
                verify.close()

    def test_atomic_refund_failure_does_not_consume_reference_numbers(self):
        # A failed composite operation rolls back *everything*, not only
        # customer credit. Invoice and refund serials cannot be consumed.
        old = self.mkproduct(SKU="MIG-OLD", price=100, quantity=1, tax_rate=5)
        new = self.mkproduct(SKU="MIG-NEW", price=120, quantity=1, tax_rate=5)
        c = customers.create_customer(self.conn, "Original")
        a = sales.checkout(self.conn, [{"product_id":old,"quantity":1}],
                           [{"method":"CASH","amount":105}], customer_id=c["id"])
        before = (list(self.conn.execute("SELECT * FROM invoice_sequences")),
                  list(self.conn.execute("SELECT * FROM refund_sequences")))
        with self.assertRaises(sales.InsufficientPaymentError):
            exchanges.exchange_sale(self.conn, a["id"], [{"product_id":old,"quantity":1}],
                    [{"product_id":new,"quantity":1}], customer_id=c["id"],
                    payments=[{"method":"CASH","amount":2}], reason="size",
                    request_id="exchange_atomic_test01")
        self.assertEqual(list(self.conn.execute("SELECT * FROM invoice_sequences")), before[0])
        self.assertEqual(list(self.conn.execute("SELECT * FROM refund_sequences")), before[1])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM exchanges").fetchone()[0], 0)
