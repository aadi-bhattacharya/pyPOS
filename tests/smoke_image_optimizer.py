"""Headless Chromium smoke: real Canvas encode, alpha, logo settings upload."""
import base64
import io
import re
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / 'pos/static/js'
CACHE = {}


def module_uri(path):
    path = path.resolve()
    if path in CACHE:
        return CACHE[path]
    source = path.read_text()
    def repl(match):
        dependency = (path.parent / match.group(2)).resolve()
        assert dependency.is_relative_to(JS)
        return match.group(1) + module_uri(dependency) + match.group(3)
    source = re.sub(r'((?:from\s+|import\s*)["\'])(\.{1,2}/[^"\']+)(["\'])', repl, source)
    uri = 'data:text/javascript;base64,' + base64.b64encode(source.encode()).decode()
    CACHE[path] = uri
    return uri


def image_bytes(mode, fmt, size=(1200, 900)):
    # Deterministic, not merely a solid-colour image that trivially compresses.
    import random
    rng = random.Random(1729)
    w, h = size
    if mode == 'RGBA':
        payload = bytearray(rng.randbytes(w * h * 4))
        for i in range(3, len(payload), 4): payload[i] = 0 if (i // 4) % 7 == 0 else 255
    else:
        payload = rng.randbytes(w * h * 3)
    pic = Image.frombytes(mode, size, bytes(payload))
    buffer = io.BytesIO()
    pic.save(buffer, format=fmt, quality=96)
    return buffer.getvalue()


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 1150, 'height': 950})
    failures = []
    page.on('pageerror', lambda error: failures.append(str(error)))
    page.set_content('<!doctype html><html><body><input type="file" id="img"><div id="toast-root"></div><main id="main"></main></body></html>')
    uri = module_uri(JS / 'imageOptimizer.js')
    page.evaluate('uri => {window.imageOps = import(uri)}', uri)
    page.locator('#img').set_input_files({'name':'logo.png','mimeType':'image/png',
                                           'buffer':image_bytes('RGBA','PNG')})
    output = page.evaluate('''async () => {
      const mod = await window.imageOps;
      const file = document.getElementById('img').files[0];
      const result = await mod.optimizeImage(file, {maxEdge:384,maxBytes:200*1024});
      const bitmap = await createImageBitmap(result.blob);
      const canvas = document.createElement('canvas');
      canvas.width=bitmap.width; canvas.height=bitmap.height;
      const ctx=canvas.getContext('2d');ctx.drawImage(bitmap,0,0);
      const alpha=ctx.getImageData(0,0,bitmap.width,bitmap.height).data;
      let minAlpha=255; for(let i=3;i<alpha.length;i+=4)minAlpha=Math.min(minAlpha,alpha[i]);
      const url=await mod.asDataUrl(result.blob);
      return {size:result.blob.size,mime:result.mime,width:result.width,height:result.height,
         minAlpha, dataUrlValid:url.startsWith('data:image/'),rawSize:file.size};
    }''')
    assert output['size'] <= 200*1024, output
    assert max(output['width'], output['height']) <= 384, output
    assert output['minAlpha'] < 255 and output['mime'] in ('image/png','image/webp'), output
    assert output['dataUrlValid'] and output['rawSize'] > output['size']

    page.locator('#img').set_input_files({'name':'photo.jpg','mimeType':'image/jpeg',
                                           'buffer':image_bytes('RGB','JPEG')})
    out2 = page.evaluate('''async () => {
      const mod = await window.imageOps;
      const r = await mod.optimizeImage(document.getElementById('img').files[0],
        {maxEdge:512,maxBytes:200*1024});
      return {size:r.blob.size,mime:r.mime,width:r.width,height:r.height};
    }''')
    assert out2['size'] <= 200*1024 and max(out2['width'],out2['height']) <= 512, out2

    rejected = page.evaluate('''async () => {
      const mod=await window.imageOps;
      try {await mod.optimizeImage(new File(['malicious'], 'script.svg', {type:'image/svg+xml'}));}
      catch(e) {return e.message}
    }''')
    assert 'PNG, JPEG or WebP' in rejected
    # Verify that a genuinely small logo isn't pointlessly recompressed.
    tiny = Image.new('RGBA', (80, 45), (100, 80, 140, 0))
    buffer = io.BytesIO(); tiny.save(buffer, format='PNG')
    page.locator('#img').set_input_files({'name':'tiny.png','mimeType':'image/png', 'buffer':buffer.getvalue()})
    small = page.evaluate('''async () => {
      const mod=await window.imageOps;
      const f=document.getElementById('img').files[0];
      const r=await mod.optimizeImage(f, {maxEdge:384,maxBytes:200*1024});
      return {same:r.blob===f,changed:r.changed,size:r.blob.size};
    }''')
    assert small['same'] and not small['changed'] and small['size']==len(buffer.getvalue())
    # A forced JPEG export flattens transparency onto white.
    converted = page.evaluate('''async () => {
      const mod=await window.imageOps;
      const f=document.getElementById('img').files[0];
      const r=await mod.optimizeImage(f, {maxEdge:384,maxBytes:200*1024,
        format:'image/jpeg',keepSmallOriginal:false});
      const bitmap=await createImageBitmap(r.blob);
      const canvas=document.createElement('canvas');canvas.width=bitmap.width;canvas.height=bitmap.height;
      const ctx=canvas.getContext('2d');ctx.drawImage(bitmap,0,0);
      return {mime:r.mime,alpha:ctx.getImageData(0,0,1,1).data[3]};
    }''')
    assert converted['mime']=='image/jpeg' and converted['alpha']==255

    print('PASS: transparent oversized PNG and high-resolution JPEG compress below 200 KiB, dimensions reduced, invalid types rejected')

    settings_uri = module_uri(JS / 'views/settingsview.js')
    settings = {"store_name":"Test Shop","pos_display_name":"Test Shop",
                "pos_logo_mode":"default","pos_logo_size":"28","pos_accent_color":"#A52A4A",
                "show_receipt_logo":"true","receipt_paper":"80mm","store_logo":"",
                "tax_breakup":"false","price_tax_mode":"inclusive"}
    page.evaluate('''s=>{window.savedSettings=s;window.savedRequests=[];
      window.fetch=async(url,opts={})=>{
        const u=String(url); let body={};
        if(u==='/api/settings/auth')body={configured:false,authenticated:true};
        else if(u==='/api/settings' && opts.method==='PUT'){
          const val=JSON.parse(opts.body);window.savedRequests.push(val);
          Object.assign(window.savedSettings,val);body=window.savedSettings;
        }else if(u==='/api/settings')body=window.savedSettings;
        else if(u.startsWith('/api/products'))body=[];
        else if(u.startsWith('/api/backups'))body={gdrive:{connected:false},local:{}};
        else body=[];
        return {ok:true,status:200,json:async()=>body};
      };
    }''', settings)
    page.evaluate('uri=>{window.settingsMod=import(uri);return window.settingsMod.then(m=>m.renderSettingsView(document.getElementById("main")))}',settings_uri)
    assert page.get_by_text('Image downscaler & compressor').count() == 1
    assert page.get_by_text('Upload menu logo (auto-compressed to 200 KiB)').count() == 1
    assert page.get_by_text('Upload receipt logo (auto-compressed to 200 KiB)').count() == 1
    menu_file=page.get_by_label('Upload separate POS logo')
    menu_file.set_input_files({'name':'huge-menu.png','mimeType':'image/png',
                              'buffer':image_bytes('RGBA','PNG')})
    page.get_by_text('Save POS branding',exact=True).click()
    page.wait_for_function('window.savedRequests.some(r=>!!r.pos_logo)')
    store = page.evaluate('window.savedRequests.find(r=>r.pos_logo).pos_logo')
    assert store.startswith('data:image/'), store[:30]
    assert len(base64.b64decode(store.split(',',1)[1])) <= 200 * 1024
    assert page.get_by_text('Image downscaler & compressor').count() == 1
    logo_input=page.get_by_label('Upload receipt logo')
    logo_input.set_input_files({'name':'large-receipt.jpg','mimeType':'image/jpeg',
                               'buffer':image_bytes('RGB','JPEG')})
    page.get_by_text('Save print settings',exact=True).click()
    page.wait_for_function('window.savedRequests.some(r=>!!r.store_logo)')
    receipt = page.evaluate('window.savedRequests.find(r=>r.store_logo).store_logo')
    assert len(base64.b64decode(receipt.split(',',1)[1])) <= 200 * 1024
    batch = page.get_by_label('Choose images to optimise')
    batch.set_input_files([
      {'name': 'sample-one.jpg', 'mimeType': 'image/jpeg', 'buffer': image_bytes('RGB', 'JPEG', size=(800, 600))},
      {'name': 'sample-two.png', 'mimeType': 'image/png', 'buffer': image_bytes('RGBA', 'PNG', size=(700, 500))},
    ])
    page.get_by_role('button', name='Optimise selected images').click()
    page.wait_for_function("document.querySelectorAll('.image-opt-result').length === 2")
    assert page.locator('.image-opt-result button').count() == 2
    assert 'sample-one.jpg' in page.locator('.image-opt-results').inner_text()
    assert 'sample-two.png' in page.locator('.image-opt-results').inner_text()
    print('PASS: two-image local batch optimiser produces downloadable outputs')
    assert not failures, failures
    print('PASS: Settings page uploads and saves compressed menu and receipt logos; controls render; no browser errors')
    browser.close()
