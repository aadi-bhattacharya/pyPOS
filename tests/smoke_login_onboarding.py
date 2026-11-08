"""Offline Chromium checks for the standalone staff setup and login UI.

Works without Flask: loads the actual HTML template and form builders from
pos/app.py with a stubbed CSRF token issuer. No HTTP server required.
"""
import ast
import html
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'pos' / 'app.py'
CSS = ROOT / 'pos' / 'static' / 'css'
LOGIN_JS = ROOT / 'pos' / 'static' / 'js' / 'login.js'


def page_builders():
    tree = ast.parse(APP.read_text(encoding='utf-8'))
    names = {'_LOGIN_PAGE', '_SETUP_INTRO'}
    routines = {'_login_page', '_staff_setup_form', '_staff_login_form'}
    subset = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in names for t in node.targets):
            subset.append(node)
        if isinstance(node, ast.FunctionDef) and node.name in routines:
            subset.append(node)
    ns = dict(html=html, re=re, issue_csrf_token=lambda: 'fake-only-for-browser-smoke',
              LOGO_PATTERN=re.compile(r'^(?:data:image/[^,]+,).*'))
    exec(compile(ast.Module(body=subset, type_ignores=[]), str(APP), 'exec'), ns)
    return ns['_login_page'], ns['_staff_setup_form'], ns['_staff_login_form']


def assert_page(page, markup, viewport, mode):
    page.set_viewport_size({'width': viewport, 'height': 850})
    # No localhost fetch; stylesheet content and JS are loaded from disk.
    markup = re.sub(r'<link rel="stylesheet" href="[^"]+">', '', markup)
    markup = re.sub(r'<script type="module"[^>]*></script>', '', markup)
    page.set_content(markup, wait_until='load')
    page.add_style_tag(content=(CSS / 'app.css').read_text(encoding='utf-8'))
    page.add_style_tag(content=(CSS / 'login.css').read_text(encoding='utf-8'))
    page.add_script_tag(path=str(LOGIN_JS))
    assert page.locator('input[name=_csrf]').count() == 1
    assert page.locator('h1').count() == 1
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), mode + ': horizontal overflow'
    if mode == 'setup':
        assert page.locator('input[name=display_name]').count() == 1
        assert page.locator('input[name=username]').count() == 1
        assert page.locator('input[name=pin]').count() == 1
        assert page.locator('input[name=admin_password]').count() == 1
        # A staff username and PIN are not the separate administrator password.
        page.locator('#setup-name').fill('Store Owner')
        page.locator('#setup-username').fill('owner')
        page.locator('#setup-pin').fill('12345')
        page.locator('#setup-admin-password').fill('valid-secret')
        assert not page.locator('form').evaluate('(f) => f.checkValidity()')
        page.locator('#setup-pin').fill('123456')
        assert page.locator('form').evaluate('(f) => f.checkValidity()')
        page.locator('[data-reveal="setup-pin"]').click()
        assert page.locator('#setup-pin').get_attribute('type') == 'text'
        page.locator('[data-reveal="setup-pin"]').click()
        assert page.locator('#setup-pin').get_attribute('type') == 'password'
    if mode == 'staff':
        assert page.locator('input[name=username]').count() == 1
        assert page.locator('input[name=pin]').count() == 1
        assert page.locator('input[name=admin_password]').count() == 0
    return page.evaluate('({width:document.querySelector(".auth-layout").getBoundingClientRect().width, height:document.querySelector(".auth-layout").getBoundingClientRect().height})')


def main():
    login_page, setup_form, staff_form = page_builders()
    branding = {'pos_display_name': 'Example Store', 'pos_accent_color': '#225FAC'}
    assert 'Existing administrator password' in setup_form(True)
    assert 'Create administrator password' in setup_form(False)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        try:
            page = browser.new_page()
            for width in (1440, 700, 390, 320):
                markup = login_page(body=setup_form(branding.get('admin_password', '') != ''),
                                    branding=branding, mode='setup')
                info = assert_page(page, markup, width, 'setup')
                print(f'setup {width}px: OK {info}')
            for width in (1440, 390, 320):
                markup = login_page(body=staff_form(), branding=branding, mode='staff')
                info = assert_page(page, markup, width, 'staff')
                print(f'staff {width}px: OK {info}')
            # Handle hostile text as an attribute value; it must never execute.
            hostile = setup_form(False, '" onfocus="alert(1)', '<script>alert(1)</script>')
            assert 'value="&quot; onfocus=&quot;alert(1)"' in hostile
            assert '&lt;script&gt;' in hostile
            assert 'name="_csrf"' in login_page(body=hostile, mode='setup')
            print('field escaping + session token: OK')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
