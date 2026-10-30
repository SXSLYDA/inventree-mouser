"""
Receive a Mouser shipment by its invoice ("Receive by Mouser invoice" on a
purchase order's Mouser panel).

Mouser sends a back order or a big order in several boxes, each with its own
invoice and date, and its API says which lines are on which invoice but not
how many of each. So the person with the box picks its invoice, the lines on
it are listed - the quantity filled in when the line only ships once, left
for them to enter when it ships in parts - and on confirming:

  1. the quantities are received into stock with InvenTree's own receiving
     (stock items, tracking, the PO's received counts - as Receive Items does),
  2. the invoice is recorded on the purchase order as metadata
     "supplier_invoices": one entry per supplier invoice - number, date, due
     date, the PO lines and quantities on it, and the fees that relate to it.

That record is what the Xero plugin (inventree-xero) bills from: one bill per
supplier invoice, dated the invoice date. Any plugin can write the same shape;
see SUPPLIER_INVOICES_KEY below.

Fees: Mouser's per-line additional fees (tariffs, handling) go on the invoice
the line first shipped on; order-level fees Mouser doesn't put on a line go on
the first invoice received for the order. GST isn't a fee here - Xero adds it
to each bill from the tax type.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from mouser_lookup import MouserError

from .dates import format_date
from .models import MouserOrder

# metadata key on the PurchaseOrder - shared with the Xero plugin:
#   [{"number": "93100001", "date": "2026-10-24", "due_date": "2026-11-23", "source": "mouser",
#     "lines": [{"line": <PurchaseOrderLineItem pk>, "quantity": 40, "unit_price": "4.94",
#                "description": "571-DT04-12PA-E005"}],
#     "extra": [{"description": "Mouser fee TARIFF - 571-...", "amount": "3.20"}],
#     "currency": "AUD", "received": "<iso time>", "received_by": "tyler"}]
SUPPLIER_INVOICES_KEY = 'supplier_invoices'


def supplier_invoices(po):
    return list((po.metadata or {}).get(SUPPLIER_INVOICES_KEY) or [])


def _decimal(value):
    try:
        return Decimal(str(value)) if value not in (None, '') else Decimal(0)
    except Exception:
        return Decimal(0)


def due_date(plugin, invoice_date):
    """Paid on order: due on the invoice date. On account: invoice date + the terms."""
    if plugin.get_setting('PAYMENT_TERMS') != 'account':
        return invoice_date
    try:
        days = max(int(plugin.get_setting('ACCOUNT_TERMS_DAYS')), 0)
    except (TypeError, ValueError):
        days = 30
    return invoice_date + timedelta(days=days)


def mouser_order_for(po):
    return MouserOrder.objects.filter(purchase_order=po).first()


def _po_line(po, mouser_line, order_lines):
    """The purchase order line a Mouser order line is: its supplier part, else the Mouser part number as SKU."""
    lines = list(po.lines.select_related('part', 'part__part'))
    known = order_lines.get(mouser_line.get('line_no'))
    if known is not None and known.supplier_part_id:
        for pl in lines:
            if pl.part_id == known.supplier_part_id:
                return pl
    pn = (mouser_line.get('mouser_pn') or '').lower()
    for pl in lines:
        if pl.part and (pl.part.SKU or '').lower() == pn:
            return pl
    return None


def invoices(po):
    """
    The Mouser invoices for a purchase order, for the panel:
    [{number, date, received (already recorded), lines: [{line, mouser_pn, part, ordered, received,
      on_invoice (recorded so far on this invoice), parts (shipments of this line), suggested}], fees}]
    """
    order = mouser_order_for(po)
    if order is None:
        return []
    detail = order.detail or {}
    order_lines = {ln.line_no: ln for ln in order.lines.all()}
    recorded = {inv['number']: inv for inv in supplier_invoices(po)}
    found = {}
    for ml in detail.get('lines') or []:
        shipments = ml.get('shipments') or [{'invoice': i, 'date': ''} for i in ml.get('invoices') or []]
        pl = _po_line(po, ml, order_lines)
        for sh in shipments:
            number = sh.get('invoice')
            if not number:
                continue
            inv = found.setdefault(number, {'number': number, 'date': sh.get('date') or '', 'lines': [],
                                            'received': number in recorded})
            if not inv['date'] and sh.get('date'):
                inv['date'] = sh['date']
            on_invoice = sum(_decimal(x['quantity']) for x in (recorded.get(number) or {}).get('lines') or []
                             if pl is not None and x.get('line') == pl.pk)
            remaining = (_decimal(pl.quantity) - _decimal(pl.received)) if pl is not None else Decimal(0)
            parts = len(shipments)
            inv['lines'].append({
                'line': pl.pk if pl is not None else None,
                'mouser_pn': ml.get('mouser_pn') or '',
                'mpn': ml.get('mpn') or '',
                'part': pl.part.part.name if pl is not None and pl.part and pl.part.part else '',
                'ordered': float(pl.quantity) if pl is not None else None,
                'received': float(pl.received) if pl is not None else None,
                'on_invoice': float(on_invoice),
                'parts': parts,
                # a line on one invoice only: whatever is still to come is in this box
                'suggested': float(max(remaining, Decimal(0))) if pl is not None and parts == 1 and not on_invoice else None,
            })
    return sorted(found.values(), key=lambda inv: (inv['date'], inv['number']))


def _fees_for(order, number, already_recorded):
    """Fees that relate to invoice `number`: its lines' fees (first shipment), plus order-level fees on the first invoice."""
    detail = order.detail or {}
    extra, line_fees_total = [], Decimal(0)
    for ml in detail.get('lines') or []:
        fees = [f for f in ml.get('fees') or [] if _decimal(f.get('amount'))]
        line_fees_total += sum(_decimal(f.get('amount')) for f in fees)
        shipments = ml.get('shipments') or [{'invoice': i} for i in ml.get('invoices') or []]
        if fees and shipments and shipments[0].get('invoice') == number:
            for f in fees:
                extra.append({'description': f"Mouser fee {f.get('code') or ''} - {ml.get('mouser_pn') or ''}".replace('  ', ' '),
                              'amount': str(_decimal(f.get('amount')))})
    order_fees = _decimal(((detail.get('totals') or {}).get('fees')))
    if not already_recorded and order_fees - line_fees_total > Decimal('0.005'):
        extra.append({'description': f'Mouser fees - order {order.sales_order}',
                      'amount': str(order_fees - line_fees_total)})
    return extra


