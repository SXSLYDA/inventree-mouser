# inventree-mouser

Mouser Electronics for [InvenTree](https://inventree.org) (1.5.1+):

- **Import from Supplier** — search Mouser from InvenTree's own Import Part wizard
  (Parts → Add Parts → Import from Supplier) and create the part with its manufacturer
  part, Mouser supplier part, price breaks, parameters and (optionally) picture.
- **Mouser orders → purchase orders** — once a day (or on demand) the orders on your
  Mouser account become InvenTree purchase orders with Mouser's actual prices. Parts that
  don't exist are created; anything that only *might* be a part you already have holds the
  order for you to decide. Receiving the stock stays with you (InvenTree's Receive Items).
- **Send to Mouser cart** — a purchase order's Mouser lines go into a cart on your Mouser
  account; check out on Mouser with the purchase order's reference as the PO number and the
  order import updates that same purchase order instead of making a new one.

It also ships **`mouser_lookup`**, a small Mouser API client with no Django dependency
(part search, order history, carts), for use outside InvenTree.

## Install

Add to InvenTree's `plugins.txt` (pin a release tag) and run `invoke update`:

```
inventree-mouser @ git+https://github.com/SXSLYDA/inventree-mouser.git@v0.1.0
```

Or on a package-installer server, `sudo inventree-mouser-update` (install it once — see the top
of `tools/server-update.sh`): it adds or moves that line, backs up, updates and restarts.

Then in **Admin Center → Plugins**, activate **Mouser Integration** and open its settings.
After activating, run `inventree run invoke update` once more (or restart, if migrations run on
startup) so its tables are created.

Other plugins that use this one (e.g. OMG Harness Import) ask for a minimum version
(`inventree-mouser>=0.1.0`), never a git pin, so this plugin updates on its own.
InvenTree needs *Enable app integration*, *Enable URL integration*, *Enable interface
integration* and *Enable schedule integration* on (Admin Center → Plugin Settings), and its
background worker running for the daily check.

## Set up

1. **Keys** — My Mouser → APIs. Mouser gives two different keys:
   - **Search API key** → `Search API key` (part search, creating parts)
   - **Order API key** → `Order API key` (reading your orders, filling carts)
2. **Supplier** — pick the Company that is Mouser in your InvenTree.
3. **Order import** — set `Import orders placed from` (YYYY-MM-DD). Orders before it are
   never imported, so purchase orders you already typed in by hand aren't doubled up.
   Then switch on `Import Mouser orders daily`.
4. Optional: `Mouser website` (e.g. `au.mouser.com`), `Country code` / `Currency code`
   for cart pricing, `New parts category`, `Receive into`, `Responsible`.

Every setting is listed in [docs/SETTINGS.md](docs/SETTINGS.md).

The **Mouser Orders** dashboard item shows what was imported, what's waiting for you, and
has *Check Mouser now* and *Import order* (by sales or web order number — ignores the
start date, handy for trying it on one order).

## How an order is imported

Each line is matched to an InvenTree part, first match wins:

| | Match | Result |
|---|---|---|
| 1 | A Mouser supplier part with this Mouser part number | used as is |
| 2 | The line's customer part number (set by *Send to Mouser cart*) is one part's IPN / name | that part; a Mouser supplier part is added |
| 3 | Exactly one part *is* this part number — name, IPN, a manufacturer part's MPN (e.g. an equivalent you added as another manufacturer part), or any supplier SKU | that part; a Mouser supplier part is added |
| 3b | Several parts are | **held** — you pick |
| 4 | A part is *close* — same once case / spaces / dashes are ignored, or the same apart from a trailing `-suffix` (`DT04-12PA` vs `DT04-12PA-E005`) | **held** — you decide: that part, a new part, or leave the line off |
| 5 | Nothing | a new part is created from Mouser into `New parts category` |

**If any line is held, the whole order is held** and nothing is created — no half-built
purchase orders, and a held order never reaches anything downstream (like a Xero sync).
Once every line is settled the order goes through in one transaction: new parts, Mouser
supplier parts, then the purchase order with Mouser's unit prices and currency,
supplier reference = the Mouser sales order number, Mouser's additional fees as an extra
line, invoice numbers and tracking stored on the order. It is **placed** (or left
**pending** — `New purchase orders are`).

Also:
- A Mouser purchase order whose **supplier reference** is already this Mouser order number
  is linked and left exactly as it is.
- When the **PO number typed at Mouser checkout** is the reference of an open InvenTree
  purchase order (the cart loop below), *that* purchase order is updated instead: Mouser's
  prices, quantities (if nothing's been received), lines added at checkout, lines dropped
  at checkout noted.
- Cancelled Mouser orders are recorded as ignored. An error is recorded with its reason
  and tried again on the next check.

## Send to Mouser cart

On a Mouser purchase order, the **Mouser** panel has *Send to Mouser cart*. Lines with a
Mouser supplier part (less anything already received) go into a new cart on your Mouser
account, with each part's IPN (or name) as the customer part number. Mouser's cart price
is copied to lines with no price (or every line — `Copy cart prices to the purchase order`).

Mouser keeps carts made through its API as separate saved carts. Sending the same purchase
order again replaces that cart's contents rather than making another; a new cart is made only
if Mouser no longer has it (checked out or deleted). On Mouser, open your saved carts, pick the
one with the key the panel shows, and check out, typing the purchase order's reference (e.g.
`PO-0042`) as the PO number. The next order check finds it and updates that purchase order.

Mouser needs a country code on cart calls: `Country code`, else worked out from
`Currency code`, else from InvenTree's default currency (AUD → AU).

## With the Xero plugin

If [inventree-xero](https://github.com/SXSLYDA/inventree-xero) exports placed purchase
orders, imported Mouser orders go to Xero as soon as they're placed (held orders don't,
nothing exists yet). With Xero bills made from received stock, don't also enter Mouser's
invoice into Xero by hand — the invoice number is on the purchase order (Mouser panel)
to check against.

## For other plugins: hooks

A plugin can add its own data to parts made from Mouser by defining methods (no
registration — any active plugin with the method is called; see `inventree_mouser/hooks.py`):

```python
def mouser_import_parameters(self, data):
    """[(template name, value)] to offer in the Import from Supplier wizard (offered first)."""

def mouser_part_created(self, part, data, source):
    """A part was just created from Mouser outside the wizard (source "order"). Return notes, or None."""
```

`mouser_part_created` is called after the purchase order is committed (outside the
order's lock, so it can make network calls) and only for parts really created - never
for an existing part the import reused. `data` is a `mouser_lookup` search result
(including Mouser's product `category`). Other plugins can also create parts the same
way this one does through `registry.get_plugin('mouser')` — `get_import_data('mouser', pn)`,
`import_part(...)`, `import_manufacturer_part(...)`, `import_supplier_part(...)`.

## mouser_lookup (no InvenTree needed)

```python
from datetime import date
from mouser_lookup import search_by_mpn, orders, cart

search_by_mpn("DT04-12PA", search_key)                       # normalized listings
orders.order_history(order_key, date(2026, 10, 1), date.today())
orders.order_detail(order_key, sales_order="282378302")     # lines, prices, invoices, tracking
cart.add_items(order_key, [{"mouser_pn": "571-DT04-2P-E005", "quantity": 100}], country="AU", currency="AUD")
```

Install just for this: `pip install "inventree-mouser @ git+https://github.com/SXSLYDA/inventree-mouser.git@vX.Y.Z"`
(the plugin part only loads inside InvenTree).

## Releasing

1. Note changes under `## Unreleased` in `CHANGELOG.md` (say when a release adds a migration).
2. `.\build-and-bump.ps1` (patch), `-Bump minor`, or `-Version X.Y.Z` — runs lint and tests,
   bumps `inventree_mouser/version.py`, stamps the changelog, commits and tags `vX.Y.Z`.
3. Push the commit **and the tag**. CI runs the unit, UI and end-to-end tests (against
   real InvenTree 1.5.1, 1.6.0 and 1.6.1) and publishes the GitHub release.
4. On the server: `sudo inventree-mouser-update vX.Y.Z` (install the script once — see the
   top of `tools/server-update.sh`), or move the `plugins.txt` pin and `invoke update`.
   Deploying from CI is off unless the `MOUSER_DEPLOY` variable and the SSH secrets are set
   (see the `deploy` job in `.github/workflows/ci.yml`).

## Tests

```bash
pip install -e . pytest pyflakes django
pytest -q tests/test_lookup.py                       # parsing + matching rules
npm install --no-save jsdom@24 && node --test tests/ui.test.mjs
# end-to-end against a real InvenTree, fake Mouser account (see the e2e job in CI):
MOUSER_TESTS=$PWD/tests python manage.py shell -c "exec(open('$PWD/tests/e2e_inventree.py').read())"
```

The end-to-end test uses a real Mouser order (`tests/fixtures/order_282378302.json`).

## Licence

MIT
