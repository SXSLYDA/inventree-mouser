// Render tests for static/mouser_ui.js in jsdom, using InvenTree's (target, context) entry convention.
// Run: npm install --no-save jsdom@24 && node --test tests/ui.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { JSDOM } = require(process.env.JSDOM_PATH || 'jsdom');

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://inventree.example.com/' });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const here = path.dirname(fileURLToPath(import.meta.url));
const ui = await import(pathToFileURL(path.join(here, '..', 'inventree_mouser', 'static', 'mouser_ui.js')).href);

const API = '/plugin/mouser/api/';
const HELD = {
  pk: 1, sales_order: '282378302', web_order: '40699983', po_number: '40699983', order_date: '2026-10-08',
  mouser_status: 'Complete', currency: 'AUD', state: 'held', state_label: 'Needs review',
  message: '1 of 5 line(s) need you to choose the part', purchase_order: null, line_count: 5, waiting: 1,
  tracking: [{ number: '1Z999', link: 'https://ups.example/1Z999' }], invoices: ['92944398'],
  totals: { merchandise: '872.86', fees: '0.00', total: '960.15' },
  lines: [
    { pk: 11, line_no: 3, mouser_pn: '571-DT04-12PA-E005', mpn: 'DT04-12PA-E005', manufacturer: 'TE', description: 'DT RECP',
      customer_pn: '', quantity: 75, unit_price: '4.94', match: 'possible', match_label: 'Possible match', decision: '',
      needs_person: true, message: 'close: DT04-12PA', part: null, supplier_part: '',
      candidates: [{ pk: 7, name: 'DT04-12PA', IPN: '', description: 'DT 12 way', active: true, url: '/web/part/7' }] },
    { pk: 12, line_no: 4, mouser_pn: '571-DT04-2P-E005', mpn: 'DT04-2P-E005', manufacturer: 'TE', description: 'DT RECP',
      customer_pn: '', quantity: 100, unit_price: '1.61', match: 'supplier_part', match_label: 'Mouser supplier part',
      decision: '', needs_person: false, message: '', part: { pk: 5, name: 'DT04-2P-E005', IPN: '', url: '/web/part/5', active: true },
      supplier_part: '571-DT04-2P-E005', candidates: [] },
  ],
};
const IMPORTED = { ...HELD, state: 'imported', state_label: 'Imported', waiting: 0, message: 'Created PO-0001.',
  purchase_order: { pk: 3, reference: 'PO-0001', url: '/web/purchasing/purchase-order/3', status: 'Placed' },
  lines: HELD.lines.map((l) => ({ ...l, needs_person: false })) };

function makeCtx(routes, context = {}) {
  const calls = [];
  const navigated = [];
  return {
    calls,
    navigated,
    navigate: (url) => navigated.push(url),
    context: { api: API, ...context },
    api: {
      request: async ({ method, url, data, params }) => {
        calls.push({ method, url, data, params });
        const key = `${method} ${url.replace(API, '')}`;
        const handler = routes[key];
        if (!handler) throw Object.assign(new Error('no route ' + key), { response: { data: { error: 'no route ' + key } } });
        const body = typeof handler === 'function' ? handler(data, params) : handler;
        if (body && body.__error) throw Object.assign(new Error(body.__error), { response: { data: { error: body.__error } } });
        return { data: body };
      },
    },
  };
}

const tick = () => new Promise((r) => setTimeout(r, 0));
async function settle() { for (let i = 0; i < 6; i++) await tick(); }
const click = (el) => el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));

const STATUS = { search_key: true, order_key: true, supplier_set: true, daily: true, start_date: '2026-10-01', hour: 6,
  po_status: 'placed', held: 1, failed: 0, can_change: true,
  last_run: { started: '2026-10-22T06:00:00Z', finished: '2026-10-22T06:00:05Z', ok: true, message: '1 Mouser order(s): 1 need review.', source: 'schedule' } };

test('dashboard lists orders and status', async () => {
  const el = document.createElement('div');
  const ctx = makeCtx({ 'GET status/': STATUS, 'GET orders/': { orders: [HELD] } });
  ui.renderMouserDashboard(el, ctx);
  await settle();
  const text = el.textContent;
  assert.match(text, /Daily at 6:00/);
  assert.match(text, /1 need review/);
  assert.match(text, /282378302/);
  assert.match(text, /Needs review/);
  assert.match(text, /1 to choose/);
});

test('held order: choose a candidate, the order goes through', async () => {
  const el = document.createElement('div');
  const ctx = makeCtx({
    'GET status/': STATUS, 'GET orders/': { orders: [HELD] }, 'GET orders/1/': { order: HELD },
    'POST lines/11/': (data) => { assert.deepEqual(data, { decision: 'link', part: 7 }); return { order: IMPORTED }; },
  });
  ui.renderMouserDashboard(el, ctx);
  await settle();
  click(el.querySelector('tr[data-order="1"]'));
  await settle();
  assert.match(el.textContent, /Possible match/);
  assert.match(el.textContent, /92944398/);
  click(el.querySelector('button[data-line="11"][data-act="link"]'));
  await settle();
  assert.match(el.textContent, /Created PO-0001/);
  assert.match(el.querySelector('tr[data-order="1"]').textContent, /Imported/);
  click(el.querySelector('[data-nav="/web/purchasing/purchase-order/3"]'));
  assert.deepEqual(ctx.navigated, ['/web/purchasing/purchase-order/3']);
});

