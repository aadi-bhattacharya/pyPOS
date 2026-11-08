"""Isolated browser smoke for sale-day gate, parking and manual UPI verification.

No real POS data or bank transfer; mocked API, NOT full Flask integration.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
import base64, re
ROOT = Path(__file__).resolve().parents[1] / 'pos'
JS = ROOT / 'static/js'
cache={}
def module_data(path):
    path=path.resolve()
    if path in cache:return cache[path]
    content=path.read_text()
    def link(m):
        other=(path.parent / m.group(2)).resolve()
        assert other.is_relative_to(JS)
        return m.group(1) + module_data(other) + m.group(3)
    content=re.sub(r'((?:from\s+|import\s*)["\'])(\.{1,2}/[^"\']+)(["\'])',link,content)
    uri='data:text/javascript;base64,'+base64.b64encode(content.encode()).decode()
    cache[path]=uri
    return uri
html=(ROOT/'templates/index.html').read_text().replace(
    '<script type="module" src="/static/js/app.js"></script>','').replace(
    '<link rel="stylesheet" href="/static/css/app.css">',
    '<style>'+(ROOT/'static/css/app.css').read_text()+'</style>')
product={'id':1,'group_id':1,'group_name':'Test garment','SKU':'SHIRT-BLK-M',
    'barcode':'8901326000001','price':200,'quantity':10,'cost_price':100,'tax_rate':0,
    'attributes':[], 'reorder_point':1, 'brand_name':'', 'category_name':'','category_id':None}
group={'id':1,'name':'Test garment','brand_id':None,'category_id':None,
       'brand_name':'','category_name':'','product_count':1,'total_stock':10}
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1280,'height':860})
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content(html,wait_until='domcontentloaded')
    page.evaluate('''() => { const m=new Map(); Object.defineProperty(window,'localStorage',
      {configurable:true,value:{getItem:k=>m.get(k)||null,setItem:(k,v)=>m.set(k,String(v))}});
      localStorage.setItem('pypos.cart',JSON.stringify({items:[[1,1]],discountMode:'percent',discountValue:0})); }''')
    page.evaluate('''data => {
       window.__parked=[];
       window.__drawer=null;
       window.__lastCheckout=null;
       window.__upiAttempts=0;
       window.fetch=async (url,opts={})=>{
         const path=String(url),method=opts.method||'GET';
         let result=null;
         if(path==='/api/settings') result={store_name:'Test Shop',store_upi_id:'test@upi',
           currency:'₹',price_tax_mode:'exclusive',low_stock_threshold:'5'};
         else if(path==='/api/staff/status') result={enabled:true,
           operator:{id:1,role:'cashier',username:'sales'}};
         else if(path==='/api/products') result=data.products;
         else if(path==='/api/groups') result=data.groups;
         else if(path==='/api/categories') result=[];
         else if(path==='/api/parked' && method==='GET') result=window.__parked;
         else if(path==='/api/parked' && method==='POST'){
            const t=JSON.parse(opts.body);
            result={id:1,label:t.label,items:t.items,salesperson:t.salesperson||''};
            window.__parked.push({...result,item_count:1,est_total:200});
         }
         else if(path==='/api/cash-sessions/current')result=window.__drawer;
         else if(path==='/api/upi/reference'){
           window.__upiAttempts++;
           if(window.__upiAttempts===1)
             return {ok:false,status:503,json:async()=>({error:'Temporary reference error'})};
           result={reference:'POS1F2E3D4C5B6A'};
         }
         else if(path.startsWith('/api/sales') && method==='POST'){
           window.__lastCheckout=JSON.parse(opts.body);
           result={id:1,invoice_number:'26-27/1',status:'COMPLETED',total:200,
             items:[],payments:window.__lastCheckout.payments};
         }
         else if(path==='/api/cash-sessions')result=[];
         else return {ok:false,status:404,json:async()=>({error:'Mock missing '+path})};
         return {ok:true,status:method==='POST'?201:200,json:async()=>result};
       };
    }''',{'products':[product],'groups':[group]})
    page.evaluate('uri=>import(uri)',module_data(JS/'app.js'))
    page.locator('.cart-pane .btn:has-text("Park")').wait_for(timeout=8000)
    # A closed day must prevent the payment dialog.
    page.locator('.cart-summary button:has-text("Charge")').click()
    assert page.locator('.modal-head:has-text("Take payment")').count()==0
    assert 'Start Day' in page.locator('#toast-root').inner_text()
    # Parking must work without the previous undefined checkoutRequestId error.
    page.locator('.cart-pane .btn:has-text("Park")').click()
    # Two synchronous clicks must never create two parked tickets.
    page.evaluate('''() => {
      const btn = [...document.querySelectorAll('.modal-overlay button')]
        .find(b => b.textContent.includes('Park sale'));
      btn.click(); btn.click();
    }''')
    page.wait_for_timeout(300)
    assert page.evaluate('window.__parked.length')==1
    assert 'Parked (1)' in page.locator('.view-actions').inner_text()
    # Restore a cart and simulate opening the day.
    page.evaluate('''() => {
      localStorage.setItem('pypos.cart',JSON.stringify({items:[[1,1]],discountMode:'percent',discountValue:0}));
      window.__drawer={session:{id:3,opened_at:'2026-11-07',closed_at:null},report:{
        session_id:3,opened_at:'2026-11-07',is_open:true,opening_float:0,transactions:0,
        items_sold:0,gross_sales:0,discounts:0,taxes:0,net_revenue:0,refund_count:0,
        refund_total:0,cash_in:0,cash_out:0,cash_net:0,card_net:0,upi_net:0,
        credit_spent:0,expected_cash:0}};
    }''')
    page.evaluate('uri=>import(uri).then(m=>m.renderRegister(document.querySelector("#main")))',module_data(JS/'views/register.js'))
    page.locator('.cart-summary button:has-text("Charge")').click()
    page.locator('.modal-head:has-text("Take payment")').wait_for(timeout=5000)
    page.locator('.pay-row select').first.select_option('UPI')
    page.locator('.upi-reference').wait_for(timeout=6000)
    page.wait_for_timeout(300)
    assert 'Reference unavailable' in page.locator('.upi-extra').inner_text()
    # A transient failure should allow retry without recreating the tender row.
    page.locator('.upi-extra button:has-text("Copy reference")').click()
    page.wait_for_timeout(300)
    assert 'POS1F2E3D4C5B6A' in page.locator('.upi-extra').inner_text()
    assert page.locator('.modal-foot button:has-text("Complete sale")').is_disabled()
    page.locator('.upi-extra input[placeholder*="UTR"]').fill('554433221100')
    page.locator('.upi-extra input[type="checkbox"]').check()
    assert page.locator('.modal-foot button:has-text("Complete sale")').is_enabled()
    # Changing the tender amount invalidates the earlier manual bank confirmation.
    page.locator('.pay-row input.num').first.fill('199')
    assert not page.locator('.upi-extra input[type="checkbox"]').is_checked()
    assert not page.locator('.upi-extra input[placeholder*="UTR"]').input_value()
    page.locator('.pay-row input.num').first.fill('200')
    page.locator('.upi-extra input[placeholder*="UTR"]').fill('554433221100')
    page.locator('.upi-extra input[type="checkbox"]').check()
    assert page.locator('.modal-foot button:has-text("Complete sale")').is_enabled()
    # Invalid fractional-paise amounts must never be silently rounded into QR payloads.
    page.locator('.pay-row input.num').first.fill('200.001')
    page.locator('.upi-extra button:has-text("Show QR")').click()
    assert page.locator('.upi-qr-box').count()==0
    page.locator('.pay-row input.num').first.fill('200')
    page.locator('.upi-extra input[placeholder*="UTR"]').fill('554433221100')
    page.locator('.upi-extra input[type="checkbox"]').check()
    page.locator('.upi-extra button:has-text("Show QR")').click()
    assert page.locator('.upi-qr-box').count()==1
    assert 'POS1F2E3D4C5B6A' in page.locator('.upi-qr-box').inner_text()
    # Close nested QR and finish. Seller UTR & POS ref are stored independently.
    page.locator('.modal-overlay').last.locator('button:has-text("Close QR")').click()
    page.locator('.modal-foot button:has-text("Complete sale")').click()
    page.wait_for_timeout(400)
    payload=page.evaluate('window.__lastCheckout')
    assert payload is not None, (errors,page.locator('#toast-root').inner_text())
    assert payload['payments'][0]['reference']=='POS1F2E3D4C5B6A'
    assert payload['payments'][0]['upi_utr']=='554433221100'
    assert not errors,errors
    print('PASS: closed day gating, parking, UPI reference + UTR + manual confirmation + QR, no JS exceptions')
    browser.close()
