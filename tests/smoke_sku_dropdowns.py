"""Browser smoke for SKU/MRP dropdowns; runs without live Flask or business data.

Run: python tests/smoke_sku_dropdowns.py
"""
from pathlib import Path
import re
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT/'pos/static/js'
CSS = (ROOT/'pos/static/css/app.css').read_text()
INDEX = (ROOT/'pos/templates/index.html').read_text()
INDEX = re.sub(r'<link rel="stylesheet" href="/static/css/app.css">', '', INDEX)
INDEX = re.sub(r'<script type="module" src="/static/js/app.js"></script>', '', INDEX)

BUILD_JS = r'''({pickerSource, stocktakeSource, purchasingSource}) => {
  function h(tag, attrs={}, ...children) {
    const el=document.createElement(tag);
    for(const [k,v] of Object.entries(attrs || {})) {
      if(v===null||v===undefined||v===false) continue;
      if(k==='class') el.className=v;
      else if(k.startsWith('on')&&typeof v==='function') el.addEventListener(k.slice(2).toLowerCase(),v);
      else if(v===true) el.setAttribute(k,'');
      else el.setAttribute(k,v);
    }
    for(const child of children.flat(Infinity)) {
      if(child===null||child===undefined||child===false) continue;
      el.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return el;
  }
  const fmt = n => '₹'+Number(n).toFixed(2);
  const publicVariant = attrs => attrs.filter(a=>['colour','size'].includes(a.name.toLowerCase()))
    .map(a=>a.name+': '+a.value).join(' · ');
  const products = [
    {id:1,SKU:'TEST-OLD-34B',group_name:'Cotton Bra',barcode:'8901326190401',price:399,quantity:3,attributes:[{name:'Colour',value:'Black'},{name:'Size',value:'34B'}]},
    {id:2,SKU:'TEST-NEW-34B',group_name:'Cotton Bra',barcode:'8901326190401',price:449,quantity:8,attributes:[{name:'Colour',value:'Black'},{name:'Size',value:'34B'}]},
    {id:3,SKU:'TEST-WHITE-36B',group_name:'Cotton Bra',barcode:'8901326190555',price:599,quantity:4,attributes:[{name:'Colour',value:'White'},{name:'Size',value:'36B'}]},
  ];
  const transform = src => src.replace(/^import .*;\s*$/gm,'')
    .replace(/\bexport (async function|function|const|let) /g,'$1 ');
  const createSkuPicker = new Function('fmt','publicVariant','h',
    transform(pickerSource)+';return createSkuPicker;')(fmt,publicVariant,h);
  const calls=[];
  const api={
    stocktakes:async()=>[{id:12,status:'DRAFT',note:'Shelf A',counted_skus:0},{id:11,status:'POSTED',note:'October',counted_skus:8}],
    stocktakeDetail:async(id)=>({id,status:id===12?'DRAFT':'POSTED',lines:[]}),
    listProducts:async()=>products,
    countStock:async(id,pid,counted)=>{calls.push(['count',id,pid,counted]);},
    startStocktake:async()=>({id:13}),
    suppliers:async()=>[{id:8,name:'Demo supplier'}],purchases:async()=>[],
    receivePurchase:async(body)=>{calls.push(['receive',body]);}
  };
  const toast=(message)=>calls.push(['toast',message]);
  const toastError=(e)=>calls.push(['error',e.message]);
  const openModal=({title,body,footer=[]})=>{
    const el=h('div',{class:'modal-overlay'},h('div',{class:'modal wide'},
      h('div',{class:'modal-head'},title),h('div',{class:'modal-body'},body),
      h('div',{class:'modal-foot'},...footer)));
    document.getElementById('modal-root').replaceChildren(el);
    return {close:()=>el.remove()};
  };
  const confirmDialog=async()=>true;
  const stocktakeFn = new Function('api','h','openModal','toast','toastError','confirmDialog',
      'createSkuPicker', transform(stocktakeSource)+';return renderStocktake')
      (api,h,openModal,toast,toastError,confirmDialog,createSkuPicker);
  const purchasingFn = new Function('api','fmt','fmtDate','h','openModal','toast','toastError','confirmDialog',
      'createSkuPicker',transform(purchasingSource)+';return renderPurchasing')
      (api,fmt,String,h,openModal,toast,toastError,confirmDialog,createSkuPicker);
  window.__test = { h, products, createSkuPicker, calls, stocktakeFn, purchasingFn };
}'''


