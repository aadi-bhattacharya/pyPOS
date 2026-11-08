"""Offline Chromium UI smoke: cashier creation, safe imports, no decrement shortcuts.

Run: python tests/smoke_stock_security_ui.py
"""
import base64
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1] / "pos"
JS = ROOT / "static/js"
cache = {}

def module_data(path):
    path = path.resolve()
    if path in cache:
        return cache[path]
    content = path.read_text()
    def link(match):
        next_path = (path.parent / match.group(2)).resolve()
        assert next_path.is_relative_to(JS)
        return match.group(1) + module_data(next_path) + match.group(3)
    content = re.sub(r'((?:from\s+|import\s*)["\'])(\.{1,2}/[^"\']+)(["\'])', link, content)
    uri = "data:text/javascript;base64," + base64.b64encode(content.encode()).decode()
    cache[path] = uri
    return uri

html = (ROOT / "templates/index.html").read_text().replace(
    '<script type="module" src="/static/js/app.js"></script>', '').replace(
    '<link rel="stylesheet" href="/static/css/app.css">',
    '<style>' + (ROOT / "static/css/app.css").read_text() + '</style>')

product = {"id": 1, "group_id": 1, "group_name": "Coats", "SKU": "COAT-L",
           "barcode": "8901112223334", "price": 1499, "cost_price": 799,
           "quantity": 5, "tax_rate": 12, "reorder_point": 2, "attributes": [],
           "hsn_code": "", "unit_code": "NOS"}
group = {"id": 1, "name": "Coats", "product_count": 1, "total_stock": 5}

