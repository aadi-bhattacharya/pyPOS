"""Chromium smoke test: thermal and A4 receipt formatting; no backend/database writes.

Run from application root: python tests/smoke_receipt_layout.py
Requires Playwright and a Chromium executable. Creates no data records.
"""
from pathlib import Path
import base64
import re
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1] / 'pos'
jsroot = root / 'static/js'
cache = {}

def module_data(path):
    path = path.resolve()
    if path in cache:
        return cache[path]
    source = path.read_text()
    def link(match):
        child = (path.parent / match.group(2)).resolve()
        assert child.is_relative_to(jsroot)
        return match.group(1) + module_data(child) + match.group(3)
    source = re.sub(r'((?:from\s+|import\s*)["\'])(\.{1,2}/[^"\']+)(["\'])', link, source)
    cache[path] = 'data:text/javascript;base64,' + base64.b64encode(source.encode()).decode()
    return cache[path]

receipt = {
    'id': 83,
    'invoice_number': '26-27/000083',
    'customer_id': 17,
    'customer_name': 'PRIVATE CUSTOMER NAME',
    'status': 'COMPLETED',
    'timestamp': '2026-11-05 09:12:00',
    'subtotal': 6442.0,
    'discount_total': 0,
    'tax_total': 0,
    'total': 6442.0,
    'payments': [{'method': 'CASH', 'amount': 6442.0}],
    'items': [
        {'receipt_name': 'Bedroom Nighty', 'receipt_variant': 'Colour: White Print, Size: FS', 'quantity': 4, 'unit_code': 'NOS', 'unit_price': 1299.0, 'line_total': 5196.0, 'tax_rate': 0},
        {'receipt_name': 'VH Crew Neck Full Sleeve T-shirt', 'receipt_variant': 'Colour: Grey Melange, Size: S', 'quantity': 1, 'unit_code': 'NOS', 'unit_price': 699.0, 'line_total': 699.0, 'tax_rate': 0},
        {'receipt_name': 'Clovia Lounge Set', 'receipt_variant': 'Size: FS', 'quantity': 1, 'unit_code': 'NOS', 'unit_price': 547.0, 'line_total': 547.0, 'tax_rate': 0},
    ],
    'invoice': {'store_name': 'Cotton Curves', 'currency': '₹', 'price_tax_mode': 'exclusive', 'receipt_heading': 'Retail Receipt', 'customer_name': 'DO NOT PRINT THIS NAME'},
}
errors = []
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 900, 'height': 1300}, device_scale_factor=1)
    page.on('pageerror', lambda err: errors.append(str(err)))
    page.set_content('<div id="print-root" data-paper="80mm"></div><style>' + (root/'static/css/app.css').read_text() + '</style>')
    page.evaluate('''async ({uri, receipt}) => {
      const {buildReceipt} = await import(uri);
      document.querySelector('#print-root').append(buildReceipt(receipt));
    }''', {'uri': module_data(jsroot/'receipt.js'), 'receipt': receipt})
    output = page.locator('.receipt').inner_text()
    assert page.locator('.receipt .r-row').filter(has_text='Customer ID').inner_text().replace('\n','').replace(' ','') == 'CustomerID17', output
    assert 'PRIVATE CUSTOMER NAME' not in output and 'DO NOT PRINT THIS NAME' not in output
    assert 'Subtotal (before' not in output and 'Subtotal (GST' not in output
    assert 'Subtotal' in output
    assert '₹5,196.00' in output
    assert '₹1,299.00' in output
    assert len(page.locator('.r-item-main').all()) == len(receipt['items'])
    assert len(page.locator('.r-item-detail').all()) == len(receipt['items'])
    assert page.locator('.r-item-main').first.locator('td').count() == 3
    assert page.locator('.r-item-detail').first.locator('.r-item-line-total').inner_text() == 'Total ₹5,196.00'

    # Test real CSS in print emulation, including 58 mm and 80 mm thermal widths.
    page.emulate_media(media='print')
    for paper in ['58mm', '80mm', 'A4']:
        page.locator('#print-root').evaluate('(el, paper) => el.dataset.paper = paper', paper)
        result = page.evaluate('''() => {
          const receipt = document.querySelector('.receipt');
          const date = document.querySelector('.r-datetime');
          const rightmost = Math.max(...[...receipt.querySelectorAll('.r-items td, .r-items th, .r-datetime')]
                  .map(e => e.getBoundingClientRect().right));
          const rect = receipt.getBoundingClientRect();
          const dt = date.getBoundingClientRect();
          const sty = getComputedStyle(date);
          return {width: rect.width, scrollWidth: receipt.scrollWidth, clientWidth: receipt.clientWidth,
            dateHeight: dt.height, dateLineHeight: parseFloat(sty.lineHeight),
            dateText: date.innerText, rightmost: rightmost, rightEdge: rect.right};
        }''')
        assert 'pm' in result['dateText'].lower() or 'am' in result['dateText'].lower(), result
        assert result['dateHeight'] <= result['dateLineHeight'] + 1.5, result
        assert result['scrollWidth'] <= result['clientWidth'] + 2, (paper, result)
        assert result['rightmost'] <= result['rightEdge'] + 2, (paper, result)
        print('PASS:', paper, result)
        if paper != 'A4':
            page.locator('.receipt').screenshot(path=str(Path('/mnt/data/pypos_receipt_layout_work') / ('preview_' + paper + '.png')))
    assert not errors, errors
    # Same renderer on partial refund; original sale ID and amounts retained.
    refund = dict(receipt, id=84, invoice_number=None, status='REFUNDED', parent_sale_id=83,
                  parent_invoice_number='26-27/000083')
    page.evaluate('''async ({uri, receipt}) => {
      const {buildReceipt} = await import(uri);
      document.querySelector('#print-root').replaceChildren(buildReceipt(receipt));
    }''', {'uri': module_data(jsroot/'receipt.js'), 'receipt': refund})
    t = page.locator('.receipt').inner_text()
    assert 'refund receipt' in t.lower() and 'TOTAL REFUNDED' in t, repr(t)
    assert '26-27/000083' in t and page.locator('.receipt .r-row').filter(has_text='Customer ID').count() == 1
    assert not errors, errors
    print('PASS: customer ID, per-item totals, receipt date, subtotals, refund print formatting, no JS errors')
    browser.close()
