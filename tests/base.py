import sqlite3
import unittest
from pathlib import Path

from pos.db import connect

SCHEMA = Path(__file__).resolve().parent.parent / "pos" / "schema.sql"


class POSTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        self.conn.executescript(SCHEMA.read_text(encoding="utf-8"))

    def tearDown(self):
        self.conn.close()

    def mkgroup(self, name="General"):
        from pos import catalog
        return catalog.create_product_group(self.conn, name)

    def mkproduct(self, **over):
        from pos import catalog
        args = dict(
            group_id=self.mkgroup(),
            SKU="SKU-1", barcode=None, price=10.0,
            quantity=5, cost_price=5.0, tax_rate=10,
        )
        args.update(over)
        return catalog.add_product_to_group(self.conn, **args)
