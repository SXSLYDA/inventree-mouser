/*
 * Mouser plugin UI for InvenTree - the "Mouser Orders" dashboard item and the
 * "Mouser" panel on Mouser purchase orders. Plain JS, no build step.
 *
 * Works with both InvenTree plugin calling conventions:
 *   fn(targetElement, context)   -> renders into the element
 *   fn(context)                  -> returns a React element (window.React)
 */

const DEFAULT_API = '/plugin/mouser/api/';

/* ------------------------------------------------------------------ helpers */

function getCookie(name) {
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return m ? decodeURIComponent(m[1]) : '';
}

function apiBase(ctx) {
  return (ctx && ctx.context && ctx.context.api) || DEFAULT_API;
}

async function request(ctx, method, url, body, params) {
  if (ctx && ctx.api && typeof ctx.api.request === 'function') {
    try {
      const r = await ctx.api.request({ method, url, data: body, params });
      return r.data;
    } catch (err) {
      const d = (err.response && err.response.data) || {};
      throw new Error(d.error || d.detail || d.message || err.message);
    }
  }
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') {
    headers['Content-Type'] = 'application/json';
    headers['X-CSRFToken'] = getCookie('csrftoken');
  }
  const query = params ? '?' + new URLSearchParams(params).toString() : '';
  const r = await fetch(url + query, {
    method, credentials: 'same-origin', headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || data.detail || data.message || r.statusText);
  return data;
}

function call(ctx, method, path, body) {
  return request(ctx, method, apiBase(ctx) + path, body);
}

function esc(v) {
  return String(v === null || v === undefined ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Only web links from Mouser's data become hrefs - never javascript: or anything else
function webUrl(v) {
  const url = String(v || '').trim();
  return /^https?:\/\//i.test(url) ? url : '';
}

// Dates are shown DD-MM-YYYY (times HH:MM, local) - the same as the plugin's settings
const pad = (n) => String(n).padStart(2, '0');
const ddmmyyyy = (d) => `${pad(d.getDate())}-${pad(d.getMonth() + 1)}-${d.getFullYear()}`;

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d) ? esc(iso) : `${ddmmyyyy(d)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fmtDay(iso) {
  if (!iso) return '—';
  const d = new Date(iso + 'T00:00:00');
  return isNaN(d) ? esc(iso) : ddmmyyyy(d);
}

function money(v, cur) {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  return isNaN(n) ? esc(v) : `${n.toFixed(n < 10 ? 3 : 2)}${cur ? ' ' + esc(cur) : ''}`;
}

function navigate(ctx, url) {
  if (ctx && typeof ctx.navigate === 'function') ctx.navigate(url);
  else window.location.href = url;
}

const STYLE = `
.mouser-ui{font-size:14px;line-height:1.45}
.mouser-ui .mr-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:6px 0}
.mouser-ui .mr-grid{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:8px 0}
.mouser-ui .mr-grid dt{opacity:.7}
.mouser-ui .mr-grid dd{margin:0;word-break:break-word}
.mouser-ui button{font:inherit;padding:4px 11px;border-radius:6px;border:1px solid rgba(128,128,128,.45);
  background:transparent;color:inherit;cursor:pointer}
