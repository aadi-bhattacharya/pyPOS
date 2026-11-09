"""Browser-only smoke: automatic printer jobs don't invoke window.print.

No live server, sale, printer or database. Requires Playwright/Chromium.
"""
from pathlib import Path
import base64
import re
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1] / 'pos' / 'static' / 'js'
cache = {}


def module_data(path):
    path = path.resolve()
    if path in cache: return cache[path]
    source = path.read_text(encoding='utf-8')
    def link(match):
        dep = (path.parent/match.group(2)).resolve()
        assert dep.is_relative_to(ROOT)
        return match.group(1)+module_data(dep)+match.group(3)
    source = re.sub(r'((?:from\s+|import\s*)["\'])(\.{1,2}/[^"\']+)(["\'])',link,source)
    cache[path] = 'data:text/javascript;base64,' + base64.b64encode(source.encode()).decode()
    return cache[path]

with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page()
    page.set_content('<div id="print-root"></div><div id="toast-root"></div>')
    out=page.evaluate('''async ({apimod,uimod}) => {
       const {state,api} = await import(apimod);
       const {printReceipt} = await import(uimod);
       const calls = [];
       let dialogs = 0;
       window.print=()=>{dialogs++};
       api.printRecordedSale=async (id,manual)=>{
         calls.push({id,manual}); return {status:'spooled',printer:'TM-T81'};
       };
       const receipt = document.createElement('div');receipt.textContent='fake DOM only';
       state.settings={receipt_print_mode:'thermal',receipt_auto_print:'true',receipt_paper:'80mm'};
       await printReceipt(receipt,{id:42},{automatic:true});
       await printReceipt(receipt,{id:42});
       const noDialogThermal=dialogs===0;
       state.settings.receipt_print_mode='browser';
       await printReceipt(receipt,{id:42},{automatic:true});
       const noAutoDialog=dialogs===0;
       await printReceipt(receipt,{id:42});
       return {calls, noDialogThermal,noAutoDialog,dialogs};
    }''', {'apimod':module_data(ROOT/'api.js'),'uimod':module_data(ROOT/'ui.js')})
    assert out['calls'] == [{'id':42,'manual':False},{'id':42,'manual':True}],out
    assert out['noDialogThermal'] and out['noAutoDialog'],out
    assert out['dialogs']==1,out
    print('PASS: direct auto + manual receipts, no browser dialogs; explicit fallback only')
    browser.close()
