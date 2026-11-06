"""Optional UI smoke test: python tests/smoke_cash_drawer.py
Requires playwright + Chromium; uses mocked API to avoid mutating POS database.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
import base64
import re

root = Path(__file__).resolve().parents[1] / 'pos'
jsroot = root / 'static/js'
cache = {}

def module_data(path):
    path = path.resolve()
    if path in cache:
        return cache[path]
    content = path.read_text()
    def link(match):
        other = (path.parent / match.group(2)).resolve()
        assert other.is_relative_to(jsroot)
        return match.group(1) + module_data(other) + match.group(3)
    content = re.sub(r'((?:from\s+|import\s*)["\'])(\.{1,2}/[^"\']+)(["\'])', link, content)
    uri = 'data:text/javascript;base64,' + base64.b64encode(content.encode()).decode()
    cache[path] = uri
    return uri

html = (root/'templates/index.html').read_text().replace(
    '<script type="module" src="/static/js/app.js"></script>', '').replace(
    '<link rel="stylesheet" href="/static/css/app.css">',
    '<style>'+(root/'static/css/app.css').read_text()+'</style>')
product = {'id':1,'group_id':1,'group_name':'Basic T-Shirt','SKU':'TS-001',
    'barcode':'100001','price':500,'quantity':10,'cost_price':250,'tax_rate':5,
    'attributes':[], 'reorder_point':1, 'brand_name':'', 'category_name':'',
    'category_id':None}
group = {'id':1,'name':'Basic T-Shirt','brand_id':None,'category_id':None,
    'brand_name':'','category_name':'','product_count':1,'total_stock':10}

def report(is_open=True):
    return {'session_id':2,'opened_at':'2026-11-05 05:30:00',
        'closed_at':None if is_open else '2026-11-05 15:30:00',
        'is_open':is_open,'opening_float':500,'transactions':2,'items_sold':3,
        'gross_sales':1000,'discounts':0,'taxes':50,'net_revenue':1050,
        'refund_count':0,'refund_total':0,'cash_in':600,'cash_out':100,
        'cash_net':500,'card_net':200,'upi_net':300,'credit_spent':0,
        'expected_cash':1000,'counted_cash':None if is_open else 990,
        'difference':None if is_open else -10,'note':''}

errors=[]
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
    page = browser.new_page(viewport={'width':1420,'height':900})
    page.on('pageerror',lambda e: errors.append(str(e)))
    page.set_content(html,wait_until='domcontentloaded')
    page.evaluate('''() => {const m=new Map(); Object.defineProperty(window,'localStorage',
      {configurable:true,value:{getItem:k=>m.get(k)||null,setItem:(k,v)=>m.set(k,String(v))}}); }''')
    page.evaluate('''(data) => {
      window.__session = null;
      window.__history = [];
      window.__operations = [];
      window.fetch = async (url, opts={}) => {
        const path=String(url);
        const method=opts.method||'GET';
        let result=null;
        if (path==='/api/settings') result=data.settings;
        else if (path==='/api/products') result=data.products;
        else if (path==='/api/groups') result=data.groups;
        else if (path==='/api/categories') result=[];
        else if (path==='/api/parked') result=[];
        else if (path==='/api/cash-sessions/current') result=window.__session;
        else if (path==='/api/cash-sessions') result=window.__history;
        else if (path==='/api/cash-sessions/open' && method==='POST') {
          window.__operations.push('open');
          window.__session={id:2,opened_at:'2026-11-05 05:30:00',report:data.report};
          result={session:window.__session,report:data.report};
        }
        else if (path==='/api/cash-sessions/close' && method==='POST') {
          window.__operations.push('close');
          const closed={...data.report,is_open:false,closed_at:'2026-11-05 15:30:00',
            counted_cash:990,difference:-10};
          window.__session=null;
          window.__history=[{id:2,opened_at:'2026-11-05 05:30:00',
            closed_at:'2026-11-05 15:30:00', net_revenue:1050,counted_cash:990,
            drawer_difference:-10}];
          result={report:closed};
        }
        else if (path==='/api/cash-sessions/2/report') result={...data.report,is_open:false,
          closed_at:'2026-11-05 15:30:00',counted_cash:990,difference:-10};
        else if (path==='/api/reports/summary?days=14') result={series:[],period:{},
          previous_period:{},today:{},top_products:[],low_stock:[],payment_mix:[]};
        else return {ok:false,status:404,json:async()=>({error:'Not found '+path})};
        return {ok:true,status:200,json:async()=>result};
      };
    }''', {'settings':{'store_name':'Demo','currency':'₹','price_tax_mode':'exclusive',
                     'low_stock_threshold':'5'},'products':[product],'groups':[group],
            'report':report()})
    page.evaluate('uri => import(uri)',module_data(jsroot/'app.js'))
    page.locator('.reg-cash-strip button:has-text("Start Day")').wait_for(timeout=10000)
    assert page.locator('#nav a[data-view="cash"]').count()==1
    assert page.locator('.reg-cash-strip').inner_text().find('Day closed')!=-1
    page.locator('.reg-cash-strip button:has-text("Start Day")').click()
    page.locator('.modal-overlay input[type="number"]').fill('500')
    page.locator('.modal-overlay button:has-text("Open day")').click()
    page.locator('.reg-cash-strip button:has-text("End Day")').wait_for(timeout=6000)
    assert '₹1,000.00' in page.locator('.reg-cash-strip').inner_text()
    page.locator('.reg-cash-strip button:has-text("X Report")').click()
    page.locator('.modal-overlay').wait_for(timeout=6000)
    assert 'X read-out' in page.locator('.modal-overlay').inner_text()
    page.locator('.modal-overlay button:has-text("Done")').click()
    page.locator('.reg-cash-strip button:has-text("End Day")').click()
    page.locator('.modal-overlay input[type="number"]').fill('990')
    page.locator('.modal-overlay button:has-text("Close day")').click()
    page.locator('.modal-overlay button:has-text("Done")').wait_for(timeout=6000)
    assert 'Z report' in page.locator('.modal-overlay').inner_text()
    page.locator('.modal-overlay button:has-text("Done")').click()
    page.locator('.reg-cash-strip button:has-text("Start Day")').wait_for(timeout=6000)
    assert page.evaluate('window.__operations')==['open','close']
    page.locator('#nav a[data-view="cash"]').click()
    page.locator('#main h2:has-text("Cash Drawer")').wait_for(timeout=6000)
    assert 'RECENT DAYS' in page.locator('#main').inner_text()
    assert not errors,errors
    print('PASS: Register cash status; Start Day; cash balance; X; End Day; Z; drawer page and history; no JS errors')
    browser.close()