.mouser-ui button.primary{background:#0a6ebd;border-color:#0a6ebd;color:#fff}
.mouser-ui button.small{padding:2px 8px;font-size:12px}
.mouser-ui button.danger{border-color:#e03131;color:#e03131}
.mouser-ui button:disabled{opacity:.5;cursor:wait}
.mouser-ui input,.mouser-ui select{font:inherit;padding:4px 8px;border-radius:6px;
  border:1px solid rgba(128,128,128,.45);background:transparent;color:inherit}
.mouser-ui input{min-width:140px}
.mouser-ui .badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600;
  background:rgba(128,128,128,.18);white-space:nowrap}
.mouser-ui .badge.ok{background:rgba(47,158,68,.18);color:#2f9e44}
.mouser-ui .badge.warn{background:rgba(240,140,0,.18);color:#e8590c}
.mouser-ui .badge.err{background:rgba(224,49,49,.18);color:#e03131}
.mouser-ui .badge.info{background:rgba(10,110,189,.18);color:#0a6ebd}
.mouser-ui .msg{margin:6px 0;padding:6px 10px;border-radius:6px;background:rgba(128,128,128,.12)}
.mouser-ui .msg.err{background:rgba(224,49,49,.12);color:#e03131}
.mouser-ui .msg.ok{background:rgba(47,158,68,.12)}
.mouser-ui table{width:100%;border-collapse:collapse;font-size:13px}
.mouser-ui th,.mouser-ui td{text-align:left;padding:5px 6px;border-bottom:1px solid rgba(128,128,128,.2);vertical-align:top}
.mouser-ui th{font-weight:600;opacity:.75}
.mouser-ui tr.clickable{cursor:pointer}
.mouser-ui tr.clickable:hover td{background:rgba(128,128,128,.08)}
.mouser-ui tr.open td{background:rgba(10,110,189,.06)}
.mouser-ui .tbl{overflow-x:auto}
.mouser-ui a{color:#0a6ebd;cursor:pointer}
.mouser-ui code{font-size:12px;padding:1px 4px;border-radius:4px;background:rgba(128,128,128,.15);word-break:break-all}
.mouser-ui h4{margin:12px 0 4px;font-size:14px}
.mouser-ui .muted{opacity:.65}
.mouser-ui .small{font-size:12px}
.mouser-ui .detail{padding:6px 4px 10px;border-left:3px solid rgba(10,110,189,.4);margin:2px 0 8px 4px;padding-left:10px}
.mouser-ui .pick{display:flex;flex-direction:column;gap:4px;margin-top:4px}
.mouser-ui .pick .opt{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
`;

function shell(target) {
  target.innerHTML = '';
  const style = document.createElement('style');
  style.textContent = STYLE;
  const root = document.createElement('div');
  root.className = 'mouser-ui';
  target.appendChild(style);
  target.appendChild(root);
  return root;
}

async function busy(btn, fn) {
  const label = btn.textContent;
  btn.disabled = true;
  try { await fn(); } finally { btn.disabled = false; btn.textContent = label; }
}

function stateBadge(state, label) {
  const cls = { imported: 'ok', linked: 'ok', held: 'warn', failed: 'err', ignored: '' }[state] || '';
  return `<span class="badge ${cls}">${esc(label || state)}</span>`;
}

function matchBadge(line) {
  if (line.decision === 'skip') return '<span class="badge">Left off</span>';
  if (line.decision === 'create') return '<span class="badge info">Create new</span>';
  if (line.decision === 'link') return '<span class="badge ok">Chosen</span>';
  const cls = { supplier_part: 'ok', part: 'ok', new: 'info', possible: 'warn', ambiguous: 'warn' }[line.match] || '';
  return `<span class="badge ${cls}">${esc(line.match_label || line.match || '?')}</span>`;
}

function partLink(p) {
  if (!p) return '';
  return `<a data-nav="${esc(p.url)}">${esc(p.name)}</a>${p.IPN && p.IPN !== p.name ? ` <span class="muted small">${esc(p.IPN)}</span>` : ''}`
    + (p.active === false ? ' <span class="badge err">inactive</span>' : '');
}

function wireNav(root, ctx) {
  root.querySelectorAll('[data-nav]').forEach((a) => a.addEventListener('click', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    navigate(ctx, a.dataset.nav);
  }));
}

/* ------------------------------------------------------------------ order detail (shared) */

function trackingHtml(order) {
  const t = (order.tracking || []).filter((x) => x.number || x.link);
  if (!t.length) return '—';
  return t.map((x) => webUrl(x.link) ? `<a href="${esc(webUrl(x.link))}" target="_blank" rel="noopener">${esc(x.number || 'track')}</a>`
    : esc(x.number)).join(', ');
}

function orderHeader(order) {
  return `<dl class="mr-grid">
    <dt>Mouser order</dt><dd>${esc(order.sales_order)}${order.web_order ? ` <span class="muted">(web ${esc(order.web_order)})</span>` : ''}</dd>
    <dt>Placed</dt><dd>${fmtDay(order.order_date)} <span class="muted">${esc(order.mouser_status)}</span></dd>
    <dt>PO number at checkout</dt><dd>${esc(order.po_number) || '—'}</dd>
    <dt>Invoice</dt><dd>${(order.invoices || []).map(esc).join(', ') || '—'}</dd>
    <dt>Tracking</dt><dd>${trackingHtml(order)}</dd>
    <dt>Total</dt><dd>${money((order.totals || {}).total, order.currency)}${(order.totals || {}).fees && Number(order.totals.fees) > 0
      ? ` <span class="muted">(fees ${money(order.totals.fees, order.currency)})</span>` : ''}</dd>
  </dl>`;
}

function linesTable(order, editable) {
  const rows = (order.lines || []).map((ln) => {
    let partCell = ln.part ? partLink(ln.part) : '';
    if (ln.supplier_part) partCell += ` <span class="muted small">(${esc(ln.supplier_part)})</span>`;
    let pick = '';
    if (editable) {
      const cands = (ln.candidates || []).map((c) => `<div class="opt">
          <button class="small" data-line="${ln.pk}" data-act="link" data-part="${c.pk}">Use</button> ${partLink(c)}
          <span class="muted small">${esc((c.description || '').slice(0, 80))}</span></div>`).join('');
      pick = `<div class="pick">${cands}
        <div class="opt"><input data-search="${ln.pk}" placeholder="Find another part…" value="${esc(ln.mpn)}">
          <button class="small" data-line="${ln.pk}" data-act="search">Search</button></div>
        <div class="opt" data-results="${ln.pk}"></div>
        <div class="opt">
          <button class="small" data-line="${ln.pk}" data-act="create">Create new part</button>
          <button class="small" data-line="${ln.pk}" data-act="skip">Leave off</button>
          ${ln.decision ? `<button class="small" data-line="${ln.pk}" data-act="reset">Undo choice</button>` : ''}
        </div></div>`;
    }
    return `<tr>
      <td>${ln.line_no}</td>
      <td><code>${esc(ln.mouser_pn)}</code><br><span class="small">${esc(ln.mpn)}</span>
        <span class="muted small">${esc(ln.manufacturer)}</span>
        ${ln.customer_pn ? `<br><span class="muted small">your no. ${esc(ln.customer_pn)}</span>` : ''}</td>
      <td>${esc(ln.quantity)}</td>
      <td>${money(ln.unit_price, order.currency)}</td>
      <td>${matchBadge(ln)} ${partCell}
        ${ln.message ? `<div class="muted small">${esc(ln.message)}</div>` : ''}${pick}</td>
    </tr>`;
  }).join('');
  return `<div class="tbl"><table><thead><tr><th>#</th><th>Mouser / MPN</th><th>Qty</th><th>Unit</th><th>InvenTree part</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="5" class="muted">No lines</td></tr>'}</tbody></table></div>`;
}

async function searchParts(ctx, term) {
  const data = await request(ctx, 'GET', '/api/part/', null, { search: term, purchaseable: true, limit: 8 });
  return Array.isArray(data) ? data : (data.results || []);
}

/* Renders a held/failed/done order with its lines; onChange(order) after an action. */
function renderOrderDetail(el, ctx, order, canChange, onChange) {
  const editable = canChange && (order.state === 'held' || order.state === 'failed');
  let message = null;

  const draw = () => {
    el.innerHTML = `
      ${message ? `<div class="msg ${message.ok ? 'ok' : 'err'}">${esc(message.text)}</div>` : ''}
      ${order.message ? `<div class="msg ${order.state === 'failed' ? 'err' : ''}">${esc(order.message)}</div>` : ''}
      ${orderHeader(order)}
      ${order.purchase_order ? `<div class="mr-row">Purchase order: <a data-nav="${esc(order.purchase_order.url)}">${esc(order.purchase_order.reference)}</a>
        <span class="muted">${esc(order.purchase_order.status)}</span></div>` : ''}
      ${linesTable(order, editable)}
      ${canChange ? `<div class="mr-row">
        ${editable ? '<button class="primary" data-order-act="retry">Try again</button>' : ''}
        ${editable ? '<button class="danger" data-order-act="ignore">Ignore this order</button>' : ''}
        ${order.state === 'ignored' ? '<button data-order-act="reopen">Reopen</button>' : ''}
      </div>` : ''}`;
    wireNav(el, ctx);

    el.querySelectorAll('button[data-order-act]').forEach((btn) => btn.addEventListener('click', () => busy(btn, async () => {
      const act = btn.dataset.orderAct;
      if (act === 'ignore' && !window.confirm(`Ignore Mouser order ${order.sales_order}? No purchase order will be made for it.`)) return;
      try {
        const r = await call(ctx, 'POST', `orders/${order.pk}/${act}/`, {});
        order = r.order;
        message = null;
        onChange(order);
      } catch (e) { message = { ok: false, text: e.message }; }
      draw();
    })));

    el.querySelectorAll('button[data-line]').forEach((btn) => btn.addEventListener('click', () => busy(btn, async () => {
      const pk = btn.dataset.line;
      const act = btn.dataset.act;
      try {
        if (act === 'search') {
          const input = el.querySelector(`input[data-search="${pk}"]`);
          const box = el.querySelector(`[data-results="${pk}"]`);
          const parts = await searchParts(ctx, input.value.trim());
          box.innerHTML = parts.length ? parts.map((p) => `<div class="opt">
              <button class="small" data-line="${pk}" data-act="link" data-part="${p.pk}">Use</button>
              <a data-nav="/web/part/${p.pk}">${esc(p.name)}</a> <span class="muted small">${esc(p.IPN || '')} ${esc((p.description || '').slice(0, 60))}</span>
            </div>`).join('') : '<span class="muted small">No purchaseable parts found</span>';
          wireNav(box, ctx);
          box.querySelectorAll('button[data-line]').forEach((b2) => b2.addEventListener('click', () => busy(b2, async () => {
            try {
              const r = await call(ctx, 'POST', `lines/${pk}/`, { decision: 'link', part: Number(b2.dataset.part) });
              order = r.order; message = null; onChange(order);
            } catch (e) { message = { ok: false, text: e.message }; }
            draw();
          })));
          return;
        }
        const body = { decision: act === 'reset' ? '' : act };
        if (act === 'link') body.part = Number(btn.dataset.part);
        const r = await call(ctx, 'POST', `lines/${pk}/`, body);
        order = r.order;
        message = null;
        onChange(order);
      } catch (e) { message = { ok: false, text: e.message }; }
      draw();
    })));
  };
  draw();
}

/* ------------------------------------------------------------------ dashboard */

function statusRow(s) {
  const b = (ok, label) => `<span class="badge ${ok ? 'ok' : 'err'}">${esc(label)}</span>`;
  const daily = s.daily
    ? (s.start_date ? `<span class="badge ok">Daily at ${esc(s.hour)}:00, orders from ${fmtDay(s.start_date)}</span>`
      : '<span class="badge warn">Daily on, but no start date</span>')
    : '<span class="badge">Daily check off</span>';
  return `<div class="mr-row">${b(s.order_key, 'Order key')} ${b(s.search_key, 'Search key')}
    ${b(s.supplier_set, 'Supplier')} ${daily}
    ${s.held ? `<span class="badge warn">${s.held} need review</span>` : ''}
    ${s.failed ? `<span class="badge err">${s.failed} failed</span>` : ''}</div>`;
}

const FILTERS = [['', 'All'], ['held,failed', 'Needs you'], ['imported,linked', 'Purchase orders'], ['ignored', 'Ignored']];

// The dashboard item, and (full: true) the Purchasing page's Mouser panel: more
// orders, filter buttons and the order total.
function dashboard(target, ctx, opts = {}) {
  const full = !!opts.full;
  const root = shell(target);
  let status = null;
  let orders = [];
  let openPk = null;
  let message = null;
  let filter = '';

  const ordersPath = () => (full ? `orders/?limit=200${filter ? `&state=${encodeURIComponent(filter)}` : ''}` : 'orders/');

  const load = async () => {
    try {
      [status, orders] = await Promise.all([
        call(ctx, 'GET', 'status/'),
        call(ctx, 'GET', ordersPath()).then((r) => r.orders || []),
      ]);
    } catch (e) {
      message = { ok: false, text: e.message };
    }
  };

  const draw = () => {
    if (!status) {
      root.innerHTML = message ? `<div class="msg err">${esc(message.text)}</div>` : '<p class="muted">Loading…</p>';
      return;
    }
    const last = status.last_run;
    const cols = full ? 5 : 4;
    root.innerHTML = `
      ${statusRow(status)}
      ${last ? `<div class="small muted">Last check ${fmtDate(last.started)} (${esc(last.source)}): ${esc(last.message || (last.finished ? '' : 'running…'))}</div>` : ''}
      ${message ? `<div class="msg ${message.ok ? 'ok' : 'err'}">${esc(message.text)}</div>` : ''}
      ${status.can_change ? `<div class="mr-row">
        <button class="primary" data-act="check">Check Mouser now</button>
        <input data-number placeholder="Mouser order no.">
        <button data-act="import">Import order</button>
        <button data-act="refresh">Refresh</button>
      </div>` : ''}
      ${full ? `<div class="mr-row">${FILTERS.map(([value, label]) =>
        `<button data-filter="${value}" class="${value === filter ? 'primary' : ''}">${label}</button>`).join(' ')}</div>` : ''}
      <div class="tbl"><table><thead><tr><th>Placed</th><th>Mouser order</th><th>Status</th><th>Purchase order</th>${full ? '<th>Total</th>' : ''}</tr></thead>
      <tbody>${orders.length ? orders.map((o) => `
        <tr class="clickable ${o.pk === openPk ? 'open' : ''}" data-order="${o.pk}">
          <td>${fmtDay(o.order_date)}</td>
          <td>${esc(o.sales_order)}<div class="muted small">${esc(o.line_count)} line(s)${o.po_number ? ' · PO no. ' + esc(o.po_number) : ''}</div></td>
          <td>${stateBadge(o.state, o.state_label)}${o.waiting ? ` <span class="small">${o.waiting} to choose</span>` : ''}</td>
          <td>${o.purchase_order ? `<a data-nav="${esc(o.purchase_order.url)}">${esc(o.purchase_order.reference)}</a>` : '—'}</td>
          ${full ? `<td>${money(o.total, o.currency)}</td>` : ''}
        </tr>
        ${o.pk === openPk ? `<tr><td colspan="${cols}"><div class="detail" data-detail="${o.pk}"><span class="muted">Loading…</span></div></td></tr>` : ''}
      `).join('') : `<tr><td colspan="${cols}" class="muted">${filter ? 'No Mouser orders like that.' : 'No Mouser orders imported yet.'}</td></tr>`}</tbody></table></div>`;
    root.querySelectorAll('button[data-filter]').forEach((btn) => btn.addEventListener('click', () => busy(btn, async () => {
      filter = btn.dataset.filter;
      openPk = null;
      await load();
      draw();
    })));
    wireNav(root, ctx);

    root.querySelectorAll('tr[data-order]').forEach((tr) => tr.addEventListener('click', () => {
      const pk = Number(tr.dataset.order);
      openPk = openPk === pk ? null : pk;
      draw();
    }));

    const el = openPk ? root.querySelector(`[data-detail="${openPk}"]`) : null;
    if (el) {
      call(ctx, 'GET', `orders/${openPk}/`).then((r) => {
        renderOrderDetail(el, ctx, r.order, status.can_change, (order) => {
          orders = orders.map((o) => (o.pk === order.pk ? { ...o, ...order } : o));
          root.querySelectorAll(`tr[data-order="${order.pk}"] td`)[2].innerHTML = stateBadge(order.state, order.state_label);
        });
      }).catch((e) => { el.innerHTML = `<div class="msg err">${esc(e.message)}</div>`; });
    }

    root.querySelectorAll('button[data-act]').forEach((btn) => btn.addEventListener('click', () => busy(btn, async () => {
      const act = btn.dataset.act;
      try {
        if (act === 'check') {
          await call(ctx, 'POST', 'orders/check/', {});
          message = { ok: true, text: 'Checking Mouser in the background - press Refresh in a minute.' };
        } else if (act === 'import') {
          const number = root.querySelector('input[data-number]').value.trim();
          const r = await call(ctx, 'POST', 'orders/import/', { number });
          openPk = r.order.pk;
          message = { ok: r.order.state !== 'failed', text: `Mouser ${r.order.sales_order}: ${r.order.state_label}` };
          await load();
          if (!orders.some((o) => o.pk === r.order.pk)) orders = [r.order, ...orders];
          draw();
          return;
        } else {
          message = null;
        }
        await load();
      } catch (e) {
        message = { ok: false, text: e.message };
      }
      draw();
    })));
  };

  load().then(draw);
}

/* ------------------------------------------------------------------ purchase order panel */

function panel(target, ctx) {
  const root = shell(target);
  const pk = (ctx.context && ctx.context.pk) || ctx.id;
  let info = null;
  let cart = null;
  let message = null;

  const load = async () => {
    try { info = await call(ctx, 'GET', `po/${pk}/`); } catch (e) { message = { ok: false, text: e.message }; }
  };

  const draw = () => {
    if (!info) {
      root.innerHTML = message ? `<div class="msg err">${esc(message.text)}</div>` : '<p class="muted">Loading…</p>';
      return;
    }
    const meta = info.mouser || {};
    const cartBlock = cart ? `
      <h4>${cart.reused ? 'Your Mouser cart was updated' : 'Sent to a new Mouser cart'}</h4>
      <div class="msg ok">${cart.lines.length} line(s), ${money(cart.merchandise_total, cart.currency)}.
        On <a href="${esc(cart.cart_url)}" target="_blank" rel="noopener">Mouser</a>, open your saved carts and pick the one
        with key <code>${esc(cart.cart_key)}</code>, then check out -
        type <b>${esc(cart.po_number)}</b> as the PO number so the order comes back to this purchase order.
        ${cart.prices_copied ? `${cart.prices_copied} price(s) copied from the cart.` : ''}</div>
      ${cart.problems.length ? `<div class="msg err">${cart.problems.map(esc).join('<br>')}</div>` : ''}
      ${cart.not_sent.length ? `<div class="muted small">Not sent (not Mouser parts): ${cart.not_sent.map(esc).join(', ')}</div>` : ''}` : '';

    root.innerHTML = `
      ${message ? `<div class="msg ${message.ok ? 'ok' : 'err'}">${esc(message.text)}</div>` : ''}
      ${info.order ? '<div data-order></div>' : (meta.sales_order ? orderHeader({ ...meta, lines: [] }) : '')}
      ${!info.order ? `
        <h4>Mouser cart</h4>
        ${meta.cart_sent ? `<div class="small muted">Last sent ${fmtDate(meta.cart_sent)} (${money(meta.cart_total, meta.cart_currency)}),
          cart <code>${esc(meta.cart_key)}</code> - sending again updates that cart.</div>` : ''}
        <p class="small">Puts this order's ${info.mouser_lines} Mouser line(s) into a cart on your Mouser account.
          At checkout, type <b>${esc(info.reference)}</b> as the PO number - the order import then updates this
          purchase order with what Mouser actually charged instead of making a new one.</p>
        ${info.other_lines.length ? `<div class="muted small">Not Mouser parts (won't be sent): ${info.other_lines.map(esc).join(', ')}</div>` : ''}
        ${info.can_change ? `<div class="mr-row"><button class="primary" data-act="cart" ${info.order_key && info.mouser_lines ? '' : 'disabled'}>
          Send to Mouser cart</button>${info.order_key ? '' : ' <span class="muted small">Set the Order API key in the plugin settings first.</span>'}</div>` : ''}
        ${cartBlock}` : ''}`;

    if (info.order) {
      renderOrderDetail(root.querySelector('[data-order]'), ctx, info.order, info.can_change, (order) => { info.order = order; });
    }
    wireNav(root, ctx);
    root.querySelectorAll('button[data-act="cart"]').forEach((btn) => btn.addEventListener('click', () => busy(btn, async () => {
      try {
        cart = await call(ctx, 'POST', `po/${pk}/cart/`, {});
        message = null;
        await load();
      } catch (e) { message = { ok: false, text: e.message }; }
      draw();
    })));
  };

  load().then(draw);
}

/* ------------------------------------------------------------------ exports */

function mount(a, b, renderer) {
  if (a && a.nodeType === 1) {
    renderer(a, b || {});
    return undefined;
  }
  const ctx = a || {};
  const React = window.React;
  if (!React) {
    console.error('Mouser plugin: React is not available and no target element was given');
    return null;
  }
  function MouserHost() {
    const ref = React.useRef(null);
    React.useEffect(() => { if (ref.current) renderer(ref.current, ctx); }, []);
    return React.createElement('div', { ref });
  }
  return React.createElement(MouserHost);
}

export function renderMouserDashboard(a, b) { return mount(a, b, dashboard); }
export function renderMouserPanel(a, b) { return mount(a, b, panel); }
export function renderMouserPurchasing(a, b) { return mount(a, b, (target, ctx) => dashboard(target, ctx, { full: true })); }
