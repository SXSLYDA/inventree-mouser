# Changelog

Add notes for the next release under **Unreleased** as you work. `build-and-bump.ps1` turns that heading into the version number and date when you release, and CI publishes a GitHub release with those notes when the tag is pushed. Say when a release adds a migration - that decides whether rolling back to the previous tag is safe.

## Unreleased

## 0.1.7 — 2026-10-30

- **Receive by Mouser invoice** on a purchase order's Mouser panel: pick the invoice that came in the box, its lines are listed (quantity filled in when the line only ships once - enter it when the line ships in parts), choose a location and confirm. The stock is received with InvenTree's own receiving (all or nothing, never more than is still to come), and the invoice is recorded on the purchase order as `metadata['supplier_invoices']` - number, date, due date, lines and quantities, and the fees that relate to it (a line's fees on the invoice it first shipped on, order-level fees on the first invoice). The Xero plugin bills each invoice on its own from that (inventree-xero, next release). Purchase orders the import makes or updates are marked to be billed this way from the start.
- New settings **Mouser payment** (*Paid on order (card)*: due on the invoice date; *On account*: due after **Account terms (days)**, default 30) - the due date recorded with each invoice.
- Per-line additional fees from Mouser are now read (`mouser_lookup`: each line's `fees`).

- **Back orders and split shipments.** Each line now keeps its own list of shipments from Mouser (date and invoice - a back-ordered line has none yet, a line sent in parts has several), and the Mouser panel on a purchase order (and the order on the Mouser Orders lists) has a **Shipping** column: each shipment, "Not shipped yet", and "Received x / y" from the purchase order. The order lists show "3 / 5 lines shipped" until every line has gone, and the order shows Mouser's back-order shipping method. The purchase order still has the full quantities ordered - receive each part as it arrives with InvenTree's Receive Items. Mouser's API gives each shipment's invoice and date but not how many were in it (if Mouser does send a quantity, it's shown).
- An order is only treated as finished - and no longer checked with Mouser - once Mouser shows it complete **and every line has shipped**; one with a back order outstanding keeps being refreshed. New setting **Keep updating for (days)** (default 180, was a fixed 90) so long back orders are still followed.
- New shipments are noted on the order line by line ("650-RKEF110 shipped - invoice 93100002, 02-11-2026; still to ship: …").
- **Mouser cutting a line** (e.g. cancelling the rest of a back order - "Mouser now has 30 of the 50 ordered") or cancelling the whole order after it was imported is flagged on the order and sent as a new notification ("Mouser order … changed on Mouser", linking to the purchase order). The purchase order isn't changed or cancelled for you.

- **Imported orders stay up to date with Mouser.** Until now an order was read from Mouser once, when its purchase order was made, so one imported while still open never got its invoice number, tracking or final status. Each check now refreshes orders that already have a purchase order - Mouser's status, invoice numbers, tracking and totals, on the Mouser order and in the purchase order's Mouser details - for 90 days after they were placed (also outside the look-back window, at most 40 a check), until Mouser shows them complete and invoiced; after that Mouser isn't asked again unless its order list shows a new status. Changes are noted on the order ("28-10-2026: Mouser status now Complete; invoice 93000014; tracking 1Z…") and the check summary counts them as "updated from Mouser". Purchase order lines, quantities, prices, status and receiving are left alone. An order cancelled on Mouser after it was imported is flagged ("CANCELLED on Mouser - cancel PO-0012 in InvenTree if that's right"); the purchase order isn't cancelled for you. A refresh that fails is just tried again next check.

## 0.1.6 — 2026-10-28

- **Orders Mouser hasn't processed yet no longer fail the check.** A new order has only a web order number until Mouser processes it and gives it a sales order number. The check asked Mouser for its details by the (blank) sales order number, so each one showed as failed - "Problems: : Give a sales order number or a web order number." - and set off a "check failed" notification. Such an order is now counted as "not processed by Mouser yet" and left for a later check, which imports it once it has its sales order number. *Import order* with its web order number says the same instead of storing it under a made-up number. An order an earlier version stored under its web order number (`W…`) is moved to its sales order number (and its purchase order's supplier reference with it) rather than imported again.

## 0.1.5 — 2026-10-27

- **Part links open the part in the same tab.** Links on the Mouser Orders dashboard / Purchasing panel / purchase order panel went to `/web/web/part/473` (InvenTree's router adds `/web` itself) - a page that doesn't exist, which sent you off to log in again. A plain click now opens the part (or purchase order) in place; ctrl-, shift- or middle-click opens it in a new tab.
- **No more "timeout of 5000ms exceeded"** when choosing a part or creating a new one on a held order. InvenTree's API client gives up after 5 seconds, but creating a part from Mouser (search, picture, supplier part) and making the purchase order takes longer - the part was made, only the panel stopped waiting. The panels now wait up to 3 minutes for changes and 30 seconds for reads, and if even that runs out they say it may still finish on the server rather than showing an error.

## 0.1.4 — 2026-10-26

- **Notification links fixed.** A notification about no particular record was attached by InvenTree to its UI-notification plugin, so clicking it opened that plugin's settings ("plugin 38 - not configured"). Notifications are now about the Mouser order / the check / the purchase order: "needs you", "could not be imported" and "check failed" open **Purchasing → Mouser Orders**, an imported order opens its purchase order. Who sees them still follows purchase-order permissions. Notifications already in the list keep their old link - mark them read.

## 0.1.3 — 2026-10-25

- **Mouser Orders panel on the Purchasing page** (Purchasing → Mouser Orders): every imported Mouser order with its status, purchase order and total, filter buttons (All / Needs you / Purchase orders / Ignored), and the same Check Mouser now, Import order and per-line choices as the dashboard item. Only shown to users who can see purchase orders.

- **InvenTree notifications** (the bell in the top bar) for the order import: a check of Mouser that fails (e.g. a key Mouser hasn't accepted - with Mouser's reason), an order waiting for you to choose parts, an order that couldn't be imported - each at most once a day, however many hourly retries - and, unless switched off, each Mouser order made into a purchase order (links to it). New settings **Notifications** (on), **Notify** (users / groups; blank = everyone who may add purchase orders) and **Notify imported orders** (on).

## 0.1.2 — 2026-10-24

- **Dates are DD-MM-YYYY.** "Import orders placed from" is now entered as DD-MM-YYYY (e.g. 24-10-2026) and anything else is refused when saved; the Mouser Orders dashboard and the check summaries show dates the same way (times as HH:MM). A start date already saved as YYYY-MM-DD no longer counts - the daily check says to set it - so enter it again as DD-MM-YYYY after updating.

## 0.1.1 — 2026-10-24

- **Works on InvenTree 1.5** (1.5.1 and newer - was 1.6.0+, so InvenTree 1.5.1 refused to load it and it stayed inactive with no tables). The end-to-end test now runs on InvenTree 1.5.1 as well as 1.6.0 / 1.6.1, and passes on all three. The first migration now depends only on migrations InvenTree 1.5.1 has; on a server where 0.1.0 never loaded (its tables were never made) nothing else changes.

## 0.1.0 — 2026-10-23

First release. Takes over the Mouser parts of the OMG Harness Import plugin and the `mouser_shared` (`mouser-lookup`) package.

**Adds a migration** (`inventree_mouser.0001_initial`: Mouser orders, their lines, and order checks).

- **Import from Supplier wizard** (moved from OMG Harness Import's `mouser_supplier.py`, same behaviour): Mouser search, parts named by MPN, manufacturer part, Mouser supplier part, price breaks, optional picture download (browser headers for Mouser's image server). Settings are now `Search API key` and `Download part images` on this plugin, and its own `Supplier`. Parameters from other plugins come in through the new `mouser_import_parameters` hook. Price breaks are only replaced when the new data has some, and descriptions are cut to InvenTree's 250 characters.
- **Mouser orders → purchase orders**: daily check (`Import Mouser orders daily`, `Check at hour`, `Import orders placed from`, `Look back`) plus *Check Mouser now* and *Import order* by number on the new **Mouser Orders** dashboard item. Lines match a Mouser supplier part, the customer part number, or one exact part number; a close or several-way match holds the whole order until a person picks per line (use a part, search for another, create new, leave off). Purchase orders get Mouser's unit prices and currency, the Mouser sales order number as supplier reference, fees as an extra line, invoice numbers and tracking. Placed or pending (setting). An order already entered by hand (supplier reference) is linked and left alone; an order whose checkout PO number is an open InvenTree purchase order updates that order.
- **Send to Mouser cart** on a Mouser purchase order's new **Mouser** panel - with customer part numbers so the order comes back matched; Mouser's cart prices onto unpriced lines (or all, setting). Sending the same purchase order again updates its cart instead of making another (a new one only if Mouser has dropped it). A country code is always sent (Mouser needs one): the setting, else from the currency. The panel shows the cart key, to pick the right saved cart on Mouser.
- Mouser price breaks are read right whatever the format ("$1,234.50", "1,45 €") - the old parser read "$1,234.50" as 1. Bare `{"Message": ...}` answers from Mouser are reported as errors.
- `mouser_lookup` (moved from `mouser_shared`, same `search_by_mpn` / `map_mouser_response` / `prefill`): new `orders` (Order History API) and `cart` (Cart API) modules and `MouserError`. The version is now this distribution's.
- Hooks for other plugins: `mouser_import_parameters(data)` and `mouser_part_created(part, data, source)`.
- **The Mouser API keys never appear in messages or logs.** Mouser takes the key in the URL, and a network error's text includes the URL - so "Couldn't reach Mouser" on the dashboard (and the order's message) showed the key. Every error is now cleaned (`mouser_lookup.http.redact`) and the original isn't chained into tracebacks; part search goes through the same `http.request` as orders and carts, so its logged errors are cleaned too.
- A part with a Mouser supplier part entered by hand (no manufacturer part) can be imported again: the supplier part is found by part + supplier + SKU, as InvenTree keeps them unique, and its missing manufacturer part / link are filled in - before, a second supplier part was attempted and InvenTree refused it.
- A daily check that failed (Mouser or the network down) is tried again on the next hourly tick instead of waiting until tomorrow; one still running isn't started twice.
- `mouser_part_created` is called once the purchase order is committed, and only for parts the import really created - an existing part reused by part number isn't handed to other plugins (whose data could overwrite what's on it).
- Search results carry Mouser's product category (`category`), so other plugins can tell a connector from a fuse.
- Tracking links on the dashboard are only made links when they're web addresses.
- Tests: unit (`tests/test_lookup.py`), UI in jsdom (`tests/ui.test.mjs`) and end-to-end against real InvenTree with a fake Mouser account (`tests/e2e_inventree.py`), all in CI.

