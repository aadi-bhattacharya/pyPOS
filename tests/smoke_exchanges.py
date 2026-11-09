"""Browser smoke: returns -> exchange -> credit offset -> new invoice.

All API calls are mocked. No live store database is used.
Run: python tests/smoke_exchanges.py
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
from smoke_refunds import sale

ROOT = Path(__file__).resolve().parent.parent


def main():
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium',
                                     args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1280, 'height': 1000})
        page.set_default_timeout(8000)
        page.on('pageerror', lambda e: errors.append(str(e)))
        html = (ROOT/'pos/templates/index.html').read_text(encoding='utf-8')
        html = html.replace('<script type="module" src="/static/js/app.js"></script>', '')
        html = html.replace('<link rel="stylesheet" href="/static/css/app.css">',
                            '<style>' + (ROOT/'pos/static/css/app.css').read_text(encoding='utf-8') + '</style>')
        page.set_content(html, wait_until='domcontentloaded')
        page.evaluate("location.hash = '#/refunds'; Object.defineProperty(window, 'localStorage', {configurable:true, value:{getItem:()=>null, setItem:()=>{}, removeItem:()=>{}}})")
        modules = {str(p.relative_to(ROOT/'pos/static/js')):p.read_text(encoding='utf-8')
                   for p in (ROOT/'pos/static/js').rglob('*.js')}
        page.evaluate(r'''async ({modules, sale}) => {
          window.__exchange_submitted = [];
          const replacement = {id:19, SKU:'NEW-19', group_name:'Sweater', price:150, tax_rate:5, quantity:5};
          let changed = false;
          window.fetch = async (url, options = {}) => {
            const u = String(url);
            const query = new URL(u, 'http://pypos.test').searchParams;
            const method = options.method || 'GET';
            const body = options.body ? JSON.parse(options.body) : {};
            let data;
            if (u === '/api/staff/status') data = {enabled:true, operator:{id:1,role:'admin',permissions:{}}};
            else if (u.startsWith('/api/settings')) data = {
              store_name:'Cotton Curves',currency:'₹',receipt_paper:'80mm',
              price_tax_mode:'exclusive',show_receipt_logo:'false'};
            else if (u.startsWith('/api/sales?')) {
              if (query.get('status') === 'REFUNDED') data = [];
              else if (query.get('status') === 'COMPLETED') data = query.get('q') ? [
                {id:83, invoice_number:sale.invoice_number,status:'COMPLETED',
                 timestamp:sale.timestamp,total:sale.total,item_count:2,
                 customer_id:9, fully_refunded:false,refunded:false,methods:'CASH'}] : [];
              else data = [];
            } else if (u === '/api/sales/83' && method === 'GET') data = sale;
            else if (u.startsWith('/api/products?')) data = [replacement];
            else if (u === '/api/sales/83/exchange/preview') {
              const n = body.returns.reduce((sum, r) => sum+r.quantity, 0);
              if (!n || body.replacements.length !== 1) throw Error('Missing items');
              data = {customer_id:9,refund_total:n*105,replacement_total:157.5,
                replacement_tax:7.5,credit_applied:n*105,
                additional_due:157.5-n*105,remaining_credit:0};
            } else if (u === '/api/sales/83/exchange') {
              window.__exchange_submitted.push(body);
              const returned = {...sale, id:84, invoice_number:null,
                refund_number:'26-27/R000001-000083',status:'REFUNDED',
                parent_sale_id:83,parent_invoice_number:sale.invoice_number,
                total:105, subtotal:100,tax_total:5, customer_id:9,
                items:[{...sale.items[0],quantity:1,line_total:100}],
                payments:[],refund_reason:body.reason,store_credit_refunded:105};
              const newInvoice = {...sale,id:85,invoice_number:'26-27/000084',
                status:'COMPLETED', parent_sale_id:null,
                items:[{...sale.items[0],quantity:1,receipt_name:'Sweater',
                  product_id:19,unit_price:150,line_total:150, tax_rate:5}],
                total:157.5,subtotal:150,tax_total:7.5,store_credit_used:105,
                payments:[{method:'CASH',amount:52.5}]};
              data = {refund:returned,replacement:newInvoice,
                refund_total:105,credit_applied:105,additional_due:52.5,
                remaining_credit:0};
            } else return new Response(JSON.stringify({error:'Unmocked '+u}), {status:404});
            return new Response(JSON.stringify(data), {status:200,
              headers:{'content-type':'application/json'}});
          };
          const urls = {};
          const build = (key) => {
            if (urls[key]) return urls[key];
            const folder = key.split('/').slice(0,-1);
            let src = modules[key];
            if (typeof src !== 'string') throw Error('Module not found '+key);
            src = src.replace(/(from\s*)(["'])([^"']+)\2/g, (match,prefix,quote,rel) => {
              if (!rel.startsWith('.')) throw new Error('Unsupported import '+rel);
              const parts = folder.slice();
              for (const p of rel.split('/')) {
                if (p === '..') parts.pop(); else if (p !== '.') parts.push(p);
              }
              return prefix+quote+build(parts.join('/'))+quote;
            });
            urls[key] = URL.createObjectURL(new Blob([src], {type:'text/javascript'}));
            return urls[key];
          };
          await import(build('app.js'));
        }''', {'modules': modules, 'sale': sale})
        page.get_by_role('heading', name='Returns & refunds').wait_for()
        page.get_by_label('Find a sale to return').fill(sale['invoice_number'])
        page.get_by_role('button', name='Exchange', exact=True).click()
        page.get_by_role('button', name='Complete exchange').wait_for()
        assert page.get_by_role('button', name='Complete exchange').is_disabled()
        page.get_by_label('Return more Tee').click()
        page.get_by_label('Find replacement product').fill('Sweater')
        page.get_by_role('button', name='Sweater · NEW-19', exact=False).click()
        page.get_by_label('Exchange reason').select_option('Size / fit issue')
        page.get_by_text('Collect extra').wait_for()
        assert page.get_by_text('₹52.50').count() >= 1
        assert page.get_by_role('button', name='Complete exchange').is_enabled()
        page.get_by_role('button', name='Complete exchange').click()
        page.get_by_text('Confirm exchange', exact=False).wait_for()
        page.locator('.modal-overlay').last.get_by_role('button', name='Complete exchange').click()
        page.get_by_text('Invoice: 26-27/000084').wait_for()
        payloads = page.evaluate('window.__exchange_submitted')
        assert len(payloads) == 1, payloads
        assert payloads[0]['returns'] == [{'product_id':7,'quantity':1}], payloads
        assert payloads[0]['replacements'] == [{'product_id':19,'quantity':1}], payloads
        assert payloads[0]['payments'][0]['amount'] == 52.5, payloads
        assert payloads[0]['request_id'], payloads
        assert not errors, errors
        print('PASS: exchange UI chooses returned/replacement goods, previews net tender, creates linked invoice.')
        browser.close()

if __name__ == '__main__':
    main()
