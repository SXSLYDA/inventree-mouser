"""
Mouser orders -> InvenTree purchase orders.

Once a day (and from the "Mouser Orders" dashboard item) the plugin asks
Mouser's Order History API for orders since the start date, and for each
order it hasn't seen:

  1. Every line is matched to an InvenTree part (matching.py).
  2. The hybrid rule: if any line is a "possible" or "ambiguous" match, the
     WHOLE order is held for review and nothing is created - so there's never
     a half-built purchase order, and a held order never reaches Xero.
     A person picks the part (or "create new" / "leave off") per line on the
     dashboard item, then the order goes through.
  3. Otherwise, in one transaction: parts that don't exist are created from
     Mouser (into the "New parts category", same methods as the Import from
     Supplier wizard), a Mouser supplier part is added where a part had none,
     and the purchase order is made with Mouser's actual unit prices and
     currency, supplier reference = the Mouser sales order number. It is
     placed (setting) - receiving the stock stays a person's job, through
     InvenTree's own Receive Items.

Purchase orders already in InvenTree:
  - a Mouser purchase order whose supplier reference is this Mouser order
    number (entered by hand) -> linked, left exactly as it is.
  - the PO number typed at Mouser checkout is an InvenTree purchase order
    reference (e.g. a cart sent from that order) -> that purchase order is
    updated instead of a new one: Mouser's prices, quantities (nothing
    received yet), missing lines added, lines not on the Mouser order noted.

A cancelled Mouser order is recorded as ignored. Errors are recorded as
failed and tried again on the next check.
"""

import logging
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from mouser_lookup import MouserError, orders

from . import hooks, notify
from .dates import parse_start_date
from .matching import match_line
from .supplier import existing_purchaseable_part
from .models import ImportRun, MouserOrder, MouserOrderLine

logger = logging.getLogger('inventree')

NEW_PARTS_CATEGORY_NAME = 'Mouser Imports'
OPEN_PO_STATUSES = ('pending', 'placed', 'on_hold')


def jsonable(value):
    """A normalized Mouser order with Decimals as strings, for a JSONField."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def parse_date(text):
    """A date from Mouser's normalized order data (YYYY-MM-DD, see mouser_lookup.orders), else None."""
    text = (text or '').strip()
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        return None


def is_cancelled(status):
    return 'cancel' in (status or '').lower()


