"""Browser smoke for Returns: no Flask or live shop DB required.

Run: python tests/smoke_refunds.py
Requires playwright + Chromium in the test environment.
"""
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import json
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sale = {
    'id': 83, 'invoice_number': '26-27/000083', 'timestamp': '2026-11-05 10:00:00',
    'status': 'COMPLETED', 'total': 210, 'subtotal': 200, 'discount_total': 0,
    'tax_total': 10, 'customer_id': 9, 'customer_name': 'Customer',
    'items': [{ 'product_id': 7, 'name':'Tee (Colour: Black, Size: M)',
                'receipt_name': 'Tee', 'receipt_variant': 'Black · M',
                'unit_price': 100, 'quantity': 2, 'refundable_qty': 2,
                'unit_code':'NOS', 'line_total': 200, 'refunded_qty':0,
                'tax_rate': 5}],
    'children': [], 'refunded': False, 'fully_refunded': False,
    'payments': [{'method':'CASH','amount':210}], 'store_credit_used':0,
    'store_credit_refunded':0, 'loyalty_points_earned':2,
    'invoice': {'store_name':'Cotton Curves','price_tax_mode':'exclusive'},
    'tax_breakup': [], 'tax_review_warning':False,
}
submitted = []
refund = None


def main():
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium',
                                     args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1220, 'height': 860})
        page.set_default_timeout(3000)
        page.on('pageerror', lambda err: errors.append(str(err)))

        # Browser admin policy blocks network navigation. A dependency-ordered
        # Blob module graph exercises the actual ES modules in about:blank.
        html = (ROOT/'pos/templates/index.html').read_text()
        html = html.replace('<script type="module" src="/static/js/app.js"></script>', '')
        html = html.replace('<link rel="stylesheet" href="/static/css/app.css">',
                            '<style>' + (ROOT/'pos/static/css/app.css').read_text() + '</style>')
        page.set_content(html, wait_until='domcontentloaded')
        page.evaluate("location.hash = '#/refunds'; Object.defineProperty(window, 'localStorage', {configurable:true, value:{getItem:()=>null, setItem:()=>{}, removeItem:()=>{}}})")
        modules = {str(p.relative_to(ROOT/'pos/static/js')):p.read_text()
                   for p in (ROOT/'pos/static/js').rglob('*.js')}
        page.evaluate(r'''async ({modules, sale}) => {
          window.__submitted = [];
          let refund = null;
          window.fetch = async (url, options = {}) => {
            const u = String(url);
            const query = new URL(u, 'http://pypos.test').searchParams;
            const method = options.method || 'GET';
            const body = options.body ? JSON.parse(options.body) : {};
            let data;
            if (u === '/api/staff/status') data = {enabled:true,operator:{id:1,role:'admin',permissions:{}}};
            else if (u.startsWith('/api/settings')) data = {
              store_name:'Cotton Curves', currency:'₹', receipt_paper:'80mm',
              price_tax_mode:'exclusive', show_receipt_logo:'false'};
            else if (u.startsWith('/api/sales?')) {
              if (query.get('status') === 'REFUNDED') data = refund ? [refund] : [];
              else if (query.get('status') === 'COMPLETED') data = query.get('q') ? [
                {id:83, invoice_number:sale.invoice_number, status:'COMPLETED',
                 timestamp:sale.timestamp, total:sale.total, item_count:2,
                 customer_id:9, fully_refunded:false, refunded:false, methods:'CASH'}] : [];
              else data = [];
            } else if (u === '/api/sales/83' && method === 'GET') data = sale;
            else if (u.endsWith('/refund/preview')) {
              const qty = body.lines.reduce((s,x) => s+x.quantity, 0);
              data = {total:qty*105, quantity:qty, discount_total:0,
                tax_total:qty*5, refund_payments: body.to_credit ? [] :
                  [{method:'CASH',amount:qty*105}],
                refund_store_credit: body.to_credit ? qty*105 : 0};
            } else if (u === '/api/sales/83/refund' && method === 'POST') {
              window.__submitted.push(body);
              refund = {...sale, id:84, invoice_number:null, refund_number:'26-27/R000001-000083', status:'REFUNDED',
                 parent_sale_id:83, parent_invoice_number:sale.invoice_number,
                 total:105, subtotal:100, tax_total:5,
                 items:[{...sale.items[0], quantity:1, line_total:100}],
                 payments:[{method:'CASH',amount:105}],
                 refund_reason:body.reason,
                 invoice:{...sale.invoice, refund_reason:body.reason}};
              data = refund;
            } else if (u === '/api/sales/84' && method === 'GET') data = refund;
            else return new Response(JSON.stringify({error:'Unmocked '+u}), {status:404});
            return new Response(JSON.stringify(data), {status:200,
              headers:{'content-type':'application/json'}});
          };
          const urls = {};
          const build = (key) => {
            if (urls[key]) return urls[key];
            const folder = key.split('/').slice(0,-1);
            let src = modules[key];
            src = src.replace(/(from\s*)(["'])([^"']+)\2/g, (match,prefix,quote,rel) => {
              if (!rel.startsWith('.')) throw new Error('Unsupported import '+rel);
              const parts = folder.slice();
              for (const p of rel.split('/')) {
                if (p === '..') parts.pop(); else if (p !== '.') parts.push(p);
              }
              const dep = parts.join('/');
              return prefix+quote+build(dep)+quote;
            });
            urls[key] = URL.createObjectURL(new Blob([src], {type:'text/javascript'}));
            return urls[key];
          };
          await import(build('app.js'));
        }''', {'modules': modules, 'sale': sale})
        page.get_by_role('heading', name='Returns & refunds').wait_for()
        assert page.get_by_text('No recorded refunds yet.').count() == 1
        page.get_by_label('Find a sale to return').fill('26-27/000083')
        page.get_by_role('button', name='Return items').click()
        page.get_by_text('0 units selected').wait_for()
        assert page.get_by_role('button', name='Record refund').is_disabled()
        page.get_by_role('button', name='Increase quantity for Tee').click()
        page.get_by_text('1 unit selected').wait_for()
        page.get_by_label('Return reason').select_option('Size / fit issue')
        page.get_by_text('Record return via: Cash ₹105.00').wait_for()
        page.screenshot(path=str(ROOT.parent/'refunds_dialog_smoke.png'), full_page=True)
        assert page.get_by_role('button', name='Record refund').is_enabled()
        page.get_by_role('button', name='Record refund').click()
        page.get_by_text('Confirm return', exact=False).wait_for()
        page.locator('.modal-overlay').last.get_by_role('button', name='Record refund').click()
        page.get_by_text('TOTAL REFUNDED').wait_for()
        page.get_by_text('Refund: 26-27/R000001-000083').wait_for()
        submitted = page.evaluate('window.__submitted')
        assert len(submitted) == 1
        assert submitted[0]['request_id'] and submitted[0]['lines'] == [{'product_id':7,'quantity':1}]
        assert submitted[0]['reason'] == 'Size / fit issue'
        assert page.get_by_text('Return reason: Size / fit issue').count() == 1
        assert not errors, errors
        print('PASS: Returns navigation, invoice lookup, safe default selection, exact preview, confirmation and refund receipt.')
        browser.close()


if __name__ == '__main__':
    main()