def receive(plugin, po, number, quantities, location=None, user=None):
    """
    Receive what's in the box for Mouser invoice `number` and record the invoice on the PO.
    quantities: [{"line": <PO line pk>, "quantity": n}] (0 / blank lines are skipped).
    All or nothing. Returns the recorded invoice entry.
    """
    from order.status_codes import PurchaseOrderStatus
    from stock.models import StockLocation

    if po.status != PurchaseOrderStatus.PLACED.value:
        raise MouserError(f'{po.reference} isn\'t placed - only a placed purchase order can be received.')
    listed = {inv['number']: inv for inv in invoices(po)}
    inv = listed.get(str(number))
    if inv is None:
        raise MouserError(f'Mouser hasn\'t shown invoice {number} on this order (yet) - try Check Mouser now.')
    allowed = {ln['line'] for ln in inv['lines'] if ln['line']}

    wanted = []
    for q in quantities or []:
        try:
            amount = Decimal(str(q.get('quantity') or 0))
        except Exception:
            raise MouserError(f"'{q.get('quantity')}' isn't a quantity.")
        if amount <= 0:
            continue
        if q.get('line') not in allowed:
            raise MouserError('A line that isn\'t on this invoice was given.')
        pl = po.lines.get(pk=q['line'])
        remaining = _decimal(pl.quantity) - _decimal(pl.received)
        if amount > remaining:
            raise MouserError(f'{pl.part.SKU}: only {remaining:g} still to receive on {po.reference}, not {amount:g}.')
        wanted.append((pl, amount))
    if not wanted:
        raise MouserError('Enter how many of each line are in the box.')

    picked = StockLocation.objects.filter(pk=location).first() if location else None

    with transaction.atomic():
        po = type(po).objects.select_for_update().get(pk=po.pk)
        entries = supplier_invoices(po)
        entry = next((e for e in entries if e.get('number') == inv['number']), None)
        if entry is None:
            day = date.fromisoformat(inv['date']) if inv['date'] else timezone.localdate()
            entry = {'number': inv['number'], 'date': day.isoformat(), 'due_date': due_date(plugin, day).isoformat(),
                     'source': 'mouser', 'currency': po.order_currency or '', 'lines': [],
                     'extra': _fees_for(mouser_order_for(po), inv['number'], bool(entries))}
            entries.append(entry)
        for pl, amount in wanted:
            row = next((x for x in entry['lines'] if x.get('line') == pl.pk), None)
            if row is None:
                entry['lines'].append({'line': pl.pk, 'quantity': str(amount), 'description': pl.part.SKU,
                                       'unit_price': str(pl.purchase_price.amount) if pl.purchase_price is not None else ''})
            else:
                row['quantity'] = str(_decimal(row['quantity']) + amount)
        entry['received'] = timezone.now().isoformat()
        entry['received_by'] = getattr(user, 'username', '') or ''
        meta = dict(po.metadata or {})
        meta[SUPPLIER_INVOICES_KEY] = entries
        po.metadata = meta
        po.save()

        for pl, amount in wanted:
            where = picked or pl.destination or po.destination or (pl.part.part.get_default_location()
                                                                   if pl.part and pl.part.part else None)
            if where is None:
                raise MouserError(f'Choose where to receive {pl.part.SKU} - it has no default location.')
            po.receive_line_item(pl, where, amount, user, notes=f'Mouser invoice {inv["number"]} '
                                                               f'({format_date(entry["date"])})')
    return entry
