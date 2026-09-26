import random
from datetime import datetime, timedelta, timezone

from . import catalog, sales


def _attr(conn, pid, name, value):
    catalog.set_product_attribute(conn, pid, name, value)


def seed_demo_data(conn):
    """Populate a realistic starter catalog + two weeks of sales history.

    Refuses to run if the catalog is not empty.
    """
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM products")
    if cur.fetchone()["n"] > 0:
        raise catalog.CatalogError("Catalog is not empty — demo data can only be loaded once")

    groups = {}
    for gname in ("Beverages", "Bakery", "Snacks", "Personal Care", "Apparel"):
        groups[gname] = catalog.create_product_group(conn, gname)

    def product(gname, sku, price, qty, cost, tax=5.0, barcode=None, attrs=None):
        pid = catalog.add_product_to_group(
            conn, groups[gname], sku, barcode, price, qty, cost, tax
        )
        for k, v in (attrs or {}).items():
            _attr(conn, pid, k, v)
        return pid

    cola = product("Beverages", "BEV-COLA-330", 1.50, 200, 0.85,
                   barcode="8901234500011", attrs={"Volume": "330 ml"})
    water = product("Beverages", "BEV-WAT-1000", 0.99, 260, 0.35,
                    barcode="8901234500028", attrs={"Volume": "1 L"})
    oj = product("Beverages", "BEV-OJ-500", 3.25, 70, 2.10,
                 barcode="8901234500035", attrs={"Volume": "500 ml"})
    bread = product("Bakery", "BAK-BRD-WHT", 2.40, 90, 1.20,
                    barcode="8901234500042")
    croissant = product("Bakery", "BAK-CRO-BTR", 1.80, 80, 0.90,
                        barcode="8901234500059")
    chips = product("Snacks", "SNK-CHP-SLT", 2.00, 150, 1.10,
                    barcode="8901234500066")
    choc = product("Snacks", "SNK-CHO-DRK", 3.50, 110, 1.95,
                   barcode="8901234500073")
    shampoo_s = product("Personal Care", "PC-SHP-200", 4.99, 90, 2.60,
                        barcode="8901234500080", attrs={"Size": "200 ml"})
    shampoo_l = product("Personal Care", "PC-SHP-500", 8.99, 60, 5.10,
                        barcode="8901234500097", attrs={"Size": "500 ml"})
    tee_m = product("Apparel", "APP-TEE-M", 12.00, 40, 6.00,
                    attrs={"Size": "M", "Color": "Navy"})
    tee_l = product("Apparel", "APP-TEE-L", 12.00, 12, 6.00,
                    attrs={"Size": "L", "Color": "Black"})

    # Two weeks of plausible sales history so Reports has something to show.
    random.seed(42)
    pool = [cola, water, oj, bread, croissant, chips, choc, shampoo_s, shampoo_l, tee_m]
    tz = datetime.now().astimezone().tzinfo
    now = datetime.now(tz)
    made = []
    for day in range(13, -1, -1):
        txns = random.randint(2, 7)
        for _ in range(txns):
            base = now - timedelta(days=day,
                                   hours=random.randint(0, 11),
                                   minutes=random.randint(0, 59))
            if base > now:
                base = now
            picks = random.sample(pool, k=random.randint(1, 4))
            items = []
            for p in picks:
                avail = catalog.get_product(conn, p)["quantity"]
                q = min(random.randint(1, 3), avail)
                if q > 0:
                    items.append({"product_id": p, "quantity": q})
            if not items:
                continue
            method = random.choices(["CASH", "CARD", "UPI"], weights=[4, 4, 2])[0]
            discount_mode = discount_value = None
            if random.random() < 0.15:
                discount_mode, discount_value = "percent", 10

            # Pay exactly what the sale totals to (server recomputes anyway).
            prods = [catalog.get_product(conn, i["product_id"]) for i in items]
            totals = sales.compute_totals(
                [{"price": p["price"], "tax_rate": p["tax_rate"],
                  "quantity": i["quantity"]} for i, p in zip(items, prods)],
                discount_mode=discount_mode, discount_value=discount_value,
            )
            receipt = sales.checkout(
                conn, items,
                payments=[{"method": method,
                           "amount": float(totals["total"])}],
                discount_mode=discount_mode, discount_value=discount_value,
            )
            ts_utc = base.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE sales SET timestamp = ? WHERE id = ?",
                         (ts_utc, receipt["id"]))
            made.append(receipt["id"])
    conn.commit()
    cur.execute("SELECT COUNT(*) AS n FROM products")
    product_count = cur.fetchone()["n"]
    return {"groups": len(groups), "products": product_count,
            "sales_created": len(made)}
