"""Audited retail operations: supplier receipts/payments and partial physical counts.

All quantities refer to an exact product ID, including separate price-edition
SKUs sharing the same EAN. Never reinterpret counts as deliveries.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from .india import gstin as validate_gstin


class OperationError(ValueError):
    pass


def amount_cents(value, *, allow_zero=False):
    if isinstance(value, bool):
        raise OperationError("Enter a valid amount")
    try:
        x = Decimal(str(value))
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise OperationError("Enter a valid amount") from exc
    if not x.is_finite() or x < 0 or (x == 0 and not allow_zero) or x > 100000000:
        raise OperationError("Amount is outside the supported range")
    if x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) != x:
        raise OperationError("Amount cannot have more than two decimal places")
    return int(x * 100)


def positive_int(value, *, allow_zero=False):
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value or "")):
        raise OperationError("Enter a whole-number quantity")
    n = int(value)
    if n < (0 if allow_zero else 1) or n > 1000000:
        raise OperationError("Quantity must be a whole number from 0 to 1,000,000")
    return n


def new_supplier(conn, name, phone="", gstin="", address=""):
    name, phone, gstin, address = [str(x or "").strip() for x in (name, phone, gstin, address)]
    if not name or len(name) > 180 or len(phone) > 30 or len(gstin) > 15 or len(address) > 500:
        raise OperationError("Invalid supplier details")
    # GSTIN is optional; do not invent one or infer input-tax eligibility.
    if gstin:
        try:
            gstin = validate_gstin(gstin)
        except ValueError as exc:
            raise OperationError(str(exc)) from exc
    try:
        cur = conn.execute("INSERT INTO suppliers(name,phone,gstin,address) VALUES(?,?,?,?)",
                           (name,phone,gstin.upper(),address))
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise OperationError("A supplier with this name already exists") from exc
        raise
    return {"id":cur.lastrowid, "name":name}


def list_suppliers(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM suppliers ORDER BY name COLLATE NOCASE")]


def receive_purchase(conn, supplier_id, supplier_invoice, lines, note="", invoice_gst=None,
                     update_catalog_cost=False):
    """Receive one supplier invoice atomically. Unit cost is actual landed price
    per unit (including any input tax); GST amount is an informational invoice
    field, not an automatically inferred ITC entitlement.
    """
    supplier_invoice = str(supplier_invoice or "").strip()
    note = str(note or "").strip()
    if not supplier_invoice or len(supplier_invoice) > 100 or len(note) > 500:
        raise OperationError("Enter a supplier invoice number (max 100 characters)")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 300:
        raise OperationError("Provide 1–300 purchase lines")
    parsed = []
    seen = set()
    for line in lines:
        if not isinstance(line, dict):
            raise OperationError("Invalid purchase line")
        pid = positive_int(line.get("product_id"))
        if pid in seen:
            raise OperationError("Combine repeated SKU lines before receiving")
        seen.add(pid)
        parsed.append((pid, positive_int(line.get("quantity")),
                       amount_cents(line.get("unit_cost"), allow_zero=True)))
    total = sum(qty*cost for _,qty,cost in parsed)
    if total > 10**13:
        raise OperationError("Purchase total is too large")
    tax = None if invoice_gst in (None, "") else amount_cents(invoice_gst, allow_zero=True)
    if tax is not None and tax > total:
        raise OperationError("Invoice GST cannot exceed the total purchase amount")
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not conn.execute("SELECT 1 FROM suppliers WHERE id=?", (supplier_id,)).fetchone():
            raise OperationError("Supplier does not exist")
        if conn.execute("SELECT 1 FROM purchase_receipts WHERE supplier_id=? AND supplier_invoice=?",
                        (supplier_id, supplier_invoice)).fetchone():
            raise OperationError("Supplier invoice was already received. No stock was added.")
        products = {r["id"]:dict(r) for r in conn.execute(
            f"SELECT id,SKU FROM products WHERE id IN ({','.join('?' for _ in parsed)})",
            [pid for pid,_,_ in parsed])}
        if len(products) != len(parsed):
            raise OperationError("One or more purchase SKUs do not exist; create them first")
        movement_start = conn.execute("SELECT COALESCE(MAX(id),0) FROM stock_movements").fetchone()[0]
        cur = conn.execute("""INSERT INTO purchase_receipts
            (supplier_id,supplier_invoice,total_cents,invoice_gst_cents,note)
            VALUES(?,?,?,?,?)""",(supplier_id,supplier_invoice,total,tax,note))
        rid = cur.lastrowid
        for pid,qty,cost in parsed:
            conn.execute("""INSERT INTO purchase_lines
                (purchase_id,product_id,quantity,unit_cost_cents,sku_at_receipt)
                VALUES(?,?,?,?,?)""",(rid,pid,qty,cost,products[pid]["SKU"]))
            conn.execute("UPDATE products SET quantity=quantity+?,updated=CURRENT_TIMESTAMP WHERE id=?",
                         (qty,pid))
            if update_catalog_cost:
                conn.execute("UPDATE products SET cost_price=? WHERE id=?",(cost/100,pid))
                conn.execute("DELETE FROM product_attributes WHERE product_id=? AND attribute_name='Cost Review Required'",(pid,))
        conn.execute("""UPDATE stock_movements SET kind='PURCHASE_RECEIPT', reference_id=?,
            reason=? WHERE id>? AND kind='CHANGE'""",(rid,f"Supplier invoice {supplier_invoice}",movement_start))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return purchase_detail(conn,rid)


def purchase_detail(conn, receipt_id):
    row = conn.execute("""SELECT p.*, s.name AS supplier_name,
        COALESCE((SELECT SUM(amount_cents) FROM supplier_payments WHERE purchase_id=p.id),0) AS paid_cents
        FROM purchase_receipts p JOIN suppliers s ON s.id=p.supplier_id WHERE p.id=?""",(receipt_id,)).fetchone()
    if not row:
        raise OperationError("Purchase receipt not found")
    doc = dict(row)
    doc["outstanding_cents"] = doc["total_cents"]-doc["paid_cents"]
    doc["lines"] = [dict(r) for r in conn.execute("""SELECT pl.*,pg.name AS product_name
        FROM purchase_lines pl JOIN products pr ON pr.id=pl.product_id
        LEFT JOIN product_groups pg ON pg.id=pr.group_id WHERE pl.purchase_id=? ORDER BY pl.id""",(receipt_id,))]
    doc["payments"] = [dict(r) for r in conn.execute("SELECT * FROM supplier_payments WHERE purchase_id=? ORDER BY id",(receipt_id,))]
    return doc


def list_purchases(conn, limit=100):
    limit = max(1,min(500,int(limit)))
    return [dict(row) for row in conn.execute("""SELECT p.id,p.supplier_invoice,
        p.received_at,p.total_cents,p.invoice_gst_cents,s.name AS supplier_name,
        COALESCE((SELECT SUM(amount_cents) FROM supplier_payments WHERE purchase_id=p.id),0) AS paid_cents
        FROM purchase_receipts p JOIN suppliers s ON s.id=p.supplier_id
        ORDER BY p.id DESC LIMIT ?""",(limit,))]


def pay_supplier(conn, receipt_id, amount, method, reference="", request_id=None):
    method = str(method or "").upper()
    if method not in ("CASH","UPI","CARD","BANK","OTHER"):
        raise OperationError("Unsupported supplier payment method")
    cents = amount_cents(amount)
    reference = str(reference or "").strip()
    if len(reference)>120:
        raise OperationError("Payment reference is too long")
    if request_id is not None:
        request_id = str(request_id)
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", request_id):
            raise OperationError("Invalid payment retry key")
    conn.execute("BEGIN IMMEDIATE")
    try:
        if request_id:
            existing=conn.execute("SELECT * FROM supplier_payments WHERE request_id=?",(request_id,)).fetchone()
            if existing:
                if existing["purchase_id"] != receipt_id or existing["amount_cents"] != cents or existing["method"] != method or existing["reference"] != reference:
                    raise OperationError("Payment retry key reused with different contents")
                conn.commit()
                return purchase_detail(conn,receipt_id)
        rec=conn.execute("""SELECT p.total_cents, COALESCE(SUM(sp.amount_cents),0) AS paid
            FROM purchase_receipts p LEFT JOIN supplier_payments sp ON sp.purchase_id=p.id
            WHERE p.id=? GROUP BY p.id""",(receipt_id,)).fetchone()
        if not rec:
            raise OperationError("Purchase receipt not found")
        if cents > rec["total_cents"]-rec["paid"]:
            raise OperationError("Payment exceeds supplier invoice outstanding")
        session_id=None
        if method=="CASH":
            session=conn.execute("SELECT id FROM cash_sessions WHERE closed_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
            if not session:
                raise OperationError("Start Day before paying a supplier from the cash drawer")
            session_id=session["id"]
        conn.execute("INSERT INTO supplier_payments(purchase_id,amount_cents,method,reference,request_id) VALUES(?,?,?,?,?)",
                     (receipt_id,cents,method,reference,request_id))
        if method=="CASH":
            conn.execute("""INSERT INTO cash_movements
                (cash_session_id,kind,amount_cents,reason,reference)
                VALUES(?,'CASH_OUT',?,?,?)""",(session_id,cents,f"Supplier payment on purchase #{receipt_id}",reference))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return purchase_detail(conn,receipt_id)


def start_stocktake(conn, note=""):
    note = str(note or "").strip()
    if len(note)>500:
        raise OperationError("Stocktake note is too long")
    cur=conn.execute("INSERT INTO stocktakes(note) VALUES(?)",(note,))
    return get_stocktake(conn,cur.lastrowid)


def get_stocktake(conn, stocktake_id):
    row=conn.execute("SELECT * FROM stocktakes WHERE id=?",(stocktake_id,)).fetchone()
    if not row:
        raise OperationError("Stocktake not found")
    result=dict(row)
    result["lines"]=[dict(r) for r in conn.execute("""SELECT sl.*,pr.SKU,pg.name AS product_name,
        pr.quantity AS current_stock FROM stocktake_lines sl
        JOIN products pr ON pr.id=sl.product_id
        LEFT JOIN product_groups pg ON pg.id=pr.group_id
        WHERE sl.stocktake_id=? ORDER BY sl.product_id""",(stocktake_id,))]
    return result


def list_stocktakes(conn):
    return [dict(r) for r in conn.execute("""SELECT st.*,
        (SELECT COUNT(*) FROM stocktake_lines l WHERE l.stocktake_id=st.id) AS counted_skus
        FROM stocktakes st ORDER BY id DESC LIMIT 100""")]


def count_stock(conn, stocktake_id, product_id, counted):
    counted = positive_int(counted,allow_zero=True)
    conn.execute("BEGIN IMMEDIATE")
    try:
        status=conn.execute("SELECT status FROM stocktakes WHERE id=?",(stocktake_id,)).fetchone()
        if not status or status["status"]!="DRAFT":
            raise OperationError("Stocktake is not an editable draft")
        product=conn.execute("SELECT quantity FROM products WHERE id=?",(product_id,)).fetchone()
        if not product:
            raise OperationError("SKU not found")
        conn.execute("""INSERT INTO stocktake_lines(stocktake_id,product_id,expected,counted)
            VALUES(?,?,?,?) ON CONFLICT(stocktake_id,product_id)
            DO UPDATE SET counted=excluded.counted""",(stocktake_id,product_id,product["quantity"],counted))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_stocktake(conn,stocktake_id)


def post_stocktake(conn, stocktake_id, *, adjustment_reason="", actor=None):
    conn.execute("BEGIN IMMEDIATE")
    try:
        row=conn.execute("SELECT status FROM stocktakes WHERE id=?",(stocktake_id,)).fetchone()
        if not row or row["status"]!="DRAFT":
            raise OperationError("Stocktake has already been posted or cancelled")
        lines=[dict(r) for r in conn.execute("""SELECT l.product_id,l.expected,l.counted,p.quantity
            FROM stocktake_lines l JOIN products p ON p.id=l.product_id WHERE l.stocktake_id=?""",(stocktake_id,))]
        if not lines:
            raise OperationError("Count at least one SKU before posting")
        stale=[x["product_id"] for x in lines if x["quantity"]!=x["expected"]]
        if stale:
            raise OperationError("Stock changed since these SKUs were counted. Recheck: " +
                                 ", ".join(map(str,stale[:10])))
        adjustment_reason = str(adjustment_reason or "").strip()
        if any(x["counted"] < x["expected"] for x in lines) and not 4 <= len(adjustment_reason) <= 200:
            raise OperationError("A stocktake reducing stock requires a reason (4–200 characters)")
        if len(adjustment_reason) > 200:
            raise OperationError("Stocktake reason must be 200 characters or shorter")
        movement_start=conn.execute("SELECT COALESCE(MAX(id),0) FROM stock_movements").fetchone()[0]
        for r in lines:
            if r["counted"]!=r["expected"]:
                conn.execute("UPDATE products SET quantity=?,updated=CURRENT_TIMESTAMP WHERE id=?",
                             (r["counted"],r["product_id"]))
        attribution = ""
        if actor:
            attribution = (f"; operator={str(actor.get('username', 'unknown'))[:60]} "
                           f"role={str(actor.get('role', 'unknown'))[:20]}")
        reason = f"Physical stocktake #{stocktake_id}{attribution}"
        if adjustment_reason:
            reason += f" | {adjustment_reason}"
        conn.execute("""UPDATE stock_movements SET kind='STOCKTAKE',reference_id=?,reason=?
            WHERE id>? AND kind='CHANGE'""",(stocktake_id,reason,movement_start))
        conn.execute("UPDATE stocktakes SET status='POSTED',posted_at=CURRENT_TIMESTAMP WHERE id=?",(stocktake_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_stocktake(conn,stocktake_id)