with sync_playwright() as browser_api:
    browser = browser_api.chromium.launch(headless=True,
        executable_path="/usr/bin/chromium", args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1360, "height": 780})
    page.set_default_timeout(3000)
    errors=[]
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.set_content(html, wait_until="domcontentloaded")
    page.evaluate('''(data) => {
      window.__writes=[];window.__creates=[];
      window.fetch=async (url,options={}) => {
        const endpoint=String(url);
        let response;
        if(endpoint==='/api/products' && !options.method) response=data.products;
        else if(endpoint==='/api/groups' && options.method==='POST') {
          const payload=JSON.parse(options.body);window.__creates.push({kind:'group',payload});
          const id=data.groups.length+1;data.groups.push({id,name:payload.name,product_count:0,total_stock:0});
          response={id,name:payload.name};
        }
        else if(endpoint==='/api/groups') response=data.groups;
        else if(endpoint==='/api/products' && options.method==='POST') {
          const payload=JSON.parse(options.body);window.__creates.push({kind:'product',payload});
          const new_product={...payload,id:data.products.length+1,group_name:'Coats',cost_price:null,
              attributes:Object.entries(payload.attributes||{}).map(([name,value])=>({name,value}))};
          data.products.push(new_product);response=new_product;
        }
        else if(endpoint.startsWith('/api/products/1/stock')) {
          const payload=JSON.parse(options.body);
          window.__writes.push(payload);
          response={id:1,quantity:5+payload.delta};
        }
        else return {ok:false,status:404,json:async()=>({error:'Unmocked '+endpoint})};
        return {ok:true,status:200,json:async()=>response};
      };
    }''', {"products": [product], "groups": [group]})
    api_uri = module_data(JS/"api.js")
    catalog_uri = module_data(JS/"views/catalog.js")
    page.evaluate('''async(uri)=>{const {state}=await import(uri);
      state.staff={enabled:true,operator:{username:'counter1',role:'cashier'}};
      state.settings={currency:'₹',low_stock_threshold:'2',cashier_allow_stock_reduction:'false',
        cashier_allow_stock_import:'true',cashier_max_discount_percent:'0'};
    }''', api_uri)
    page.evaluate('''async(uri)=>{const {renderCatalog}=await import(uri);
      await renderCatalog(document.getElementById('main'));}''',catalog_uri)
    assert page.get_by_title('Remove 1').count()==0
    assert page.get_by_title('Edit').count()==0
    assert page.get_by_title('Delete').count()==0
    assert page.get_by_text('New group', exact=True).count()==1
    assert page.get_by_text('Add product', exact=True).count()==1
    assert page.get_by_text('Export CSV', exact=True).count()==0
    assert page.locator('td.num.cell-sub').first.inner_text()=='—'
    page.get_by_title('Record a stock receipt / correction').click()
    modal=page.locator('.modal-overlay')
    assert modal.get_by_text('Documented damage / shortage correction', exact=True).count()==0
    assert modal.get_by_text('-1',exact=True).count()==0
    assert modal.get_by_text('-5',exact=True).count()==0
    assert modal.get_by_text('-10',exact=True).count()==0
    qty=modal.locator('input[type=number]')
    assert qty.get_attribute('min')=='1'
    qty.fill('-1')
    modal.locator('input[type=text]').fill('Supplier Invoice 129')
    modal.get_by_text('Save stock movement').click()
    assert page.evaluate('window.__writes.length')==0
    qty.fill('2')
    modal.get_by_text('Save stock movement').click()
    page.wait_for_timeout(150)
    assert page.evaluate('window.__writes.length')==1
    assert page.evaluate('window.__writes[0].delta')==2
    assert page.evaluate('window.__writes[0].reason')=='Supplier Invoice 129'
    page.get_by_text('Import / Receive stock').click()
    assert page.locator('.modal-overlay select option').all_text_contents()==[
        'Create new products, skip existing SKUs (safest)',
        'Create new products + receive stock into existing SKUs']
    page.get_by_text('Cancel',exact=True).last.click()
    page.get_by_text('Add product',exact=True).click()
    modal=page.locator('.modal-overlay')
    assert modal.get_by_text('Cost',exact=True).count()==0
    def fill_labeled(label, value):
        modal.locator('label',has_text=label).first.locator('..').locator('input').first.fill(value)
    modal.locator('select').first.select_option('1')
    fill_labeled('SKU *','NEW-CASHIER')
    fill_labeled('Barcode','NEWEAN-10')
    fill_labeled('Price *','199')
    fill_labeled('GST rate %','5')
    fill_labeled('Opening stock','3')
    modal.locator('.attr-row input').nth(0).fill('Size')
    modal.locator('.attr-row input').nth(1).fill('XL')
    modal.get_by_text('Create',exact=True).evaluate('(b)=>b.click()')
    page.wait_for_timeout(140)
    assert page.evaluate('window.__creates.length')==1,page.evaluate('window.__creates')
    submitted=page.evaluate('window.__creates[0].payload')
    assert submitted['SKU']=='NEW-CASHIER'
    assert submitted['quantity']==3
    assert submitted['attributes']['Size']=='XL'
    assert not 'cost_price' in submitted
    page.get_by_text('New group',exact=True).click()
    modal=page.locator('.modal-overlay')
    modal.locator('input').fill('New arrivals')
    modal.get_by_text('Create',exact=True).evaluate('(b)=>b.click()')
    page.wait_for_timeout(140)
    assert page.evaluate('window.__creates.length')==2
    assert page.evaluate('window.__creates[1].payload.name')=='New arrivals'
    # Re-render as manager: changes use a documented form, not the removed -1 stepper.
    page.evaluate('''async(uri)=>{const {state}=await import(uri);state.staff.operator.role='manager';}''',api_uri)
    page.evaluate('''async(uri)=>{const {renderCatalog}=await import(uri);
      await renderCatalog(document.getElementById('main'));}''',catalog_uri)
    assert page.get_by_title('Edit').count()>0
    assert page.get_by_title('Remove 1').count()==0
    page.get_by_title('Record a stock receipt / correction').first.click()
    modal=page.locator('.modal-overlay')
    assert modal.locator('input[type=number]').get_attribute('min')=='1'
    assert modal.locator('.chip').all_text_contents()==['+1','+5','+10']
    assert modal.locator('select option').all_text_contents()==[
        'Receive goods (add stock)','Documented damage / shortage correction']
    modal.locator('select').select_option('ADJUSTMENT')
    modal.locator('input[type=number]').fill('-1')
    modal.locator('input[type=text]').fill('Damaged delivery item')
    modal.get_by_text('Save stock movement',exact=True).evaluate('(b)=>b.click()')
    assert page.evaluate('window.__writes.length')==1
    assert not errors, errors
    print('PASS: cashier has Add Product and safe imports without stock reduction, editing or cost-price access')
    browser.close()
