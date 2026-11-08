"""Isolated Chromium layout checks; no network, Flask or live database required."""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
INDEX=(ROOT/'pos/templates/index.html').read_text()
CSS=(ROOT/'pos/static/css/app.css').read_text()
SOURCES={v:(ROOT/f'pos/static/js/views/{v}.js').read_text() for v in ['register','purchasing','stocktake']}
STATIC_INDEX=re.sub(r'<link rel="stylesheet" href="/static/css/app.css">','',INDEX)
STATIC_INDEX=re.sub(r'<script type="module" src="/static/js/app.js"></script>','',STATIC_INDEX)

RUN_JS=r'''async ({view, source, withProducts}) => {
  window.h=(tag, attrs={}, ...children) => {
    const el=document.createElement(tag);
    for (const [k,v] of Object.entries(attrs||{})) {
      if(v===null||v===undefined||v===false) continue;
      if(k==='class') el.className=v;
      else if(k==='dataset') Object.assign(el.dataset,v);
      else if(k.startsWith('on') && typeof v==='function') el.addEventListener(k.slice(2).toLowerCase(),v);
      else if(v===true) el.setAttribute(k,'');
      else el.setAttribute(k,v);
    }
    for(const c of children.flat(Infinity)) {
      if(c===null || c===undefined || c===false) continue;
      el.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return el;
  };
  const icon=()=>h('svg',{},h('title',{},'Icon'));
  const fmt=n=>'₹'+Number(n||0).toFixed(2);
  const fmtDate=n=>String(n||'');
  const products=withProducts?[{id:1,group_id:1,group_name:'Test apparel',SKU:'TEST-1',barcode:'8901326190401',price:399,quantity:3,cost_price:0,tax_rate:5,attributes:[],reorder_point:1}]:[];
  const api={
    suppliers:async()=>[], purchases:async()=>[], stocktakes:async()=>[],
    listProducts:async()=>products, listGroups:async()=>withProducts?[{id:1,name:'Test apparel'}]:[],
    listCategories:async()=>[],listParked:async()=>[],
  };
  const state={settings:{currency:'₹',price_tax_mode:'inclusive',tax_label:'GST',low_stock_threshold:2},staff:{operator:{role:'staff',display_name:'Test operator'}}};
  const computeTotals=()=>({subtotal:0, discount:0, tax:0, total:0});
  const noop=()=>{};
  const renderCashStatusStrip=async target=>target.replaceChildren(h('div',{},'Day closed · No active cash session'));
  source=source.replace(/^import .*;\s*$/gm,'').replace(/\bexport (async function|function) /g,'$1 ');
  const argNames=['api','fmt','fmtDate','h','icon','openModal','toast','toastError',
    'confirmDialog','printReceipt','buildReceipt','methodLabel','publicVariant','publicAttributes',
    'renderCashStatusStrip','tenderPreview','state','computeTotals','can'];
  const values=[api,fmt,fmtDate,h,icon,noop,noop,noop,noop,noop,noop,noop,noop,()=>[],renderCashStatusStrip,
    noop,state,computeTotals,()=>true];
  const exportName=view==='register'?'renderRegister':view==='purchasing'?'renderPurchasing':'renderStocktake';
  const fn=new Function(...argNames,source+';return '+exportName)(...values);
  await fn(document.getElementById('main'));
  return document.getElementById('main').innerText.slice(0,300);
}'''


def run():
    # Import only when running this standalone visual smoke test. The
    # regular unittest discovery and native release builds do not need
    # Playwright or a downloaded browser.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        for width in (1440, 760, 430):
            for view in ('register','purchasing','stocktake'):
                page=browser.new_page(viewport={'width':width,'height':860})
                errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.set_content(STATIC_INDEX,wait_until='domcontentloaded')
                page.add_style_tag(content=CSS)
                text=page.evaluate(RUN_JS,{'view':view,'source':SOURCES[view],'withProducts':False})
                page.wait_for_timeout(80)
                assert not errors,(view,width,errors)
                assert text,(view,width,'empty page')
                assert page.locator('h2').count()>0,(view,width)
                if view=='register':
                    assert page.locator('.view-actions > *').count()==2,(view,width)
                    boxes=page.locator('.view-actions > *').evaluate_all('(els)=>els.map(x=>{const r=x.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}})')
                    assert boxes[1]['left']>=boxes[0]['right']-1,boxes
                    if width>=600:
                        assert boxes[1]['left']-boxes[0]['right']<=16,boxes
                    assert 'No products in the catalogue' in text
                    assert page.locator('.register-empty a[href="#/catalog"]').count()==1
                    if width < 940:
                        # The catalogue empty state must not be painted under the cart.
                        bounds=page.evaluate('''()=>({empty:document.querySelector('.register-empty').getBoundingClientRect().bottom,cart:document.querySelector('.cart-pane').getBoundingClientRect().top})''')
                        assert bounds['empty'] <= bounds['cart'] + 1, bounds
                if view=='purchasing':
                    pos=page.evaluate('''()=>{const card=document.querySelector('.section-card'); const hd=card.querySelector('.section-card-head');const empty=card.querySelector('.section-empty'); const c=card.getBoundingClientRect(); const h=hd.getBoundingClientRect(); const e=empty.getBoundingClientRect();return {card:c.left,heading:h.left,content:e.left,headerPad:getComputedStyle(hd).paddingLeft}}''')
                    assert pos['content']>pos['card']+10, pos
                    assert page.locator('.section-empty-actions .btn').count()==2
                if view=='stocktake':
                    assert page.locator('.form-grid').first.evaluate('(e)=>getComputedStyle(e).display')=='grid'
                    assert page.locator('.form-grid-actions .btn').count()==1
                if width==1440 and view in ('purchasing','register'):
                    page.screenshot(path=str(ROOT/f'layout_{view}_desktop.png'))
                if width==430 and view in ('purchasing','register'):
                    page.screenshot(path=str(ROOT/f'layout_{view}_narrow.png'))
                print('PASS',view,width)
                page.close()
        browser.close()

if __name__=='__main__':run()