class OrderImporter:
    """Everything the order import does, for one plugin instance."""

    def __init__(self, plugin):
        self.plugin = plugin

    # ------------------------------------------------------------------ settings
    def setting(self, key, default=None):
        value = self.plugin.get_setting(key)
        return default if value in (None, '') else value

    @property
    def order_key(self):
        return self.setting('ORDER_API_KEY', '')

    @property
    def mouser(self):
        """The Company that is Mouser (SupplierMixin's Supplier setting)."""
        from company.models import Company
        pk = self.plugin.get_setting('SUPPLIER')
        company = Company.objects.filter(pk=pk).first() if pk else None
        if company is None:
            raise MouserError('Pick the Mouser company in the plugin\'s "Supplier" setting first.')
        return company

    @property
    def start_date(self):
        return parse_start_date(self.setting('ORDER_IMPORT_START', ''))

    def new_parts_category(self):
        from part.models import PartCategory
        pk = self.setting('NEW_PART_CATEGORY')
        category = PartCategory.objects.filter(pk=pk).first() if pk else None
        if category is None:
            category = (PartCategory.objects.filter(name=NEW_PARTS_CATEGORY_NAME, parent=None).first()
                        or PartCategory.objects.create(
                            name=NEW_PARTS_CATEGORY_NAME,
                            description='Parts created from Mouser orders - move each to its own category.'))
            self.plugin.set_setting('NEW_PART_CATEGORY', category.pk)
        return category

    def responsible_owner(self, user=None):
        """The purchase order's responsible owner: the PO_RESPONSIBLE setting (user or group name), else whoever pressed the button."""
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group

        from users.models import Owner

        name = (self.setting('PO_RESPONSIBLE', '') or '').strip()
        target = None
        if name:
            target = (get_user_model().objects.filter(username__iexact=name).first()
                      or Group.objects.filter(name__iexact=name).first())
        if target is None and user is not None and user.is_authenticated:
            # the real User, not request.user's lazy wrapper - InvenTree's notifications
            # check type(owner.owner) and send nothing (or fail) for a wrapper
            target = get_user_model().objects.filter(pk=user.pk).first()
        return Owner.create(obj=target) if target is not None else None

    # ------------------------------------------------------------------ checking Mouser
    def check(self, source='manual'):
        """Fetch Mouser's order list since the start date and import what's new. Returns the ImportRun."""
        run = ImportRun.objects.create(source=source)
        counts = Counter()
        problems = []
        try:
            if not self.order_key:
                raise MouserError('Set the Mouser Order API key first (plugin settings).')
            start = self.start_date
            if start is None:
                raise MouserError('Set "Import orders placed from" (plugin settings) - orders before it are never imported.')
            self.mouser  # noqa: B018 - raises if the Supplier setting is missing
            today = timezone.localdate()
            lookback = max(int(self.setting('ORDER_LOOKBACK_DAYS', 14)), 1)
            since = max(start, today - timedelta(days=lookback))
            rows = orders.order_history(self.order_key, since, today)
            seen = set()
            for row in rows:
                number = row['sales_order']
                seen.add(number)
                try:
                    state = self.import_summary(row, source)
                    counts[state] += 1
                except Exception as exc:  # one bad order never stops the rest
                    logger.exception('Mouser order %s failed', number)
                    problems.append(f'{number}: {exc}')
                    counts['failed'] += 1
            # held orders not in this window: parts may have been set up since
            for order in MouserOrder.objects.filter(state=MouserOrder.STATE_HELD).exclude(sales_order__in=seen):
                self.process(order)
            run.ok = not problems
            run.message = self.summary(counts, len(rows), since) + (' Problems: ' + '; '.join(problems) if problems else '')
        except Exception as exc:
            logger.exception('Mouser order check failed')
            run.ok = False
            run.message = str(exc)
        run.counts = dict(counts)
        run.finished = timezone.now()
        run.save()
        if not run.ok:
            notify.check_failed(self.plugin, run)
        return run

    @staticmethod
    def summary(counts, total, since):
        if not total:
            return f'No Mouser orders since {since:%d-%m-%Y}.'
        labels = {'imported': 'imported', 'linked': 'linked to existing POs', 'held': 'need review',
                  'ignored': 'ignored', 'failed': 'failed', 'done': 'already done', 'before_start': 'before the start date'}
        parts = [f'{n} {labels.get(k, k)}' for k, n in counts.items() if n]
        return f'{total} Mouser order(s) since {since:%d-%m-%Y}: ' + ', '.join(parts) + '.'

    def import_summary(self, row, source):
        """One row of Mouser's order list -> state word for the run summary."""
        order = MouserOrder.objects.filter(sales_order=row['sales_order']).first()
        if order and order.state in (MouserOrder.STATE_IMPORTED, MouserOrder.STATE_LINKED, MouserOrder.STATE_IGNORED):
            return 'done'
        if order and order.state == MouserOrder.STATE_HELD:
            return self.process(order).state
        start = self.start_date
        if start and row['order_date'] and parse_date(row['order_date']) and parse_date(row['order_date']) < start:
            return 'before_start'
        if is_cancelled(row['status']):
            MouserOrder.objects.update_or_create(sales_order=row['sales_order'], defaults={
                'web_order': row['web_order'], 'po_number': row['po_number'][:64],
                'order_date': parse_date(row['order_date']), 'mouser_status': row['status'][:60],
                'state': MouserOrder.STATE_IGNORED, 'message': 'Cancelled on Mouser.', 'source': source})
            return 'ignored'
        detail = orders.order_detail(self.order_key, sales_order=row['sales_order'])
        detail['po_number'] = detail['po_number'] or row['po_number']
        return self.process(self.store(detail, source)).state

    def import_number(self, number, user=None):
        """
        One order by its Mouser sales order or web order number (the dashboard's
        "Import an order" box) - regardless of the start date. Returns the MouserOrder.
        """
        number = (number or '').strip().lstrip('#')
        if not number:
            raise MouserError('Type a Mouser sales order or web order number.')
        if not self.order_key:
            raise MouserError('Set the Mouser Order API key first (plugin settings).')
        self.mouser  # noqa: B018
        order = MouserOrder.objects.filter(Q(sales_order=number) | Q(web_order=number)).first()
        if order and order.state != MouserOrder.STATE_FAILED:
            return self.process(order, user=user)
        try:
            detail = orders.order_detail(self.order_key, sales_order=number)
        except MouserError:
            detail = orders.order_detail(self.order_key, web_order=number)
        if is_cancelled(detail['status']):
            order = self.store(detail, 'by-number')
            order.state, order.message = MouserOrder.STATE_IGNORED, 'Cancelled on Mouser.'
            order.save()
            return order
        return self.process(self.store(detail, 'by-number'), user=user)

    # ------------------------------------------------------------------ storing
    def store(self, detail, source):
        """Save (or refresh) the MouserOrder and, the first time, its lines."""
        number = detail['sales_order'] or f"W{detail['web_order']}"
        order, created = MouserOrder.objects.get_or_create(sales_order=number[:40])
        order.web_order = detail['web_order'][:40]
        order.po_number = (detail['po_number'] or '')[:64]
        order.order_date = parse_date(detail['order_date'])
        order.mouser_status = detail['status'][:60]
        order.currency = detail['currency'][:3]
        order.buyer = detail['buyer'][:120]
        order.detail = jsonable(detail)
        if created:
            order.source = source
        if order.state == MouserOrder.STATE_FAILED:
            order.state = MouserOrder.STATE_HELD
        order.save()
        if created or not order.lines.exists():
            MouserOrderLine.objects.bulk_create([
                MouserOrderLine(order=order, line_no=line['line_no'], mouser_pn=line['mouser_pn'][:100],
                                mpn=line['mpn'][:100], manufacturer=line['manufacturer'][:120],
                                description=line['description'][:250], customer_pn=line['customer_pn'][:100],
                                quantity=max(int(line['quantity'] or 0), 0), unit_price=line['unit_price'])
                for line in detail['lines']
            ])
        return order

    # ------------------------------------------------------------------ the hybrid rule
    def rematch(self, order):
        """Match every line nobody has decided on yet (parts may have been added since)."""
        mouser = self.mouser
        for line in order.lines.all():
            if line.decision != MouserOrderLine.DECISION_AUTO:
                continue
            if line.quantity <= 0:
                line.decision, line.message = MouserOrderLine.DECISION_SKIP, 'Quantity 0 on the Mouser order.'
                line.save()
                continue
            found = match_line(line, mouser)
            line.match, line.part, line.supplier_part = found.match, found.part, found.supplier_part
            line.candidates, line.message = found.candidates, found.message
            line.save()

    def existing_by_reference(self, order):
        """A Mouser purchase order already carrying this Mouser order number as its supplier reference."""
        from order.models import PurchaseOrder
        q = Q(supplier_reference__iexact=order.sales_order)
        if order.web_order:
            q |= Q(supplier_reference__iexact=order.web_order)
        return PurchaseOrder.objects.filter(q, supplier=self.mouser).order_by('pk').first()

    def target_from_po_number(self, order):
        """The open InvenTree purchase order whose reference was typed as the PO number at Mouser checkout."""
        from order.models import PurchaseOrder
        from order.status_codes import PurchaseOrderStatus
        if not order.po_number:
            return None
        open_values = [getattr(PurchaseOrderStatus, s.upper()).value for s in OPEN_PO_STATUSES]
        return PurchaseOrder.objects.filter(reference__iexact=order.po_number.strip(), supplier=self.mouser,
                                            status__in=open_values).first()

    def process(self, order, user=None):
        """
        Apply the hybrid rule to one stored order (_process), then tell people
        (notify.py): it waits for them, it failed, or it became a purchase order.
        """
        if order.purchase_order_id or order.state in (MouserOrder.STATE_IMPORTED, MouserOrder.STATE_LINKED,
                                                       MouserOrder.STATE_IGNORED):
            return order
        order = self._process(order, user)
        if order.state == MouserOrder.STATE_HELD:
            notify.order_held(self.plugin, order)
        elif order.state == MouserOrder.STATE_FAILED:
            notify.order_failed(self.plugin, order)
        elif order.purchase_order_id and order.message.startswith(('Created ', 'Updated ')):
            notify.order_imported(self.plugin, order)
        return order

    def _process(self, order, user=None):
        """The hybrid rule for one stored order: hold it, or make / update its purchase order."""
        if order.purchase_order_id or order.state in (MouserOrder.STATE_IMPORTED, MouserOrder.STATE_LINKED,
                                                       MouserOrder.STATE_IGNORED):
            return order
        try:
            self.rematch(order)
            existing = self.existing_by_reference(order)
            if existing:
                order.state, order.purchase_order = MouserOrder.STATE_LINKED, existing
                order.message = (f'Already in InvenTree as {existing.reference} (its supplier reference is this '
                                 'Mouser order number) - left as it is.')
                order.save()
                return order

            lines = list(order.lines.select_related('part', 'supplier_part'))
            waiting = [ln for ln in lines if ln.needs_person]
            if waiting:
                order.state = MouserOrder.STATE_HELD
                order.message = (f'{len(waiting)} of {len(lines)} line(s) need you to choose the part - '
                                 'nothing is created until every line is settled.')
                order.save()
                return order

            target = self.target_from_po_number(order)
            notes = []
            created = []    # (part, data) for parts this import really created - for the hooks
            with transaction.atomic():
                # one at a time: the daily check and a person's button press can't both make a purchase order
                locked = MouserOrder.objects.select_for_update().get(pk=order.pk)
                if locked.purchase_order_id or locked.state not in (MouserOrder.STATE_HELD, MouserOrder.STATE_FAILED):
                    return locked
                resolved = []
                for line in lines:
                    if line.decision == MouserOrderLine.DECISION_SKIP:
                        continue
                    resolved.append((line, self.supplier_part_for(line, notes, created)))
                if not resolved:
                    raise MouserError('Every line is left off - nothing to order. Ignore the order instead.')
                if target:
                    self.update_po(target, order, resolved, notes, user)
                    order.state, order.purchase_order = MouserOrder.STATE_LINKED, target
                    head = f'Updated {target.reference} (the PO number given at Mouser checkout).'
                else:
                    po = self.create_po(order, resolved, notes, user)
                    order.state, order.purchase_order = MouserOrder.STATE_IMPORTED, po
                    head = f'Created {po.reference}.'
                order.message = ' '.join([head] + notes)
                order.save()
            # Other plugins' part data (hooks.part_created - OMG's parameters) only once the
            # purchase order is committed: a provider may call out over the network, which
            # mustn't happen while this order is locked, and a rolled-back import has no parts.
            hook_notes = [f'{part.name}: {note}' for part, data in created
                          for note in hooks.part_created(part, data, 'order')]
            if hook_notes:
                order.message = ' '.join([order.message] + hook_notes)
                order.save()
        except Exception as exc:
            logger.exception('Mouser order %s could not be imported', order.sales_order)
            order.refresh_from_db()
            order.state, order.message = MouserOrder.STATE_FAILED, f'Not imported: {exc}'
            order.save()
        return order

    # ------------------------------------------------------------------ parts
    def line_data(self, line):
        """A mouser_lookup-shaped dict from the order line alone (no extra API call)."""
        return {'mpn': line.mpn or line.mouser_pn, 'spn': line.mouser_pn, 'manufacturer': line.manufacturer,
                'description': line.description, 'url': '', 'image': '', 'category': '', 'attributes': {},
                'price_breaks': []}

    def search_data(self, line):
        """Mouser's full listing for the line (attributes, image, price breaks), else line_data()."""
        if self.setting('SEARCH_API_KEY', ''):
            try:
                data = self.plugin.get_import_data('mouser', line.mouser_pn or line.mpn)
                if data and data.get('mpn'):
                    return data
            except Exception:
                logger.info('Mouser search for %s failed - creating from the order line', line.mouser_pn)
        return self.line_data(line)

    def supplier_part_for(self, line, notes, created):
        """
        The Mouser SupplierPart this line orders - creating the part / manufacturer
        part / supplier part as needed. A part really created (not an existing one
        import_part reused) is added to created, for the hooks.
        """
        from company.models import SupplierPart

        if line.decision == MouserOrderLine.DECISION_AUTO and line.match == MouserOrderLine.MATCH_SUPPLIER_PART \
                and line.supplier_part_id:
            return line.supplier_part

        create = (line.decision == MouserOrderLine.DECISION_CREATE
                  or (line.decision == MouserOrderLine.DECISION_AUTO and line.match == MouserOrderLine.MATCH_NEW))
        if create:
            data = self.search_data(line)
            reused = existing_purchaseable_part(data.get('mpn'))
            part = self.plugin.import_part(data, category=self.new_parts_category(), creation_user=None)
            if reused is not None and reused.pk == part.pk:
                line.message = f'Used the existing part {part.name} (same part number).'
            else:
                line.message = f'New part {part.name} in {part.category.pathstring if part.category else "no category"}.'
                created.append((part, data))
        else:
            part = line.part
            if part is None:
                raise MouserError(f'Line {line.line_no} ({line.mouser_pn}) has no part chosen.')
            data = self.line_data(line)

        existing = (SupplierPart.objects.filter(supplier=self.mouser, part=part, SKU__iexact=line.mouser_pn).first()
                    if line.mouser_pn else None)
        if existing:
            supplier_part = existing
        else:
            manufacturer_part = self.plugin.import_manufacturer_part(data, part=part) if data.get('mpn') else None
            supplier_part = self.plugin.import_supplier_part(data, part=part, manufacturer_part=manufacturer_part)
            if not create:
                line.message = f'Added Mouser supplier part {supplier_part.SKU} to {part.name}.'
        line.part, line.supplier_part = part, supplier_part
        line.save()
        return supplier_part

    # ------------------------------------------------------------------ purchase orders
    def money(self, amount, currency):
        """Money in the order's currency, or None when InvenTree doesn't have that currency."""
        from djmoney.money import Money

        from common.currency import currency_codes
        if amount is None or not currency or currency not in currency_codes():
            return None
        return Money(Decimal(str(amount)), currency)

    def metadata(self, order):
        detail = order.detail or {}
        invoices = sorted({i for line in detail.get('lines') or [] for i in line.get('invoices') or []})
        return {'sales_order': order.sales_order, 'web_order': order.web_order, 'po_number': order.po_number,
                'order_date': order.order_date.isoformat() if order.order_date else '', 'invoices': invoices,
                'tracking': detail.get('tracking') or [], 'imported': timezone.now().isoformat()}

    def create_po(self, order, resolved, notes, user):
        from order.models import PurchaseOrder, PurchaseOrderExtraLine

        currency = order.currency
        if currency and self.money(1, currency) is None:
            notes.append(f'{currency} is not one of InvenTree\'s currencies - prices left blank.')
        po = PurchaseOrder(
            reference=PurchaseOrder.generate_reference(),
            supplier=self.mouser,
            supplier_reference=order.sales_order[:64],
            description=(f'Mouser order {order.sales_order}' + (f' (web order {order.web_order})' if order.web_order else ''))[:250],
            order_currency=currency if self.money(1, currency) is not None else None,
            responsible=self.responsible_owner(user),
            destination=self.destination(),
        )
        po.metadata = {'mouser': self.metadata(order)}
        po.save()
        for line, supplier_part in resolved:
            po.add_line_item(supplier_part, line.quantity, group=False, reference=f'Mouser {line.mouser_pn}'[:100],
                             purchase_price=self.money(line.unit_price, currency))
        self.add_fees(po, order, PurchaseOrderExtraLine)
        if self.setting('PO_STATUS', 'placed') == 'placed':
            po.place_order()
        return po

    def update_po(self, po, order, resolved, notes, user):
        """The cart-to-checkout loop: bring an existing open purchase order in line with what Mouser took."""
        from order.models import PurchaseOrderExtraLine
        from order.status_codes import PurchaseOrderStatus

        currency = order.currency
        ordered = set()
        for line, supplier_part in resolved:
            ordered.add(supplier_part.pk)
            price = self.money(line.unit_price, currency)
            po_line = po.lines.filter(part=supplier_part).first()
            if po_line is None:
                po.add_line_item(supplier_part, line.quantity, group=False,
                                 reference=f'Mouser {line.mouser_pn}'[:100], purchase_price=price)
                notes.append(f'Added {supplier_part.SKU} x {line.quantity} (on the Mouser order, not the PO).')
                continue
            if price is not None:
                po_line.purchase_price = price
            if po_line.quantity != line.quantity:
                if po_line.received:
                    notes.append(f'{supplier_part.SKU}: Mouser has {line.quantity}, the PO {po_line.quantity:g} '
                                 '(some received already - not changed).')
                else:
                    notes.append(f'{supplier_part.SKU}: quantity {po_line.quantity:g} -> {line.quantity} (as ordered on Mouser).')
                    po_line.quantity = line.quantity
            po_line.save()
        missing = [pl.part.SKU for pl in po.lines.select_related('part') if pl.part_id and pl.part_id not in ordered]
        if missing:
            notes.append('On the PO but not the Mouser order: ' + ', '.join(missing) + '.')
        po.supplier_reference = order.sales_order[:64]
        meta = dict(po.metadata or {})
        meta['mouser'] = {**(meta.get('mouser') or {}), **self.metadata(order)}
        po.metadata = meta
        po.save()
        if not po.extra_lines.filter(reference='Mouser fees').exists():
            self.add_fees(po, order, PurchaseOrderExtraLine)
        if po.status == PurchaseOrderStatus.PENDING.value and self.setting('PO_STATUS', 'placed') == 'placed':
            po.place_order()

    def add_fees(self, po, order, extra_line_model):
        """Mouser's additional fees (tariffs, handling) as one extra line, so the PO total matches the invoice."""
        if not self.plugin.get_setting('ADD_FEES'):
            return
        fees = ((order.detail or {}).get('totals') or {}).get('fees')
        try:
            amount = Decimal(str(fees)) if fees not in (None, '') else Decimal(0)
        except Exception:
            amount = Decimal(0)
        if amount > 0:
            extra_line_model.objects.create(order=po, reference='Mouser fees', quantity=1,
                                            price=self.money(amount, order.currency),
                                            description='Additional fees on the Mouser order (tariffs, handling)')

    def destination(self):
        from stock.models import StockLocation
        pk = self.setting('PO_DESTINATION')
        return StockLocation.objects.filter(pk=pk).first() if pk else None

    # ------------------------------------------------------------------ people's decisions
    def decide(self, line, decision, part=None):
        """A person's choice for one line of a held order; then the order is tried again."""
        if line.order.state not in (MouserOrder.STATE_HELD, MouserOrder.STATE_FAILED):
            raise MouserError('This order is already done - change its purchase order in InvenTree instead.')
        if decision not in dict(MouserOrderLine.DECISIONS):
            raise MouserError(f'Unknown choice "{decision}".')
        if decision == MouserOrderLine.DECISION_LINK and part is None:
            raise MouserError('Choose the part to use.')
        line.decision = decision
        if decision == MouserOrderLine.DECISION_LINK:
            line.part, line.supplier_part = part, None
            line.message = f'Chosen: {part.name}.'
        elif decision == MouserOrderLine.DECISION_CREATE:
            line.part, line.supplier_part, line.message = None, None, 'A new part will be created from Mouser.'
        elif decision == MouserOrderLine.DECISION_SKIP:
            line.message = 'Left off the purchase order.'
        else:
            line.message = ''
        line.save()
        return line


RUNNING_MINUTES = 30    # a check started this recently and not finished is still running


def run_scheduled(plugin):
    """
    The hourly tick: one check a day, at or after the "Check at hour" setting
    (server local time). A check that failed (Mouser or the network down) is
    tried again on the next tick, so one bad hour doesn't lose the day; one
    still running isn't started twice.
    """
    from InvenTree.helpers import str2bool

    if not str2bool(plugin.get_setting('ORDER_IMPORT')):
        return None
    importer = OrderImporter(plugin)
    if not importer.order_key or importer.start_date is None:
        return None
    now = timezone.localtime()
    try:
        hour = min(max(int(plugin.get_setting('ORDER_IMPORT_HOUR')), 0), 23)
    except (TypeError, ValueError):
        hour = 6
    if now.hour < hour:
        return None
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = ImportRun.objects.filter(source='schedule', started__gte=day_start)
    if today.filter(ok=True).exists():
        return None
    if today.filter(finished__isnull=True, started__gte=now - timedelta(minutes=RUNNING_MINUTES)).exists():
        return None
    return importer.check(source='schedule')

