"""Shop reporting in India Standard Time, using recorded sale/refund values.

Revenue here means sales less recorded refund documents. It is not cash
collected, profit, bank settlement, or a statutory GST return.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .india import IST


def _period_bounds(days, today=None):
    """Inclusive UTC timestamp bounds for the latest N *local* calendar days."""
    days = max(1, min(int(days), 365))
    today = today or datetime.now(IST).date()
    start = datetime.combine(today - timedelta(days=days - 1), datetime.min.time(), IST)
    end = datetime.combine(today + timedelta(days=1), datetime.min.time(), IST)
    fmt = '%Y-%m-%d %H:%M:%S'
    return (start.astimezone(timezone.utc).strftime(fmt),
            (end.astimezone(timezone.utc) - timedelta(seconds=1)).strftime(fmt))


def _cents(value):
    return int(round(float(value or 0) * 100))


def _money(cents):
    return round(cents / 100, 2)


def _local_day(timestamp):
    utc = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    return utc.astimezone(IST).date().isoformat()


def _sales_rows(conn, start, end):
    return [dict(r) for r in conn.execute('''
        SELECT s.id, s.timestamp, s.status, s.parent_sale_id, s.customer_id,
               s.total, s.tax_total, s.discount_total,
               COALESCE(q.units, 0) AS units
        FROM sales s
        LEFT JOIN (SELECT sale_id, SUM(quantity) AS units FROM sale_items GROUP BY sale_id) q
          ON q.sale_id = s.id
        WHERE s.timestamp BETWEEN ? AND ? ORDER BY s.timestamp, s.id
    ''', (start, end))]


def _totals(rows):
    out = dict(gross_cents=0, refund_cents=0, tax_cents=0, discount_cents=0,
               transactions=0, refund_count=0, void_count=0, items=0,
               customer_ids=set())
    for r in rows:
        status = r['status']
        if status == 'VOID':
            out['void_count'] += 1
            continue
        if status not in ('COMPLETED', 'REFUNDED'):
            continue
        sign = 1 if status == 'COMPLETED' else -1
        if sign == 1:
            out['transactions'] += 1
            out['gross_cents'] += _cents(r['total'])
            out['discount_cents'] += _cents(r['discount_total'])
            if r['customer_id'] is not None:
                out['customer_ids'].add(r['customer_id'])
        else:
            out['refund_count'] += 1
            out['refund_cents'] += _cents(r['total'])
        out['tax_cents'] += sign * _cents(r['tax_total'])
        out['items'] += sign * int(r['units'])
    out['net_cents'] = out['gross_cents'] - out['refund_cents']
    return out


def _serialize_totals(t):
    return {
        'gross_sales': _money(t['gross_cents']),
        'refund_total': _money(t['refund_cents']),
        'net_revenue': _money(t['net_cents']),
        'net_tax': _money(t['tax_cents']),
        'discount_total': _money(t['discount_cents']),
        'transactions': t['transactions'], 'refund_count': t['refund_count'],
        'void_count': t['void_count'], 'net_items': t['items'],
        'unique_customers': len(t['customer_ids']),
        'avg_basket': _money(round(t['gross_cents'] / t['transactions']))
            if t['transactions'] else 0.0,
    }


def _top_products(conn, start, end):
    # Amounts use the saved item-level discount and GST values. Refund lines
    # subtract from the originating item totals, even for partially refunded sales.
    rows = conn.execute('''
        SELECT si.product_id, COALESCE(NULLIF(si.description_at_sale, ''), g.name, si.sku_at_sale) AS name,
               COALESCE(NULLIF(si.sku_at_sale, ''), p.SKU, '') AS sku,
               si.quantity, si.unit_price, si.discount_cents, si.tax_cents,
               s.status, s.invoice_snapshot
        FROM sale_items si JOIN sales s ON s.id = si.sale_id
        LEFT JOIN products p ON p.id = si.product_id
        LEFT JOIN product_groups g ON g.id = p.group_id
        WHERE s.timestamp BETWEEN ? AND ? AND s.status IN ('COMPLETED', 'REFUNDED')
    ''', (start, end)).fetchall()
    import json
    agg = {}
    for r in rows:
        sign = 1 if r['status'] == 'COMPLETED' else -1
        try:
            snapshot = json.loads(r['invoice_snapshot'] or '{}')
        except (TypeError, ValueError):
            snapshot = {}
        inclusive = snapshot.get('price_tax_mode') == 'inclusive'
        value = (_cents(r['unit_price'] * r['quantity']) - int(r['discount_cents'] or 0)
                 + (0 if inclusive else int(r['tax_cents'] or 0)))
        key = r['product_id']
        if key not in agg:
            agg[key] = {'product_id': key, 'group_name': r['name'] or 'Unnamed item',
                        'SKU': r['sku'], 'qty': 0, 'cents': 0}
        agg[key]['qty'] += sign * r['quantity']
        agg[key]['cents'] += sign * value
    return [{k: v for k, v in {**item, 'revenue': _money(item['cents'])}.items() if k != 'cents'}
            for item in sorted(agg.values(), key=lambda x: x['cents'], reverse=True)[:10]
            if item['qty'] or item['cents']]


def _payment_mix(conn, start, end):
    rows = conn.execute('''
        SELECT p.method, s.status, COUNT(*) AS count, COALESCE(SUM(p.amount),0) AS amount
        FROM payments p JOIN sales s ON p.sale_id = s.id
        WHERE s.timestamp BETWEEN ? AND ? AND s.status IN ('COMPLETED', 'REFUNDED')
        GROUP BY p.method, s.status
    ''', (start, end)).fetchall()
    mix = defaultdict(lambda: {'amount_cents': 0, 'count': 0})
    for r in rows:
        sign = 1 if r['status'] == 'COMPLETED' else -1
        mix[r['method']]['amount_cents'] += sign * _cents(r['amount'])
        if sign == 1:
            mix[r['method']]['count'] += r['count']
    return [{'method': method, 'amount': _money(v['amount_cents']), 'count': v['count']}
            for method, v in sorted(mix.items(), key=lambda kv: kv[1]['amount_cents'], reverse=True)]


def summary(conn, days=14, low_stock_threshold=5):
    days = max(1, min(int(days), 365))
    today = datetime.now(IST).date()
    start, end = _period_bounds(days, today)
    prev_start, prev_end = _period_bounds(days, today - timedelta(days=days))
    rows = _sales_rows(conn, start, end)
    prev = _totals(_sales_rows(conn, prev_start, prev_end))
    period = _totals(rows)
    current_day = today.isoformat()
    today_totals = _totals([r for r in rows if _local_day(r['timestamp']) == current_day])

    by_day = defaultdict(list)
    for r in rows:
        by_day[_local_day(r['timestamp'])].append(r)
    series = []
    for i in range(days):
        date = (today - timedelta(days=days - 1 - i)).isoformat()
        t = _totals(by_day[date])
        series.append({'date': date, 'revenue': _money(t['net_cents']),
                       'gross': _money(t['gross_cents']),
                       'refunds': _money(t['refund_cents']),
                       'txns': t['transactions'], 'refund_count': t['refund_count'],
                       'net_items': t['items']})
    low_stock = [dict(r) for r in conn.execute('''
        SELECT p.id, p.SKU, p.quantity, g.name AS group_name
        FROM products p LEFT JOIN product_groups g ON g.id = p.group_id
        WHERE p.quantity <= ? ORDER BY p.quantity ASC, g.name LIMIT 50
    ''', (low_stock_threshold,))]
    return {
        'days': days,
        'period': _serialize_totals(period),
        'previous_period': _serialize_totals(prev),
        'today': _serialize_totals(today_totals),
        'series': series,
        'top_products': _top_products(conn, start, end),
        'payment_mix': _payment_mix(conn, start, end),
        'low_stock': low_stock,
        # Legacy keys retained for integrations that already use this endpoint.
        'today_revenue': _money(today_totals['net_cents']),
        'today_transactions': today_totals['transactions'],
        'today_avg_basket': _serialize_totals(today_totals)['avg_basket'],
        'period_items_sold': period['items'],
        'period_refund_count': period['refund_count'],
        'period_refund_total': _money(period['refund_cents']),
    }
