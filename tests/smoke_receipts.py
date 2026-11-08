"""Optional UI smoke test: python tests/smoke_receipts.py; Chromium, mock receipt only."""
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
    text = path.read_text()
    def link(match):
        target = (path.parent / match.group(2)).resolve()
        assert target.is_relative_to(jsroot)
        return match.group(1) + module_data(target) + match.group(3)
    text = re.sub(r'((?:from\s+|import\s*)["\'])(\.{1,2}/[^"\']+)(["\'])', link, text)
    uri = 'data:text/javascript;base64,' + base64.b64encode(text.encode()).decode()
    cache[path] = uri
    return uri

errors=[]
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
    page = browser.new_page(viewport={'width':420,'height':850})
    page.on('pageerror', lambda err: errors.append(str(err)))
    page.set_content('<div id="root"></div><style>' + (root/'static/css/app.css').read_text() + '</style>')
    receipt = {
        'id':3, 'invoice_number':'26-27/000003', 'status':'COMPLETED',
        'timestamp':'2026-11-05 09:00:00', 'subtotal':1499,
        'discount_total':74.95, 'tax_total':0,'total':1424.05,
        'tax_review_warning':True,
        'payments':[{'method':'CASH','amount':1424.05}],
        'items':[{'receipt_name':'Clovia Lounge set','receipt_variant':'Colour: Blue Print, Size: FS',
                  'name':'Clovia Lounge set (Colour: Blue Print, Size: FS)',
                  'quantity':1,'unit_price':1499,'line_total':1499,'tax_rate':0,'unit_code':'NOS'}],
        'loyalty_points_earned':14,
        'invoice':{'store_name':'Sample Store','currency':'₹','price_tax_mode':'exclusive',
                   'receipt_heading':'Retail receipt'}
    }
    page.evaluate('''async ({uri, receipt}) => {
      const m=await import(uri);
      document.querySelector('#root').append(m.buildReceipt(receipt));
    }''', {'uri':module_data(jsroot/'receipt.js'), 'receipt':receipt})
    rendered = page.locator('.receipt').inner_text()
    assert 'Clovia Lounge set' in rendered
    assert 'Colour: Blue Print, Size: FS' in rendered
    assert 'Cost Review' not in rendered and 'Source Row' not in rendered
    assert '₹1,424.05' in rendered
    assert 'unverified GST rate' in rendered
    refund = dict(receipt, status='REFUNDED', id=4, parent_sale_id=3,
                  refund_number='26-27/R000001-000003',
                  parent_invoice_number='26-27/000003', tax_review_warning=False,
                  payments=[{'method':'CASH','amount':1424.05}], loyalty_points_earned=0,
                  loyalty_points_reversed=14)
    page.evaluate('''async ({uri, receipt}) => {
      const m=await import(uri);
      document.querySelector('#root').replaceChildren(m.buildReceipt(receipt));
    }''', {'uri':module_data(jsroot/'receipt.js'), 'receipt':refund})
    rendered = page.locator('.receipt').inner_text()
    assert 'TOTAL REFUNDED' in rendered
    assert '26-27/000003' in rendered
    assert 'Refund: 26-27/R000001-000003' in rendered
    assert 'Refund via Cash' in rendered
    assert 'Loyalty points reversed' in rendered
    assert not errors, errors
    print('PASS: clean thermal receipt; historic GST warning; refund reference, amount, tender, points; no browser JS errors')
    browser.close()