def interact(page):
    page.evaluate('''()=>{
      const c=window.__test.createSkuPicker(window.__test.products);
      c.element.id='test-picker';document.getElementById('main').replaceChildren(c.element);
      window.__picker=c;
    }''')
    inp=page.locator('#test-picker input')
    inp.fill('8901326190401')
    inp.press('Enter')
    assert page.evaluate('window.__picker.getSelected() === null'), 'shared EAN must never pick arbitrary MRP'
    assert page.locator('#test-picker [role="option"]').count()==2
    assert '₹399' in page.locator('#test-picker [role="option"]').first.inner_text()
    assert '₹449' in page.locator('#test-picker [role="option"]').last.inner_text()
    page.locator('#test-picker [role="option"]').last.click()
    assert page.evaluate('window.__picker.getSelected()?.id')==2
    inp.fill('TEST-OLD-34B')
    assert page.evaluate('window.__picker.getSelected() === null'), 'editing SKU must invalidate selection'
    inp.press('Enter')
    assert page.evaluate('window.__picker.getSelected()?.id')==1, 'exact SKU scanner auto-select'
    inp.fill('8901326190555');inp.press('Enter')
    assert page.evaluate('window.__picker.getSelected()?.id')==3, 'unique EAN scanner auto-select'
    inp.fill('cotton bra');inp.press('ArrowDown');inp.press('ArrowDown');inp.press('Enter')
    assert page.evaluate('window.__picker.getSelected()?.id')==2,'keyboard selection'
    inp.fill('no-match');assert 'No matching' in page.locator('#test-picker .sku-picker-menu').inner_text()


def stocktake(page):
    page.evaluate('''async()=>{document.getElementById('main').replaceChildren();await window.__test.stocktakeFn(document.getElementById('main'))}''')
    assert page.locator('select[aria-label="Choose stocktaking session"] option').count()==2
    page.locator('select[aria-label="Choose stocktaking session"]').select_option('11')
    page.wait_for_timeout(70)
    assert page.locator('#main .sku-picker').count()==0, 'posted counts are read-only'
    page.locator('select[aria-label="Choose stocktaking session"]').select_option('12')
    page.wait_for_timeout(70)
    assert page.locator('#main .sku-picker').count()==1
    picker=page.locator('#main .sku-picker')
    inp=picker.locator('input')
    inp.fill('8901326190401');inp.press('Enter')
    if page.viewport_size["width"] == 1440:
        page.screenshot(path=str(ROOT / "preview_stocktake_dropdown.png"))
    assert page.evaluate('window.__test.calls.filter(x=>x[0]==="count").length')==0
    page.get_by_placeholder('Actual units counted').fill('5')
    page.get_by_role('button',name='Save count').click()
    assert page.evaluate('window.__test.calls.filter(x=>x[0]==="count").length')==0
    picker.locator('[role="option"]').last.click()
    page.get_by_role('button',name='Save count').click()
    page.wait_for_timeout(80)
    assert page.evaluate('window.__test.calls.findLast(x=>x[0]==="count")')==['count',12,2,'5']


def purchasing(page):
    page.evaluate('''async()=>{document.getElementById('main').replaceChildren();await window.__test.purchasingFn(document.getElementById('main'))}''')
    page.get_by_role('button',name='Receive goods').first.click()
    picker=page.locator('.modal .sku-picker')
    inp=picker.locator('input')
    inp.fill('8901326190401');inp.press('Enter')
    if page.viewport_size["width"] == 1440:
        page.wait_for_timeout(180)  # let the modal entry animation complete
        page.screenshot(path=str(ROOT / "preview_purchasing_dropdown.png"))
    assert page.evaluate('window.__test.calls.filter(x=>x[0]==="receive").length')==0
    page.locator('.modal input[placeholder="Exactly as on the supplier\'s invoice"]').fill('DEMO-0001')
    page.locator('.modal input[placeholder="Landed price per unit ₹"]').fill('125.00')
    page.get_by_role('button',name='Receive stock').click()
    assert page.evaluate('window.__test.calls.filter(x=>x[0]==="receive").length')==0, 'unresolved EAN blocked'
    inp.fill('8901326190401')
    page.locator('.modal [role="option"]').first.click()
    page.get_by_role('button',name='Receive stock').click()
    page.wait_for_timeout(80)
    payload=page.evaluate('window.__test.calls.findLast(x=>x[0]==="receive")[1]')
    assert payload['lines'][0]['product_id']==1
    assert payload['supplier_id']==8
    assert payload['lines'][0]['unit_cost']=='125.00'


def main():
    sources={
      'pickerSource':(JS/'skuPicker.js').read_text(),
      'stocktakeSource':(JS/'views/stocktake.js').read_text(),
      'purchasingSource':(JS/'views/purchasing.js').read_text(),
    }
    with sync_playwright() as pw:
      browser=pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
          args=['--no-sandbox','--disable-dev-shm-usage'])
      for width in (1440,500):
        page=browser.new_page(viewport={'width':width,'height':900})
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.set_content(INDEX,wait_until='domcontentloaded')
        page.add_style_tag(content=CSS)
        page.evaluate(BUILD_JS,sources)
        interact(page);print('PASS picker',width)
        stocktake(page);print('PASS stocktake',width)
        purchasing(page);print('PASS supplier receipt',width)
        assert not errors,errors
        assert page.locator('.modal .sku-picker').count()==0, 'successful receipt closes modal'
        page.close()
      browser.close()

if __name__=='__main__': main()
