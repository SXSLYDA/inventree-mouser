"""
Purchase order -> Mouser cart ("Send to Mouser cart" on a purchase order's
Mouser panel).

Every line whose supplier part is a Mouser part goes into a cart on your
Mouser account, with the part's IPN (or name) as the customer part number.
Mouser keeps API carts as separate saved carts: the first send makes one,
sending the same purchase order again replaces that cart's contents (so you
don't pile up carts), and a new one is made only if Mouser has dropped it.
You check out on Mouser's website and type this purchase order's reference
(e.g. PO-0042) as the PO number - the order import then finds the order by
that number and updates this purchase order instead of making a new one,
and the customer part numbers make each line match exactly.

Mouser's cart prices are copied onto lines with no price (or onto every
line, with "Copy cart prices to the purchase order" on).
"""

from django.utils import timezone

from mouser_lookup import MouserError, cart

from .order_import import OrderImporter


def mouser_lines(po, mouser):
    """The purchase order's lines that are Mouser supplier parts, and the others."""
    lines, others = [], []
    for line in po.lines.select_related('part', 'part__part'):
        if line.part and line.part.supplier_id == mouser.pk and line.part.SKU:
            lines.append(line)
        else:
            others.append(line)
    return lines, others


def customer_part_number(part):
    return (part.IPN or part.name or '').strip()


def country_code(importer):
    """
    Mouser needs a country code on cart calls (it decides the cart's currency and
    pricing): the Country code setting, else the one for the Currency code
    setting, else for InvenTree's default currency.
    """
    from common.currency import currency_code_default

    code = (importer.setting('COUNTRY_CODE', '') or '').strip().upper()
    if code:
        return code
    currency = (importer.setting('CURRENCY_CODE', '') or '').strip().upper() or currency_code_default()
    code = cart.CURRENCY_COUNTRIES.get(currency)
    if not code:
        raise MouserError(f'Set the Country code setting - Mouser needs one for carts, and {currency} '
                          'doesn\'t give it.')
    return code


def push(plugin, po):
    """Put the purchase order into a Mouser cart. Returns what the panel shows."""
    importer = OrderImporter(plugin)
    mouser = importer.mouser
    key = importer.order_key
    if not key:
        raise MouserError('Set the Mouser Order API key first (plugin settings).')
    if po.supplier_id != mouser.pk:
        raise MouserError('This purchase order is not from Mouser.')
    lines, others = mouser_lines(po, mouser)
    if not lines:
        raise MouserError('No lines with a Mouser supplier part to send.')

    items = []
    for line in lines:
        quantity = int(line.quantity - (line.received or 0))
        if quantity > 0:
            items.append({'mouser_pn': line.part.SKU, 'quantity': quantity,
                          'customer_pn': customer_part_number(line.part.part)})
    if not items:
        raise MouserError('Everything on this purchase order has been received - nothing to send.')

    country = country_code(importer)
    currency = (importer.setting('CURRENCY_CODE', '') or '').strip().upper() or None
    old_key = ((po.metadata or {}).get('mouser') or {}).get('cart_key') or ''
    reused = False
    if old_key:
        try:
            result = cart.replace_items(key, old_key, items, country=country, currency=currency)
            reused = result['cart_key'].lower() == old_key.lower()
        except MouserError:
            result = None  # Mouser dropped that cart (checked out or deleted) - a new one below
    if not reused:
        result = cart.add_items(key, items, country=country, currency=currency)

    # Mouser's cart prices onto the purchase order lines
    copy_all = bool(plugin.get_setting('CART_UPDATE_PRICES'))
    by_sku = {ln['mouser_pn'].upper(): ln for ln in result['lines']}
    priced = 0
    for line in lines:
        found = by_sku.get(line.part.SKU.upper())
        if not found or found['unit_price'] is None or found['errors']:
            continue
        if line.purchase_price is None or copy_all:
            price = importer.money(found['unit_price'], result['currency'])
            if price is not None:
                line.purchase_price = price
                line.save()
                priced += 1

    meta = dict(po.metadata or {})
    meta['mouser'] = {**(meta.get('mouser') or {}), 'cart_key': result['cart_key'],
                      'cart_sent': timezone.now().isoformat(),
                      'cart_total': str(result['merchandise_total'] or ''), 'cart_currency': result['currency']}
    po.metadata = meta
    po.save()

    site = (importer.setting('MOUSER_SITE', 'www.mouser.com') or 'www.mouser.com').strip().strip('/')
    if '://' not in site:
        site = f'https://{site}'
    return {
        'cart_key': result['cart_key'],
        'reused': reused,
        'cart_url': f'{site}/Cart/',
        'currency': result['currency'],
        'merchandise_total': str(result['merchandise_total'] or ''),
        'lines': [{**ln, 'unit_price': str(ln['unit_price'] or ''), 'ext_price': str(ln['ext_price'] or '')}
                  for ln in result['lines']],
        'problems': [f"{ln['mouser_pn']}: {'; '.join(ln['errors'])}" for ln in result['lines'] if ln['errors']],
        'not_sent': [ln.part.SKU if ln.part else '(no supplier part)' for ln in others],
        'prices_copied': priced,
        'po_number': po.reference,
    }
