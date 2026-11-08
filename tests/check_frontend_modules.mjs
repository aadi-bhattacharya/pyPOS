// Run: node tests/check_frontend_modules.mjs
// Checks browser ES-module parsing AND import linking. A syntax error in any
// eagerly imported page otherwise leaves the entire POS screen blank.
import { execFileSync } from 'node:child_process';
import { readdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const jsRoot = resolve(root, 'pos/static/js');
const files = [
  ...readdirSync(jsRoot).filter(n => n.endsWith('.js')).map(n => resolve(jsRoot, n)),
  ...readdirSync(resolve(jsRoot, 'views')).filter(n => n.endsWith('.js'))
    .map(n => resolve(jsRoot, 'views', n)),
];

for (const file of files) {
  // Force module parse mode. A plain `node --check filename.js` can parse in
  // CommonJS mode on some Node versions and miss errors in module files.
  execFileSync(process.execPath, ['--check', '--input-type=module'], {
    input: await import('node:fs').then(fs => fs.readFileSync(file)),
    stdio: ['pipe', 'pipe', 'pipe'],
  });
}

// Parse alone is insufficient: linking catches missing named exports and
// broken imports before the app's static import graph reaches the browser.
const expected = {
  'views/register.js': 'renderRegister',
  'views/catalog.js': 'renderCatalog',
  'views/inventory.js': 'renderInventory',
  'views/customers.js': 'renderCustomers',
  'views/salesview.js': 'renderSales',
  'views/refunds.js': 'renderRefunds',
  'views/reports.js': 'renderReports',
  'views/settingsview.js': 'renderSettingsView',
};
for (const [file, exported] of Object.entries(expected)) {
  const mod = await import(pathToFileURL(resolve(jsRoot, file)));
  assert.equal(typeof mod[exported], 'function', `${file} must export ${exported}`);
  if (file === 'views/reports.js') {
    for (const name of ['renderCashDrawerPage', 'renderCashStatusStrip', 'showZModal']) {
      assert.equal(typeof mod[name], 'function', `Cash drawer must export ${name}`);
    }
  }
}
const html = (await import('node:fs')).readFileSync(resolve(root, 'pos/templates/index.html'), 'utf8');
// Regression: GET /api/cash-sessions/current is a flattened session object,
// not { session: {...}, report: {...} } like the POST open/close responses.
// This check runs on every native-binary CI build, even without Chromium.
const { isOpenCashSession } = await import(
  pathToFileURL(resolve(jsRoot, 'views/register.js')));
assert.equal(isOpenCashSession({ id: 19, opened_at: '2026-11-08 09:00:00',
  closed_at: null, report: {} }), true, 'open day must permit payment');
assert.equal(isOpenCashSession(null), false, 'closed day must block payment');
assert.equal(isOpenCashSession({ id: 19, closed_at: '2026-11-08 18:00:00' }), false);
assert.equal(isOpenCashSession({ session: { id: 19, closed_at: null } }), false,
  'unexpected API response must not permit payment');
assert.match(html, /data-view="cash"/, 'Cash Drawer must remain in main navigation');
assert.match(html, /data-view="refunds"/, 'Returns must be directly accessible from navigation');
console.log(`PASS: ${files.length} ES modules parse; all page imports and Cash Drawer exports link.`);
