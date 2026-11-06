"""Isolated real Chromium UI check for barcode-scan multi-MRP selection."""
from pathlib import Path
import base64
import re
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
JS=ROOT/'pos/static/js'
CACHE={}

def module_uri(path):
    path=path.resolve()
    if path in CACHE: return CACHE[path]
    src=path.read_text()
    def repl(m):
        dep=(path.parent/m.group(2)).resolve()
        assert dep.is_relative_to(JS)
        return m.group(1)+module_uri(dep)+m.group(3)
    src=re.sub(r'((?:from\s+|import\s*)["\'])(\.{1,2}/[^"\']+)(["\'])',repl,src)
    u='data:text/javascript;base64,'+base64.b64encode(src.encode()).decode()
    CACHE[path]=u
    return u

items=[]
for i,price in enumerate([399,429,449],start=1):
    items.append(dict(id=i, group_id=1,group_name='UL04',SKU=f'EAN-001-MRP{price}',
          barcode='8901326156094',price=price,quantity=1,cost_price=0,tax_rate=5,
          attributes=[{'name':'Colour','value':'ICRSH'},{'name':'Size','value':'M'}],
          reorder_point=0,brand_name='',category_name='',category_id=None))
groups=[{'id':1,'name':'UL04','brand_id':None,'category_id':None,
         'brand_name':'','category_name':'','product_count':3,'total_stock':3}]
settings={'store_name':'Test','currency':'₹','price_tax_mode':'inclusive','low_stock_threshold':'1'}
html=(ROOT/'pos/templates/index.html').read_text().replace(
    '<script type="module" src="/static/js/app.js"></script>','').replace(
    '<link rel="stylesheet" href="/static/css/app.css">',
    '<style>'+(ROOT/'pos/static/css/app.css').read_text()+'</style>')

with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1280,'height':850})
    errors=[];page.on('pageerror',lambda err:errors.append(str(err)))
    page.set_content(html,wait_until='domcontentloaded')
    page.evaluate('''() => {const kv=new Map();Object.defineProperty(window,'localStorage',
      {configurable:true,value:{getItem:k=>kv.get(k)||null,
      setItem:(k,v)=>kv.set(k,String(v)),removeItem:k=>kv.delete(k)}})}''')
    page.evaluate('''({items,groups,settings})=>{
       window.fetch=async(url,opts={})=>{
         const path=String(url),params=new URL(path,'http://example.test').searchParams;
         let data;
         if(path==='/api/settings')data=settings;
         else if(path==='/api/products')data=items;
         else if(path==='/api/groups')data=groups;
         else if(path==='/api/categories')data=[];
         else if(path==='/api/parked')data=[];
         else if(path==='/api/cash-sessions/current')data=null;
         else if(path.startsWith('/api/lookup?')){
           const code=params.get('code');
           if(code==='8901326156094')data={barcode:code,matches:items};
           else data=items.find(p=>p.SKU===code);
         }
         else return {ok:false,status:404,json:async()=>({error:'Mock route '+path})};
         return {ok:true,status:200,json:async()=>data};
       };
     }''',{'items':items,'groups':groups,'settings':settings})
    page.evaluate('uri => import(uri)',module_uri(JS/'app.js'))
    page.locator('#reg-search').wait_for()
    search=page.locator('#reg-search')
    search.fill('8901326156094');search.press('Enter')
    page.get_by_text('Choose marked price').wait_for()
    assert page.locator('.modal-overlay .variant-row').count()==3
    assert '₹399.00' in page.locator('.modal-overlay').inner_text()
    assert '₹429.00' in page.locator('.modal-overlay').inner_text()
    assert '₹449.00' in page.locator('.modal-overlay').inner_text()
    page.locator('.modal-overlay .variant-row').nth(1).click()
    assert page.locator('.cart-line').count()==1
    assert '₹429.00' in page.locator('.cart-line').inner_text()
    search.fill('8901326156094');search.press('Enter')
    page.locator('.modal-overlay .variant-row').nth(0).click()
    assert page.locator('.cart-line').count()==2
    assert '₹399.00' in page.locator('.cart-lines').inner_text()
    assert not errors,errors
    print('PASS: scanner presents 3 marked prices, choosing one inserts exact SKU and price; can add another edition; no JS errors')
    browser.close()
