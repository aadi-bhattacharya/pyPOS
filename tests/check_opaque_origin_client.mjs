// Node-only regression for POST header propagation without an HTTP server.
import assert from 'node:assert/strict';
import { api, csrfHeaders } from '../pos/static/js/api.js';

let token = 'server-provided-session-token';
globalThis.document = {
  querySelector(selector) {
    assert.equal(selector, 'meta[name="pypos-csrf-token"]');
    return { content: token };
  },
};
let captured = [];
globalThis.fetch = async (url, options) => {
  captured.push([url, options]);
  return {
    ok: true, status: 200,
    async json() { return { ok: true }; },
  };
};

assert.equal(csrfHeaders()['X-pyPOS-CSRF'], token);
await api.openSession(0);
assert.equal(captured[0][0], '/api/cash-sessions/open');
assert.equal(captured[0][1].headers['X-pyPOS-CSRF'], token);
assert.equal(captured[0][1].method, 'POST');
await api.applyCatalogImport(new Blob(['SKU']), 'add_stock', false);
assert.equal(captured[1][0], '/api/catalog/import/apply');
assert.equal(captured[1][1].headers['X-pyPOS-CSRF'], token);
assert.equal(captured[1][1].method, 'POST');
console.log('PASS: normal API writes and multipart imports include the session CSRF header');
