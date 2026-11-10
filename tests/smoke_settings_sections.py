"""Settings browser smoke with local ES modules and mocked API (no network).

Requires Playwright plus Chromium. Run: python tests/smoke_settings_sections.py
"""
import base64
import re
import unittest
from pathlib import Path

JS_ROOT = Path(__file__).resolve().parents[1] / "pos" / "static" / "js"


def module_url(path, cache):
    """Inline the module import graph; no live server or shop DB needed."""
    path = path.resolve()
    if path in cache:
        return cache[path]
    contents = path.read_text(encoding="utf-8")
    def change(m):
        ref = m.group(2)
        if ref.startswith("."):
            return m.group(1) + module_url(path.parent / ref, cache) + m.group(3)
        return m.group(0)
    contents = re.sub(r'(\bfrom\s+["\'])([^"\']+)(["\'])', change, contents)
    result = "data:text/javascript;base64," + base64.b64encode(contents.encode()).decode()
    cache[path] = result
    return result


BOOTSTRAP = r'''async (moduleUrl) => {
  window.saved = {store_name:'Test Shop', pos_display_name:'pyPOS',
    receipt_print_mode:'thermal', receipt_paper:'80mm',
    price_tax_mode:'inclusive', auto_updates_enabled:'true'};
  window.writes = [];
  window.fetch = async (url, opts={}) => {
    let path = String(url).replace(/^.*\/api/, '');
    let body = {};
    if (path === '/settings/auth') body = {configured:true, authenticated:true};
    else if (path === '/settings' && opts.method === 'PUT') {
      const values = JSON.parse(opts.body);
      window.writes.push(values);
      Object.assign(window.saved, values);
      body = window.saved;
    } else if (path === '/settings') body = window.saved;
    else if (path === '/products') body = [];
    else if (path === '/backups/status') body = {enabled:false,storage:{used_human:'0 B',free_human:'1 GB'}};
    else if (path === '/backups') body = [];
    else if (path === '/printing/info') body = {platform:'Linux',default_printer:'TM-T81'};
    return {ok:true, status:200, json:async()=>body};
  };
  const {renderSettingsView} = await import(moduleUrl);
  await renderSettingsView(document.querySelector('#mount'));
}'''


class SettingsBrowserTests(unittest.TestCase):
    def test_categories_and_update_opt_out(self):
        from playwright.sync_api import sync_playwright
        js_url = module_url(JS_ROOT / "views" / "settingsview.js", {})
        css = (JS_ROOT.parent / "css" / "app.css").read_text(encoding="utf-8")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path="/usr/bin/chromium", args=["--no-sandbox"])
            try:
                for width in (1280, 390):
                    with self.subTest(viewport=width):
                        page = browser.new_page(viewport={"width": width, "height": 900})
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.set_content('<meta name="pypos-csrf-token" content="localtest">'
                                         '<div id="toast-root"></div><main id="mount"></main>')
                        page.add_style_tag(content=css)
                        page.evaluate(BOOTSTRAP, js_url)
                        self.assertEqual(page.locator(".settings-nav-button").count(), 7)
                        self.assertEqual(page.locator(".settings-panel:visible").count(), 1)
                        self.assertTrue(page.locator("#settings-panel-store").is_visible())
                        page.get_by_role("button", name="Receipts & printer").click()
                        self.assertTrue(page.locator("#settings-panel-receipts").is_visible())
                        self.assertFalse(page.get_by_label("How are product prices entered?").is_visible())
                        page.get_by_role("button", name="Save print settings").click()
                        page.wait_for_function("window.writes.length > 0")
                        self.assertNotIn("price_tax_mode", page.evaluate("window.writes.at(-1)"))
                        self.assertNotIn("store_local_tax", page.evaluate("window.writes.at(-1)"))
                        page.get_by_role("button", name="Store & tax").click()
                        self.assertTrue(page.get_by_label("How are product prices entered?").is_visible())
                        page.get_by_role("button", name="Save tax settings").click()
                        page.wait_for_function("window.writes.length > 1")
                        self.assertIn("price_tax_mode", page.evaluate("window.writes.at(-1)"))
                        page.get_by_role("button", name="App & updates").click()
                        toggle = page.get_by_label("Automatically check, download and activate software updates")
                        self.assertTrue(toggle.is_checked())
                        toggle.uncheck()
                        page.get_by_role("button", name="Save update preference").click()
                        page.wait_for_function("window.saved.auto_updates_enabled === 'false'")
                        self.assertEqual(page.evaluate("window.writes.at(-1)"),
                                         {"auto_updates_enabled": "false"})
                        page.get_by_role("button", name="Store & tax").click()
                        page.get_by_role("button", name="App & updates").click()
                        self.assertFalse(toggle.is_checked())
                        self.assertEqual(page.locator(".settings-panel:visible").count(), 1)
                        self.assertFalse(errors, errors)
                        page.close()
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