test('held order: search for another part uses InvenTree\'s part API', async () => {
  const el = document.createElement('div');
  const ctx = makeCtx({
    'GET status/': STATUS, 'GET orders/': { orders: [HELD] }, 'GET orders/1/': { order: HELD },
    'GET /api/part/': (_d, params) => { assert.equal(params.search, 'DT04-12PA-E005'); assert.equal(params.purchaseable, true);
      return { results: [{ pk: 9, name: 'DT04-12PA-E005', IPN: 'X', description: 'exact' }] }; },
    'POST lines/11/': (data) => { assert.equal(data.part, 9); return { order: IMPORTED }; },
  });
  ui.renderMouserDashboard(el, ctx);
  await settle();
  click(el.querySelector('tr[data-order="1"]'));
  await settle();
  click(el.querySelector('button[data-line="11"][data-act="search"]'));
  await settle();
  const use = el.querySelector('[data-results="11"] button[data-part="9"]');
  assert.ok(use, 'search result has a Use button');
  click(use);
  await settle();
  assert.match(el.textContent, /Created PO-0001/);
});

test('import by number and errors are shown', async () => {
  const el = document.createElement('div');
  const ctx = makeCtx({
    'GET status/': STATUS, 'GET orders/': { orders: [] }, 'GET orders/1/': { order: HELD },
    'POST orders/import/': (data) => (data.number === '999' ? { __error: 'Mouser has no order 999 on this account.' } : { order: HELD }),
  });
  ui.renderMouserDashboard(el, ctx);
  await settle();
  assert.match(el.textContent, /No Mouser orders imported yet/);
  el.querySelector('input[data-number]').value = '999';
  click(el.querySelector('button[data-act="import"]'));
  await settle();
  assert.match(el.textContent, /no order 999/);
  el.querySelector('input[data-number]').value = '282378302';
  click(el.querySelector('button[data-act="import"]'));
  await settle();
  assert.match(el.textContent, /Mouser 282378302: Needs review/);
});

test('purchase order panel: send to cart', async () => {
  const el = document.createElement('div');
  const ctx = makeCtx({
    'GET po/4/': { reference: 'PO-0004', status: 10, is_pending: true, mouser: {}, order: null, mouser_lines: 2,
      other_lines: ['ACME-1'], order_key: true, cart_url: 'https://au.mouser.com/Cart/', can_change: true },
    'POST po/4/cart/': { cart_key: 'abc', cart_url: 'https://au.mouser.com/Cart/', currency: 'AUD', merchandise_total: '81.00',
      lines: [{ mouser_pn: '571-DT04-2P-E005' }, { mouser_pn: '650-RKEF135' }], problems: ['650-X: Part number not found.'],
      not_sent: ['ACME-1'], prices_copied: 2, po_number: 'PO-0004' },
  }, { pk: 4 });
  ui.renderMouserPanel(el, ctx);
  await settle();
  assert.match(el.textContent, /type PO-0004 as the PO number/);
  assert.match(el.textContent, /ACME-1/);
  click(el.querySelector('button[data-act="cart"]'));
  await settle();
  assert.match(el.textContent, /Sent to a new Mouser cart/);
  assert.match(el.textContent, /key abc/);
  assert.match(el.textContent, /81\.00 AUD/);
  assert.match(el.textContent, /Part number not found/);
  assert.ok(el.querySelector('a[href="https://au.mouser.com/Cart/"]'), 'link to Mouser');
});

test('purchase order panel: shows the Mouser order behind an imported PO', async () => {
  const el = document.createElement('div');
  const ctx = makeCtx({
    'GET po/3/': { reference: 'PO-0001', status: 20, is_pending: false, mouser: { sales_order: '282378302' }, order: IMPORTED,
      mouser_lines: 5, other_lines: [], order_key: true, cart_url: 'https://au.mouser.com/Cart/', can_change: true },
  }, { pk: 3 });
  ui.renderMouserPanel(el, ctx);
  await settle();
  assert.match(el.textContent, /282378302/);
  assert.match(el.textContent, /1Z999/);
  assert.match(el.textContent, /960\.15 AUD/);
  assert.ok(!el.querySelector('button[data-act="cart"]'), 'no cart button on an imported order');
});

test('escapes Mouser text', async () => {
  const el = document.createElement('div');
  const evil = { ...HELD, sales_order: '<img src=x onerror=alert(1)>' };
  const ctx = makeCtx({ 'GET status/': STATUS, 'GET orders/': { orders: [evil] } });
  ui.renderMouserDashboard(el, ctx);
  await settle();
  assert.equal(el.querySelector('img'), null);
});

test('tracking: only web links become links', async () => {
  const el = document.createElement('div');
  const order = { ...HELD, tracking: [{ number: 'BAD1', link: 'javascript:alert(1)' }, { number: '1Z999', link: 'https://ups.example/1Z999' }] };
  const ctx = makeCtx({ 'GET status/': STATUS, 'GET orders/': { orders: [order] }, 'GET orders/1/': { order } });
  ui.renderMouserDashboard(el, ctx);
  await settle();
  click(el.querySelector('tr[data-order="1"]'));
  await settle();
  assert.match(el.textContent, /BAD1/);
  assert.ok(!el.querySelector('a[href^="javascript"]'), 'no javascript: link');
  assert.ok(el.querySelector('a[href="https://ups.example/1Z999"]'), 'web link kept');
});

test('dates are shown DD-MM-YYYY', async () => {
  const el = document.createElement('div');
  const ctx = makeCtx({ 'GET status/': STATUS, 'GET orders/': { orders: [HELD] } });
  ui.renderMouserDashboard(el, ctx);
  await settle();
  assert.match(el.textContent, /orders from 01-10-2026/);
  assert.match(el.textContent, /08-10-2026/);
});
