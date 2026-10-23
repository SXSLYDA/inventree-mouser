# Changelog

Add notes for the next release under **Unreleased** as you work. `build-and-bump.ps1` turns that heading into the version number and date when you release, and CI publishes a GitHub release with those notes when the tag is pushed. Say when a release adds a migration - that decides whether rolling back to the previous tag is safe.

## Unreleased

First release. Takes over the Mouser parts of the OMG Harness Import plugin and the `mouser_shared` (`mouser-lookup`) package.

**Adds a migration** (`inventree_mouser.0001_initial`: Mouser orders, their lines, and order checks).

- **Import from Supplier wizard** (moved from OMG Harness Import's `mouser_supplier.py`, same behaviour): Mouser search, parts named by MPN, manufacturer part, Mouser supplier part, price breaks, optional picture download (browser headers for Mouser's image server). Settings are now `Search API key` and `Download part images` on this plugin, and its own `Supplier`. Parameters from other plugins come in through the new `mouser_import_parameters` hook. Price breaks are only replaced when the new data has some, and descriptions are cut to InvenTree's 250 characters.
- **Mouser orders → purchase orders**: daily check (`Import Mouser orders daily`, `Check at hour`, `Import orders placed from`, `Look back`) plus *Check Mouser now* and *Import order* by number on the new **Mouser Orders** dashboard item. Lines match a Mouser supplier part, the customer part number, or one exact part number; a close or several-way match holds the whole order until a person picks per line (use a part, search for another, create new, leave off). Purchase orders get Mouser's unit prices and currency, the Mouser sales order number as supplier reference, fees as an extra line, invoice numbers and tracking. Placed or pending (setting). An order already entered by hand (supplier reference) is linked and left alone; an order whose checkout PO number is an open InvenTree purchase order updates that order.
- **Send to Mouser cart** on a Mouser purchase order's new **Mouser** panel - with customer part numbers so the order comes back matched; Mouser's cart prices onto unpriced lines (or all, setting). Sending the same purchase order again updates its cart instead of making another (a new one only if Mouser has dropped it). A country code is always sent (Mouser needs one): the setting, else from the currency. The panel shows the cart key, to pick the right saved cart on Mouser.
- Mouser price breaks are read right whatever the format ("$1,234.50", "1,45 €") - the old parser read "$1,234.50" as 1. Bare `{"Message": ...}` answers from Mouser are reported as errors.
- `mouser_lookup` (moved from `mouser_shared`, same `search_by_mpn` / `map_mouser_response` / `prefill`): new `orders` (Order History API) and `cart` (Cart API) modules and `MouserError`. The version is now this distribution's.
- Hooks for other plugins: `mouser_import_parameters(data)` and `mouser_part_created(part, data, source)`.
- Tests: unit (`tests/test_lookup.py`), UI in jsdom (`tests/ui.test.mjs`) and end-to-end against real InvenTree with a fake Mouser account (`tests/e2e_inventree.py`), all in CI.
