"""End-to-end test against a real InvenTree database, with a fake Mouser account (fake_mouser.py).

Run from InvenTree's backend directory (the plugin installed and activated, DB migrated):

    MOUSER_TESTS=/path/to/tests python manage.py shell -c "exec(open('/path/to/tests/e2e_inventree.py').read())"

Uses the real order 282378302 (tests/fixtures) and exits non-zero on the first failed check.
"""

import copy
import importlib.util
import os
import sys
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from company.models import Company, ManufacturerPart, SupplierPart
from order.models import PurchaseOrder
from order.status_codes import PurchaseOrderStatus
from part.models import Part, PartCategory
from plugin.registry import registry

from inventree_mouser.models import ImportRun, MouserOrder
from inventree_mouser.order_import import OrderImporter, run_scheduled

_tests = os.environ.get('MOUSER_TESTS', 'tests')
_spec = importlib.util.spec_from_file_location('fake_mouser', os.path.join(_tests, 'fake_mouser.py'))
fake_mouser = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fake_mouser)

CHECKS = []


def check(label, condition, detail=''):
    CHECKS.append(label)
    print(f'[{"ok " if condition else "FAIL"}] {label}' + (f' — {detail}' if detail and not condition else ''))
    if not condition:
        raise SystemExit(1)


def section(title):
    print(f'\n=== {title} ===')


# ---------------------------------------------------------------------- setup
section('Setup')
plugin = registry.get_plugin('mouser')
check('plugin is active', plugin is not None)

from common.settings import set_global_setting  # noqa: E402

set_global_setting('CURRENCY_CODES', 'AUD,USD')
set_global_setting('INVENTREE_DEFAULT_CURRENCY', 'AUD')

User = get_user_model()
admin, _ = User.objects.get_or_create(username='mouser-admin', defaults={'is_staff': True, 'is_superuser': True})
admin.is_staff = admin.is_superuser = True
admin.set_password('pw')
admin.save()

mouser, _ = Company.objects.get_or_create(name='Mouser Electronics', defaults={'is_supplier': True})
te, _ = Company.objects.get_or_create(name='TE Connectivity / DEUTSCH', defaults={'is_manufacturer': True})
connectors, _ = PartCategory.objects.get_or_create(name='Connectors', parent=None)

plugin.set_setting('SUPPLIER', mouser.pk)
plugin.set_setting('ORDER_API_KEY', 'fake-order-key')
plugin.set_setting('SEARCH_API_KEY', 'fake-search-key')
plugin.set_setting('ORDER_IMPORT_START', '01-10-2026')
plugin.set_setting('ORDER_IMPORT', True)
# the fixture orders are from October 2026 - look back far enough whatever day the test runs
plugin.set_setting('ORDER_LOOKBACK_DAYS', 365)
plugin.set_setting('COUNTRY_CODE', 'AU')
plugin.set_setting('CURRENCY_CODE', 'AUD')

# What InvenTree already has:
#   DT04-2P-E005  - with its Mouser supplier part        -> "supplier_part"
#   DT04-6P-E005  - the part only, no supplier parts       -> "part" (Mouser supplier part added)
#   DT04-12PA     - close to DT04-12PA-E005 (no -E005)     -> "possible" -> order held
#   RKEF135 / RKEF110 - nothing                            -> "new"
p2 = Part.objects.create(name='DT04-2P-E005', description='DT 2 way receptacle', category=connectors, purchaseable=True)
sp2 = SupplierPart.objects.create(part=p2, supplier=mouser, SKU='571-DT04-2P-E005')
p6 = Part.objects.create(name='DT04-6P-E005', description='DT 6 way receptacle', category=connectors, purchaseable=True)
p12 = Part.objects.create(name='DT04-12PA', description='DT 12 way receptacle A key', category=connectors, purchaseable=True)
parts_before = Part.objects.count()

fake = fake_mouser.FakeMouser()
order_raw = fake_mouser.load_fixture('order_282378302.json')
fake.add_order(order_raw)
fake.add_listing('650-RKEF135', 'RKEF135', 'Bourns', 'Resettable Fuses - PPTC 1.35A', price=0.70,
                 attributes={'Hold Current': '1.35 A'})
fake.add_listing('650-RKEF110', 'RKEF110', 'Bourns', 'Resettable Fuses - PPTC 1.1A', price=0.80)
fake.add_listing('571-DT04-12PA-E005', 'DT04-12PA-E005', 'TE Connectivity / DEUTSCH', 'DT RECP ASM', price=5.10)
fake.install()

importer = OrderImporter(plugin)

# A stand-in for another plugin's mouser_part_created hook (OMG Harness Import's):
# records which parts it was told about, and returns a note for the order's message.
from inventree_mouser import hooks  # noqa: E402

hook_calls = []


class _Provider:
    slug = 'fake-provider'


def _part_created(part, data, source):
    hook_calls.append((part.name, source, data.get('mpn')))
    return [f'hook saw {part.name}']


hooks.providers = lambda name: [(_Provider(), _part_created)] if name == 'mouser_part_created' else []

try:
    # ------------------------------------------------------------------ matching + hybrid hold
    section('Daily check: one line is only a close match, so the order is held')
    run = importer.check(source='manual')
    check('check ran ok', run.ok, run.message)
    order = MouserOrder.objects.get(sales_order='282378302')
    check('order stored', order.web_order == '40699983' and order.currency == 'AUD', order.__dict__)
    check('order held', order.state == MouserOrder.STATE_HELD, order.message)
    check('nothing created while held', PurchaseOrder.objects.count() == 0 and Part.objects.count() == parts_before)
    lines = {ln.mpn: ln for ln in order.lines.all()}
    check('5 lines', len(lines) == 5, list(lines))
    check('DT04-2P-E005 -> existing Mouser supplier part',
          lines['DT04-2P-E005'].match == 'supplier_part' and lines['DT04-2P-E005'].supplier_part_id == sp2.pk)
    check('DT04-6P-E005 -> existing part', lines['DT04-6P-E005'].match == 'part' and lines['DT04-6P-E005'].part_id == p6.pk)
    check('DT04-12PA-E005 -> possible match DT04-12PA',
          lines['DT04-12PA-E005'].match == 'possible' and p12.pk in lines['DT04-12PA-E005'].candidates,
          lines['DT04-12PA-E005'].message)
    check('RKEF135 / RKEF110 -> new', lines['RKEF135'].match == 'new' and lines['RKEF110'].match == 'new')
    check('prices from Mouser', lines['DT04-12PA-E005'].unit_price == Decimal('4.94'))

    section('Second check: still held, no duplicate rows')
    run = importer.check(source='manual')
    check('second check ok', run.ok, run.message)
    check('one MouserOrder', MouserOrder.objects.count() == 1)
    check('still held', MouserOrder.objects.get(pk=order.pk).state == MouserOrder.STATE_HELD)

    section('Notifications (the bell)')
    from common.models import NotificationMessage
    held_notes = NotificationMessage.objects.filter(user=admin, category='mouser.order_held')
    check('held order -> notification', held_notes.count() == 1 and '282378302' in held_notes.first().name,
          list(held_notes.values_list('name', flat=True)))
    check('...only once a day, however often it is checked', held_notes.count() == 1)

    def api_note(category):
        # what InvenTree's notification drawer gets: it links to target.link unless it
        # knows target.model_type (then that object's page - this used to be "plugin 38")
        api = Client()
        api.login(username='mouser-admin', password='pw')
        r = api.get('/api/notifications/', {'limit': 100})
        rows = r.json()['results'] if isinstance(r.json(), dict) else r.json()
        return next((n for n in rows if n.get('category') == category), None)

    note = api_note('mouser.order_held')
    check('held notification is about the Mouser order and links to Purchasing - Mouser Orders',
          note and note['target']['model_type'] == 'mouserorder'
          and note['target']['link'] == '/purchasing/index/mouser-orders', note)

    # ------------------------------------------------------------------ the person decides via the API
    section('Review on the dashboard item (API)')
    client = Client()
    check('login', client.login(username='mouser-admin', password='pw'))
    r = client.get('/plugin/mouser/api/status/')
    check('status', r.status_code == 200 and r.json()['held'] == 1 and r.json()['order_key'], r.content[:300])
    r = client.get('/plugin/mouser/api/orders/')
    check('orders list, held first', r.status_code == 200 and r.json()['orders'][0]['state'] == 'held')
    r = client.get(f'/plugin/mouser/api/orders/{order.pk}/')
    detail = r.json()['order']
    line12 = next(ln for ln in detail['lines'] if ln['mpn'] == 'DT04-12PA-E005')
    check('line shows candidate with name', line12['needs_person'] and line12['candidates'][0]['name'] == 'DT04-12PA')
    r = client.post(f'/plugin/mouser/api/lines/{line12["pk"]}/', {'decision': 'create'}, content_type='application/json')
    check('decide "create new part" -> order goes through', r.status_code == 200, r.content[:300])
    order.refresh_from_db()
    check('order imported', order.state == MouserOrder.STATE_IMPORTED, order.message)

    section('The purchase order')
    po = order.purchase_order
    check('PO exists', po is not None)
    check('PO supplier Mouser, reference = sales order', po.supplier_id == mouser.pk and po.supplier_reference == '282378302')
    check('PO placed', po.status == PurchaseOrderStatus.PLACED.value, po.status)
    check('PO currency AUD', po.order_currency == 'AUD')
    check('PO 5 lines', po.lines.count() == 5)
    po_line = po.lines.get(part__SKU='571-DT04-12PA-E005')
    check('line price is Mouser\'s', po_line.purchase_price.amount == Decimal('4.94')
          and str(po_line.purchase_price.currency) == 'AUD', po_line.purchase_price)
    check('line qty 75', po_line.quantity == 75)
    check('nothing received', all(ln.received == 0 for ln in po.lines.all()))
    meta = po.metadata['mouser']
    check('PO marked as billed per Mouser invoice (none yet)', po.metadata.get('supplier_invoices') == [])
    check('PO metadata: invoice + tracking', meta['invoices'] == ['92944398'] and meta['tracking'][0]['number'].startswith('1Z'))
    check('PO total = Mouser merchandise total', abs(po.total_price.amount - Decimal('872.86')) < Decimal('0.01'),
          po.total_price)

    section('Parts')
    cat = PartCategory.objects.get(name='Mouser Imports')
    new = Part.objects.filter(category=cat).order_by('name')
    check('3 new parts in Mouser Imports', sorted(p.name for p in new) == ['DT04-12PA-E005', 'RKEF110', 'RKEF135'],
          [p.name for p in new])
    rkef = Part.objects.get(name='RKEF135')
    check('new part from Mouser search (description)', 'PPTC 1.35A' in rkef.description, rkef.description)
    check('new part has Bourns manufacturer part', ManufacturerPart.objects.filter(part=rkef, MPN='RKEF135',
                                                                                     manufacturer__name='Bourns').exists())
    sp = SupplierPart.objects.get(part=rkef, supplier=mouser)
    check('new part has Mouser supplier part + price breaks', sp.SKU == '650-RKEF135' and sp.pricebreaks.count() == 2)
    check('DT04-6P-E005 got a Mouser supplier part', SupplierPart.objects.filter(part=p6, supplier=mouser,
                                                                                SKU='571-DT04-6P-E005').exists())
    check('DT04-12PA untouched', not SupplierPart.objects.filter(part=p12).exists())
    check('category setting remembered', str(plugin.get_setting('NEW_PART_CATEGORY')) == str(cat.pk))
    check('part-created hook told about each new part once, after the PO',
          sorted(hook_calls) == [('DT04-12PA-E005', 'order', 'DT04-12PA-E005'), ('RKEF110', 'order', 'RKEF110'),
                                 ('RKEF135', 'order', 'RKEF135')], hook_calls)
    order.refresh_from_db()
    check("hook notes on the order's message", 'fake-provider: hook saw RKEF135' in order.message, order.message)

    imported_notes = NotificationMessage.objects.filter(user=admin, category='mouser.order_imported')
    check('imported order -> notification linking the PO', imported_notes.count() == 1
          and imported_notes.first().link == f'/purchasing/purchase-order/{po.pk}',
          list(imported_notes.values_list('name', 'link')))
    plugin.set_setting('ORDER_API_KEY', '')
    bad = importer.check(source='manual')
    failed_notes = NotificationMessage.objects.filter(user=admin, category='mouser.check_failed')
    check('failed check -> notification with the reason', not bad.ok and failed_notes.count() == 1
          and 'Order API key' in failed_notes.first().message, list(failed_notes.values_list('message', flat=True)))
    importer.check(source='manual')
    check('...once a day, not every retry', failed_notes.count() == 1)
    note = api_note('mouser.check_failed')
    check('failed-check notification links to Purchasing - Mouser Orders',
          note and note['target']['model_type'] == 'importrun'
          and note['target']['link'] == '/purchasing/index/mouser-orders', note)
    note = api_note('mouser.order_imported')
    check('imported notification is about the purchase order', note and note['target']['model_type'] == 'purchaseorder'
          and note['target']['model_id'] == po.pk, note)
    plugin.set_setting('ORDER_API_KEY', 'fake-order-key')

    section('Run again: nothing doubles up')
    run = importer.check(source='manual')
    check('third check ok', run.ok, run.message)
    check('still one PO', PurchaseOrder.objects.count() == 1)
    check('counted as done', run.counts.get('done') == 1, run.counts)

    # ------------------------------------------------------------------ all lines clear -> straight through
    section('An order where every line is known goes straight through')
    o2 = copy.deepcopy(order_raw)
    o2['SalesOrderId'], o2['WebOrderId'], o2['OrderDate'] = '300000002', 40700002, '2026-10-15T09:00:00'
    o2['PaymentDetail']['PoNumber'] = '40700002'
    o2['OrderLines'] = [ln for ln in o2['OrderLines'] if ln['ProductInfo']['ManufacturerPartNumber'] in ('RKEF110', 'DT04-2P-E005')]
    o2['SummaryDetail']['AdditionalFeesTotal'] = '$12.50'
    fake.add_order(o2)
    run = importer.check(source='manual')
    o2_row = MouserOrder.objects.get(sales_order='300000002')
    check('imported without review', o2_row.state == MouserOrder.STATE_IMPORTED, o2_row.message)
    check('fees as an extra line', o2_row.purchase_order.extra_lines.filter(reference='Mouser fees',
                                                                            price=Decimal('12.50')).exists())
    check('no new parts', Part.objects.filter(category=cat).count() == 3)

    # ------------------------------------------------------------------ closed loop: cart -> checkout PO number
    section('Send a purchase order to a Mouser cart')
    po_loop = PurchaseOrder.objects.create(reference=PurchaseOrder.generate_reference(), supplier=mouser,
                                           description='Restock')
    po_loop.add_line_item(sp2, 50, group=False)
    po_loop.add_line_item(SupplierPart.objects.get(part=rkef, supplier=mouser), 20, group=False)
    r = client.post(f'/plugin/mouser/api/po/{po_loop.pk}/cart/', {}, content_type='application/json')
    check('cart sent', r.status_code == 200, r.content[:300])
    cart = r.json()
    check('cart has both lines', len(cart['lines']) == 2 and cart['cart_key'])
    sent = next(c for c in fake.calls if c[1] == '/cart/items/insert')
    check('customer part numbers sent', {i['CustomerPartNumber'] for i in sent[3]['CartItems']} == {'DT04-2P-E005', 'RKEF135'})
    check('country/currency sent', sent[2].get('countryCode') == 'AU' and sent[2].get('currencyCode') == 'AUD')
    po_loop.refresh_from_db()
    check('blank prices copied from cart', cart['prices_copied'] == 2
          and po_loop.lines.get(part=sp2).purchase_price is not None)
    check('cart key stored on PO', po_loop.metadata['mouser']['cart_key'] == cart['cart_key'])
    check('first send makes a new cart', cart['reused'] is False and len(fake.carts) == 1)
    po_loop.lines.filter(part=sp2).update(quantity=60)
    r = client.post(f'/plugin/mouser/api/po/{po_loop.pk}/cart/', {}, content_type='application/json')
    again = r.json()
    check('sending again updates the same cart', r.status_code == 200 and again['reused'] is True
          and again['cart_key'] == cart['cart_key'] and len(fake.carts) == 1, r.content[:300])
    check('cart holds the new quantities, not doubled', fake.carts[cart['cart_key']] == {'571-DT04-2P-E005': 60,
                                                                                       '650-RKEF135': 20},
          fake.carts[cart['cart_key']])
    del fake.carts[cart['cart_key']]  # checked out / deleted on Mouser
    r = client.post(f'/plugin/mouser/api/po/{po_loop.pk}/cart/', {}, content_type='application/json')
    check('cart gone on Mouser -> a new one', r.status_code == 200 and r.json()['reused'] is False
          and r.json()['cart_key'] != cart['cart_key'], r.content[:300])
    plugin.set_setting('COUNTRY_CODE', '')
    plugin.set_setting('CURRENCY_CODE', '')
    r = client.post(f'/plugin/mouser/api/po/{po_loop.pk}/cart/', {}, content_type='application/json')
    sent = [c for c in fake.calls if c[1] in ('/cart', '/cart/items/insert')][-1]
    check('no country set -> AU from InvenTree\'s AUD', r.status_code == 200 and sent[2].get('countryCode') == 'AU',
          (r.content[:200], sent[2]))
    plugin.set_setting('COUNTRY_CODE', 'AU')
    plugin.set_setting('CURRENCY_CODE', 'AUD')
    po_loop.lines.filter(part=sp2).update(quantity=50)
    r = client.get(f'/plugin/mouser/api/po/{po_loop.pk}/')
    check('PO panel info', r.status_code == 200 and r.json()['mouser_lines'] == 2, r.content[:300])

    section('...checked out on Mouser with the PO reference as the PO number')
    o3 = copy.deepcopy(order_raw)
    o3['SalesOrderId'], o3['WebOrderId'], o3['OrderDate'] = '300000003', 40700003, '2026-10-20T09:00:00'
    o3['PaymentDetail']['PoNumber'] = po_loop.reference
    keep = []
    for ln in o3['OrderLines']:
        mpn = ln['ProductInfo']['ManufacturerPartNumber']
        if mpn == 'DT04-2P-E005':
            ln['Quantity'], ln['UnitPrice'] = 100, 1.55          # changed quantity at checkout
            ln['ProductInfo']['CustomerPartNumber'] = 'DT04-2P-E005'
            keep.append(ln)
        if mpn == 'RKEF110':                                   # added at checkout, not on the PO
            ln['Quantity'] = 5
            keep.append(ln)
    o3['OrderLines'] = keep                                     # RKEF135 dropped at checkout
    fake.add_order(o3)
    run = importer.check(source='manual')
    o3_row = MouserOrder.objects.get(sales_order='300000003')
    check('linked to the existing PO', o3_row.state == MouserOrder.STATE_LINKED and o3_row.purchase_order_id == po_loop.pk,
          o3_row.message)
    po_loop.refresh_from_db()
    check('no extra PO made', PurchaseOrder.objects.count() == 3)
    line = po_loop.lines.get(part=sp2)
    check('quantity updated to what Mouser took', line.quantity == 100)
    check('price updated to Mouser\'s', line.purchase_price.amount == Decimal('1.55'))
    check('missing line added', po_loop.lines.filter(part__SKU='650-RKEF110', quantity=5).exists())
    check('dropped line reported', '650-RKEF135' in o3_row.message, o3_row.message)
    check('PO placed + supplier reference', po_loop.status == PurchaseOrderStatus.PLACED.value
          and po_loop.supplier_reference == '300000003')

    # ------------------------------------------------------------------ already entered by hand
    section('An order already entered by hand (supplier reference) is left alone')
    po_hand = PurchaseOrder.objects.create(reference=PurchaseOrder.generate_reference(), supplier=mouser,
                                           supplier_reference='300000004', description='Typed in by hand')
    o4 = copy.deepcopy(order_raw)
    o4['SalesOrderId'], o4['WebOrderId'], o4['OrderDate'] = '300000004', 40700004, '2026-10-21T09:00:00'
    fake.add_order(o4)
    importer.check(source='manual')
    o4_row = MouserOrder.objects.get(sales_order='300000004')
    check('linked, not imported', o4_row.state == MouserOrder.STATE_LINKED and o4_row.purchase_order_id == po_hand.pk)
    check('hand PO untouched', po_hand.lines.count() == 0 and PurchaseOrder.objects.count() == 4)

    # ------------------------------------------------------------------ cancelled / before start / by number
    section('Cancelled, before the start date, by number')
    o5 = copy.deepcopy(order_raw)
    o5['SalesOrderId'], o5['WebOrderId'], o5['OrderDate'] = '300000005', 40700005, '2026-10-21T10:00:00'
    fake.add_order(o5, status_display='Cancelled')
    o6 = copy.deepcopy(order_raw)
    o6['SalesOrderId'], o6['WebOrderId'], o6['OrderDate'] = '300000006', 40700006, '2026-09-25T10:00:00'
    fake.add_order(o6)
    run = importer.check(source='manual')
    check('cancelled -> ignored', MouserOrder.objects.get(sales_order='300000005').state == MouserOrder.STATE_IGNORED)
    asked = [c for c in fake.calls if c[1] == '/orderhistory/ByDateRange'][-1][2]
    check('window starts at the start date, not 365 days back', asked['startDate'] == '10/01/2026', asked)
    check('not stored', not MouserOrder.objects.filter(sales_order='300000006').exists())
    r = client.post('/plugin/mouser/api/orders/import/', {'number': '40700006'}, content_type='application/json')
    check('import by web order number ignores the start date', r.status_code == 200
          and r.json()['order']['sales_order'] == '300000006' and r.json()['order']['state'] == 'imported', r.content[:300])
    r = client.post('/plugin/mouser/api/orders/import/', {'number': '999'}, content_type='application/json')
    check('unknown number -> clear error', r.status_code == 400 and 'error' in r.json(), r.content[:200])

    section('An order Mouser has not processed yet (web order number, no sales order number)')
    o12 = copy.deepcopy(order_raw)
    o12['SalesOrderId'], o12['WebOrderId'], o12['OrderDate'] = '', 40700012, '2026-10-22T09:30:00'
    o12['OrderLines'] = copy.deepcopy(order_raw['OrderLines'][:1])
    pending = fake.add_order(o12)
    run = importer.check(source='manual')
    check('check is ok - not a failure', run.ok and not run.counts.get('failed'), run.message)
    check('counted as waiting for Mouser', run.counts.get('waiting') == 1 and 'not processed by Mouser yet' in run.message,
          run.message)
    check('nothing stored for it', not MouserOrder.objects.filter(web_order='40700012').exists())
    check('Mouser not asked for a blank sales order', not any(
        c[1] == '/orderhistory/salesOrderNumber' and not c[2].get('salesOrderNumber') for c in fake.calls))
    r = client.post('/plugin/mouser/api/orders/import/', {'number': '40700012'}, content_type='application/json')
    check('import by its web number -> says Mouser has not processed it', r.status_code == 400
          and "hasn't processed web order 40700012" in r.json()['error'], r.content[:300])
    # Mouser processes it: it gets a sales order number
    del fake.orders['web-40700012']
    pending['SalesOrderId'] = '300000012'
    fake.orders['300000012'] = pending
    run = importer.check(source='manual')
    o12_row = MouserOrder.objects.filter(sales_order='300000012').first()
    check('imported once it has a sales order number', o12_row is not None and o12_row.state == 'imported'
          and o12_row.web_order == '40700012', (run.message, o12_row and o12_row.message))
    # stored under its web number by an older version ("W40700013") - moved to the real number, not imported twice
    po_w = PurchaseOrder.objects.create(reference=PurchaseOrder.generate_reference(), supplier=mouser,
                                        supplier_reference='W40700013')
    MouserOrder.objects.create(sales_order='W40700013', web_order='40700013', state=MouserOrder.STATE_IMPORTED,
                               purchase_order=po_w)
    o13 = copy.deepcopy(order_raw)
    o13['SalesOrderId'], o13['WebOrderId'], o13['OrderDate'] = '300000013', 40700013, '2026-10-22T10:00:00'
    fake.add_order(o13)
    pos_before = PurchaseOrder.objects.count()
    importer.check(source='manual')
    po_w.refresh_from_db()
    check('old web-number row moved to its sales order number, no second PO',
          MouserOrder.objects.filter(sales_order='300000013', web_order='40700013').count() == 1
          and not MouserOrder.objects.filter(sales_order='W40700013').exists()
          and po_w.supplier_reference == '300000013' and PurchaseOrder.objects.count() == pos_before)

    # ------------------------------------------------------------------ ambiguous + skip + ignore
    section('Ambiguous match: pick one, leave one off')
    Part.objects.create(name='RKEF185-A', purchaseable=True, category=connectors)
    p185 = Part.objects.create(name='RKEF185', purchaseable=True, category=connectors)
    p185b = Part.objects.create(name='Fuse 1.85A', IPN='RKEF185', purchaseable=True, category=connectors)
    o7 = copy.deepcopy(order_raw)
    o7['SalesOrderId'], o7['WebOrderId'], o7['OrderDate'] = '300000007', 40700007, '2026-10-21T11:00:00'
    o7['OrderLines'] = copy.deepcopy(order_raw['OrderLines'][:2])
    o7['OrderLines'][0]['ProductInfo'].update({'MouserPartNumber': '650-RKEF185', 'ManufacturerPartNumber': 'RKEF185'})
    o7['OrderLines'][1]['ProductInfo'].update({'MouserPartNumber': '650-NOPE1', 'ManufacturerPartNumber': 'NOPE1'})
    fake.add_order(o7)
    importer.check(source='manual')
    o7_row = MouserOrder.objects.get(sales_order='300000007')
    amb = o7_row.lines.get(mpn='RKEF185')
    check('two exact matches -> ambiguous, held', amb.match == 'ambiguous' and o7_row.state == 'held'
          and set(amb.candidates) >= {p185.pk, p185b.pk}, (amb.match, amb.candidates))
    r = client.post(f'/plugin/mouser/api/lines/{amb.pk}/', {'decision': 'link', 'part': p185.pk},
                    content_type='application/json')
    o7_row.refresh_from_db()
    check('one line chosen, other line new -> imported', o7_row.state == 'imported', o7_row.message)
    check('chosen part got the Mouser supplier part', SupplierPart.objects.filter(part=p185, SKU='650-RKEF185').exists())

    o8 = copy.deepcopy(o7)
    o8['SalesOrderId'], o8['WebOrderId'] = '300000008', 40700008
    o8['OrderLines'][0]['ProductInfo'].update({'MouserPartNumber': '650-RKEF185X', 'ManufacturerPartNumber': 'RKEF185'})
    fake.add_order(o8)
    importer.check(source='manual')
    o8_row = MouserOrder.objects.get(sales_order='300000008')
    amb8 = o8_row.lines.get(mpn='RKEF185')
    client.post(f'/plugin/mouser/api/lines/{amb8.pk}/', {'decision': 'skip'}, content_type='application/json')
    o8_row.refresh_from_db()
    check('leave off -> imported with 1 line', o8_row.state == 'imported' and o8_row.purchase_order.lines.count() == 1,
          o8_row.message)

    o9 = copy.deepcopy(o8)
    o9['SalesOrderId'], o9['WebOrderId'] = '300000009', 40700009
    fake.add_order(o9)
    importer.check(source='manual')
    o9_row = MouserOrder.objects.get(sales_order='300000009')
    check('held again (new order)', o9_row.state == 'held')
    r = client.post(f'/plugin/mouser/api/orders/{o9_row.pk}/ignore/', {}, content_type='application/json')
    check('ignore a held order', r.status_code == 200 and r.json()['order']['state'] == 'ignored')
    r = client.post(f'/plugin/mouser/api/lines/{o9_row.lines.first().pk}/', {'decision': 'create'},
                    content_type='application/json')
    check('cannot decide on an ignored order', r.status_code == 400)

    section('The only exact match is inactive -> held, not auto-linked')
    inactive = Part.objects.create(name='OLDPART-1', purchaseable=True, active=False, category=connectors)
    o11 = copy.deepcopy(order_raw)
    o11['SalesOrderId'], o11['WebOrderId'], o11['OrderDate'] = '300000011', 40700011, '2026-10-22T07:00:00'
    o11['OrderLines'] = copy.deepcopy(order_raw['OrderLines'][:1])
    o11['OrderLines'][0]['ProductInfo'].update({'MouserPartNumber': '650-OLDPART-1', 'ManufacturerPartNumber': 'OLDPART-1'})
    fake.add_order(o11)
    importer.check(source='manual')
    o11_row = MouserOrder.objects.get(sales_order='300000011')
    l11 = o11_row.lines.get()
    check('inactive match held as possible', o11_row.state == 'held' and l11.match == 'possible'
          and l11.candidates == [inactive.pk] and 'inactive' in l11.message, (l11.match, l11.message))

    # ------------------------------------------------------------------ status / invoice / tracking kept up to date
    section('An imported order keeps its Mouser status, invoice and tracking up to date')
    o14 = copy.deepcopy(order_raw)
    o14['SalesOrderId'], o14['WebOrderId'], o14['OrderDate'] = '300000014', 40700014, '2026-10-22T11:00:00'
    o14['OrderLines'] = copy.deepcopy(order_raw['OrderLines'][1:2])     # RKEF110 - already in InvenTree
    o14['OrderStatusName'] = 'Open'
    o14['DeliveryDetail'] = {'ShippingMethodName': 'UPS', 'TrackingDetails': []}
    for ln in o14['OrderLines']:
        ln['Activities'] = []
    live14 = fake.add_order(o14, status_display='Open')
    importer.check(source='manual')
    o14_row = MouserOrder.objects.get(sales_order='300000014')
    po14 = o14_row.purchase_order
    check('imported while open: no invoice, no tracking yet', o14_row.state == 'imported' and po14 is not None
          and po14.metadata['mouser']['invoices'] == [] and po14.metadata['mouser']['status'] == 'Open',
          (o14_row.state, o14_row.message))
    imported_at = po14.metadata['mouser']['imported']
    po14.lines.update(received=0)
    # on Mouser: shipped, invoiced, complete
    live14['OrderStatusName'] = 'Complete'
    live14['_summary']['OrderStatusDisplay'] = 'Complete'
    live14['DeliveryDetail']['TrackingDetails'] = [{'Number': '1ZNEW14', 'Link': 'https://www.ups.com/track?tracknum=1ZNEW14'}]
    for ln in live14['OrderLines']:
        ln['Activities'] = [{'InvoiceNumber': 93000014, 'Date': '2026-10-24T00:00:00'}]
    lines_before = list(po14.lines.values_list('part_id', 'quantity', 'purchase_price'))
    run = importer.check(source='manual')
    o14_row.refresh_from_db()
    po14.refresh_from_db()
    check('check counts it as updated', run.counts.get('updated') == 1 and 'updated from Mouser' in run.message,
          (run.counts, run.message))
    check('Mouser status on the order', o14_row.mouser_status == 'Complete')
    meta14 = po14.metadata['mouser']
    check('PO gets the invoice, tracking and status', meta14['invoices'] == ['93000014']
          and meta14['tracking'][0]['number'] == '1ZNEW14' and meta14['status'] == 'Complete', meta14)
    check('import time kept, update time added', meta14['imported'] == imported_at and meta14.get('updated'))
    check('noted on the order', 'invoice 93000014' in o14_row.message and 'tracking 1ZNEW14' in o14_row.message,
          o14_row.message)
    check('PO lines, prices and status untouched', list(po14.lines.values_list('part_id', 'quantity', 'purchase_price'))
          == lines_before and po14.status == PurchaseOrderStatus.PLACED.value)
    r = client.get(f'/plugin/mouser/api/po/{po14.pk}/')
    check('PO panel shows the invoice', r.status_code == 200 and r.json()['order']['invoices'] == ['93000014'],
          r.content[:300])
    asked = len([c for c in fake.calls if c[1] == '/orderhistory/salesOrderNumber'
                 and c[2].get('salesOrderNumber') == '300000014'])
    run = importer.check(source='manual')
    check('complete and invoiced: Mouser not asked again', asked == len([
        c for c in fake.calls if c[1] == '/orderhistory/salesOrderNumber' and c[2].get('salesOrderNumber') == '300000014'])
          and not run.counts.get('updated'), run.counts)

    section('...also for orders older than the look-back window')
    o15 = copy.deepcopy(o14)
    recent = (timezone.localdate() - timedelta(days=20)).isoformat()
    o15['SalesOrderId'], o15['WebOrderId'], o15['OrderDate'] = '300000015', 40700015, f'{recent}T09:00:00'
    live15 = fake.add_order(o15, status_display='Open')
    o15_row = importer.import_number('300000015')
    check('imported while open', o15_row.state == 'imported' and o15_row.mouser_status == 'Open', o15_row.message)
    live15['OrderStatusName'] = 'Cancelled'
    live15['_summary']['OrderStatusDisplay'] = 'Cancelled'
    plugin.set_setting('ORDER_LOOKBACK_DAYS', 1)
    run = importer.check(source='manual')
    plugin.set_setting('ORDER_LOOKBACK_DAYS', 365)
    o15_row.refresh_from_db()
    check('outside the window, still refreshed', o15_row.mouser_status == 'Cancelled' and run.counts.get('updated') == 1,
          (o15_row.mouser_status, run.counts))
    check('cancelled after import: flagged, PO left for a person', 'CANCELLED on Mouser' in o15_row.message
          and o15_row.purchase_order.status == PurchaseOrderStatus.PLACED.value, o15_row.message)

    # ------------------------------------------------------------------ back orders and split shipments
    section('Back orders and split shipments')
    o16 = copy.deepcopy(order_raw)
    o16['SalesOrderId'], o16['WebOrderId'], o16['OrderDate'] = '300000016', 40700016, '2026-10-23T09:00:00'
    o16['OrderLines'] = copy.deepcopy(order_raw['OrderLines'][:2])          # RKEF135 x10, RKEF110 x10
    o16['OrderLines'][1]['Quantity'] = 50
    o16['OrderLines'][0]['Activities'] = [{'InvoiceNumber': 93100001, 'Date': '2026-10-24T00:00:00'}]
    o16['OrderLines'][1]['Activities'] = []                                  # back-ordered
    o16['OrderStatusName'] = 'Open'
    o16['DeliveryDetail'] = {'ShippingMethodName': 'UPS', 'BackOrderShippingMethodName': 'UPS Saver', 'TrackingDetails': []}
    live16 = fake.add_order(o16, status_display='Open')
    importer.check(source='manual')
    o16_row = MouserOrder.objects.get(sales_order='300000016')
    po16 = o16_row.purchase_order
    check('part-shipped order imported with the full quantities', o16_row.state == 'imported'
          and sorted(po16.lines.values_list('quantity', flat=True)) == [10, 50], o16_row.message)
    check('not settled while a line is back-ordered', not importer.settled(o16_row))
    r = client.get(f'/plugin/mouser/api/orders/{o16_row.pk}/')
    d16 = r.json()['order']
    by_pn = {ln['mouser_pn']: ln for ln in d16['lines']}
    check('API: 1 / 2 lines shipped', d16['shipped_lines'] == 1 and d16['mouser_lines'] == 2, d16)
    check('API: shipped line has its shipment, back order has none',
          by_pn['650-RKEF135']['shipments'][0]['invoice'] == '93100001' and by_pn['650-RKEF110']['shipments'] == [],
          by_pn)
    check('API: received so far from the PO', by_pn['650-RKEF110']['received'] == 0
          and by_pn['650-RKEF110']['ordered'] == 50, by_pn['650-RKEF110'])
    check('API: back-order shipping method', d16['backorder_shipping_method'] == 'UPS Saver')
    # first part of the back order ships; the first line ships again (split)
    live16['OrderLines'][1]['Activities'] = [{'InvoiceNumber': 93100002, 'Date': '2026-11-02T00:00:00'}]
    live16['OrderLines'][0]['Activities'].append({'InvoiceNumber': 93100003, 'Date': '2026-11-02T00:00:00'})
    run = importer.check(source='manual')
    o16_row.refresh_from_db()
    check('new shipments noted, line by line', run.counts.get('updated') == 1
          and '650-RKEF110 shipped - invoice 93100002, 02-11-2026' in o16_row.message
          and '650-RKEF135 shipped - invoice 93100003' in o16_row.message, o16_row.message)
    check('PO has every invoice', po16.__class__.objects.get(pk=po16.pk).metadata['mouser']['invoices']
          == ['93100001', '93100002', '93100003'])
    check('still open on Mouser -> still refreshed', not importer.settled(o16_row))
    # Mouser cuts the rest of the back order: 50 -> 30, then completes the order
    live16['OrderLines'][1]['Quantity'] = 30
    live16['OrderStatusName'] = 'Complete'
    live16['_summary']['OrderStatusDisplay'] = 'Complete'
    from common.models import NotificationMessage
    run = importer.check(source='manual')
    o16_row.refresh_from_db()
    check('cut quantity flagged', 'Mouser now has 30 of the 50 ordered' in o16_row.message, o16_row.message)
    check('...and notified', NotificationMessage.objects.filter(user=admin, category='mouser.order_changed').exists())
    check('PO line not changed for you', po16.lines.filter(quantity=50).exists())
    check('complete and every line shipped -> settled', importer.settled(o16_row))
    r = client.get(f'/plugin/mouser/api/orders/{o16_row.pk}/')
    rk110 = next(ln for ln in r.json()['order']['lines'] if ln['mouser_pn'] == '650-RKEF110')
    check('API shows Mouser now 30 of 50', rk110['mouser_quantity'] == 30 and rk110['quantity'] == 50, rk110)

    # ------------------------------------------------------------------ receive by Mouser invoice
    section('Receive a box by its Mouser invoice')
    from stock.models import StockLocation
    shelf, _ = StockLocation.objects.get_or_create(name='Mouser shelf')
    o17 = copy.deepcopy(order_raw)
    o17['SalesOrderId'], o17['WebOrderId'], o17['OrderDate'] = '300000017', 40700017, '2026-10-23T10:00:00'
    o17['OrderLines'] = copy.deepcopy(order_raw['OrderLines'][:2])          # RKEF135 x10, RKEF110 x50
    o17['OrderLines'][1]['Quantity'] = 50
    o17['OrderLines'][0]['Activities'] = [{'InvoiceNumber': 93170001, 'Date': '2026-10-24T00:00:00'}]
    o17['OrderLines'][0]['AdditionalFees'] = [{'code': 'TARIFF', 'amount': '0.32', 'extendedAmount': '3.20'}]
    o17['OrderLines'][1]['Activities'] = [{'InvoiceNumber': 93170001, 'Date': '2026-10-24T00:00:00'},
                                          {'InvoiceNumber': 93170002, 'Date': '2026-11-02T00:00:00'}]
    o17['SummaryDetail']['AdditionalFeesTotal'] = '$5.00'
    fake.add_order(o17, status_display='Complete')
    importer.check(source='manual')
    po17 = MouserOrder.objects.get(sales_order='300000017').purchase_order
    check('order imported and placed', po17 is not None and po17.status == PurchaseOrderStatus.PLACED.value)
    r = client.get(f'/plugin/mouser/api/po/{po17.pk}/invoices/')
    invs = r.json()['invoices']
    check('two invoices, oldest first', [i['number'] for i in invs] == ['93170001', '93170002'], invs)
    first = {ln['mouser_pn']: ln for ln in invs[0]['lines']}
    check('line on one invoice: quantity filled in', first['650-RKEF135']['suggested'] == 10, first)
    check('line in two parts: left for you to enter', first['650-RKEF110']['suggested'] is None
          and first['650-RKEF110']['parts'] == 2, first)
    line_a = po17.lines.get(part__SKU='650-RKEF135')
    line_b = po17.lines.get(part__SKU='650-RKEF110')
    stock_before = line_b.part.part.total_stock
    r = client.post(f'/plugin/mouser/api/po/{po17.pk}/receive/',
                    {'invoice': '93170001', 'location': shelf.pk,
                     'lines': [{'line': line_a.pk, 'quantity': 10}, {'line': line_b.pk, 'quantity': 30}]},
                    content_type='application/json')
    check('first box received', r.status_code == 200, r.content[:300])
    line_a.refresh_from_db()
    line_b.refresh_from_db()
    check('stock received through InvenTree', line_a.received == 10 and line_b.received == 30
          and line_b.part.part.total_stock == stock_before + 30, (line_a.received, line_b.received))
    po17.refresh_from_db()
    inv1 = po17.metadata['supplier_invoices'][0]
    check('invoice recorded: number, date, due on the day (paid on order)', inv1['number'] == '93170001'
          and inv1['date'] == '2026-10-24' and inv1['due_date'] == '2026-10-24', inv1)
    check('invoice lines and quantities', {(x['line'], x['quantity']) for x in inv1['lines']}
          == {(line_a.pk, '10'), (line_b.pk, '30')}, inv1['lines'])
    check('fees on the invoice they relate to: line tariff + order-level rest',
          sorted(x['amount'] for x in inv1['extra']) == ['1.80', '3.20'], inv1['extra'])
    r = client.post(f'/plugin/mouser/api/po/{po17.pk}/receive/',
                    {'invoice': '93170002', 'lines': [{'line': line_b.pk, 'quantity': 25}]}, content_type='application/json')
    check('more than is still to come -> refused', r.status_code == 400 and 'only 20' in r.json()['error'], r.content[:200])
    r = client.post(f'/plugin/mouser/api/po/{po17.pk}/receive/',
                    {'invoice': '99999', 'lines': [{'line': line_b.pk, 'quantity': 1}]}, content_type='application/json')
    check('unknown invoice -> refused', r.status_code == 400, r.content[:200])
    r = client.post(f'/plugin/mouser/api/po/{po17.pk}/receive/',
                    {'invoice': '93170002', 'lines': [{'line': line_a.pk, 'quantity': 1}]}, content_type='application/json')
    check('line not on that invoice -> refused', r.status_code == 400, r.content[:200])
    plugin.set_setting('PAYMENT_TERMS', 'account')
    plugin.set_setting('ACCOUNT_TERMS_DAYS', 30)
    r = client.post(f'/plugin/mouser/api/po/{po17.pk}/receive/',
                    {'invoice': '93170002', 'location': shelf.pk, 'lines': [{'line': line_b.pk, 'quantity': 20}]},
                    content_type='application/json')
    plugin.set_setting('PAYMENT_TERMS', 'on_order')
    check('second box received', r.status_code == 200, r.content[:300])
    po17.refresh_from_db()
    inv2 = po17.metadata['supplier_invoices'][1]
    check('on account: due after the terms', inv2['date'] == '2026-11-02' and inv2['due_date'] == '2026-12-02', inv2)
    check('second invoice: only its own line, no fees again', [(x['line'], x['quantity']) for x in inv2['lines']]
          == [(line_b.pk, '20')] and inv2['extra'] == [], inv2)
    r = client.get(f'/plugin/mouser/api/po/{po17.pk}/invoices/')
    check('both invoices show as received', all(i['received'] for i in r.json()['invoices']))

    # ------------------------------------------------------------------ failure is recorded and retried
    section('A failing order is recorded, then retried')
    o10 = copy.deepcopy(order_raw)
    o10['SalesOrderId'], o10['WebOrderId'], o10['OrderDate'] = '300000010', 40700010, '2026-10-22T08:00:00'
    o10['OrderLines'] = copy.deepcopy(order_raw['OrderLines'][:1])
    o10['OrderLines'][0]['ProductInfo'].update({'MouserPartNumber': '650-NEWFAIL', 'ManufacturerPartNumber': 'NEWFAIL'})
    fake.add_order(o10)
    real_import_part = plugin.import_part

    def broken(*a, **k):
        raise RuntimeError('database said no')
    plugin.import_part = broken
    importer.check(source='manual')
    o10_row = MouserOrder.objects.get(sales_order='300000010')
    check('failed with the reason', o10_row.state == 'failed' and 'database said no' in o10_row.message, o10_row.message)
    check('nothing half-made', not Part.objects.filter(name='NEWFAIL').exists() and o10_row.purchase_order is None)
    plugin.import_part = real_import_part
    importer.check(source='manual')
    o10_row.refresh_from_db()
    check('retried on next check', o10_row.state == 'imported', o10_row.message)

    # ------------------------------------------------------------------ schedule
    section('Daily schedule')
    ImportRun.objects.filter(source='schedule').delete()
    plugin.set_setting('ORDER_IMPORT_HOUR', 0)
    first = run_scheduled(plugin)
    check('runs once a day', first is not None and first.source == 'schedule')
    check('not twice the same day', run_scheduled(plugin) is None)
    ImportRun.objects.filter(source='schedule').update(ok=False)
    retry = run_scheduled(plugin)
    check('a failed check is tried again on the next tick', retry is not None and retry.pk != first.pk)
    ImportRun.objects.filter(source='schedule').delete()
    ImportRun.objects.create(source='schedule')        # started, not finished: still running
    check('not started twice while one is running', run_scheduled(plugin) is None)
    ImportRun.objects.filter(source='schedule').delete()
    plugin.set_setting('ORDER_IMPORT', False)
    ImportRun.objects.filter(source='schedule').delete()
    check('off = does nothing', run_scheduled(plugin) is None)
    plugin.set_setting('ORDER_IMPORT', True)
    now = timezone.localtime()
    if now.hour < 23:
        plugin.set_setting('ORDER_IMPORT_HOUR', 23)
        check('waits for the hour', run_scheduled(plugin) is None)
    plugin.set_setting('ORDER_IMPORT_HOUR', 6)
    plugin.set_setting('ORDER_IMPORT_START', '')
    check('no start date = does nothing', run_scheduled(plugin) is None)
    plugin.set_setting('ORDER_IMPORT_START', '01-10-2026')
    from django.core.exceptions import ValidationError
    try:
        plugin.set_setting('ORDER_IMPORT_START', '2026-10-01')
        refused = False
    except ValidationError:
        refused = True
    check('start date must be DD-MM-YYYY', refused and str(plugin.get_setting('ORDER_IMPORT_START')) == '01-10-2026',
          plugin.get_setting('ORDER_IMPORT_START'))
    check('ImportRun older than a day does not block', ImportRun.objects.filter(
        started__lt=timezone.now() - timedelta(days=1)).count() == 0)

    # ------------------------------------------------------------------ wizard
    section('Import from Supplier wizard')
    results = plugin.get_search_results('mouser', 'RKEF135')
    check('search finds the listing, marks existing part', len(results) == 1 and results[0].sku == '650-RKEF135'
          and results[0].existing_part == rkef)
    data = plugin.get_import_data('mouser', '650-RKEF110')
    check('import data by Mouser PN', data['mpn'] == 'RKEF110')
    params = plugin.get_parameters(fake_mouser_data := plugin.get_import_data('mouser', '650-RKEF135'))
    check('Mouser attributes offered as parameters', any(p.name == 'Hold Current' for p in params), params)
    check('pricing', plugin.get_pricing_data(fake_mouser_data)[1] == (0.7, 'AUD'))
    hand = Part.objects.create(name='MANUAL-1', description='entered by hand', purchaseable=True)
    sp_hand = SupplierPart.objects.create(part=hand, supplier=mouser, SKU='999-MANUAL-1')
    listing = {'mpn': 'MANUAL-1', 'spn': '999-MANUAL-1', 'manufacturer': 'Bourns', 'url': 'https://www.mouser.com/x'}
    mp_hand = plugin.import_manufacturer_part(listing, part=hand)
    again = plugin.import_supplier_part(listing, part=hand, manufacturer_part=mp_hand)
    again.refresh_from_db()
    check('supplier part entered by hand is reused, manufacturer part filled in',
          again.pk == sp_hand.pk and again.manufacturer_part_id == mp_hand.pk
          and SupplierPart.objects.filter(part=hand, supplier=mouser).count() == 1)

    # ------------------------------------------------------------------ permissions
    section('Permissions')
    viewer, _ = User.objects.get_or_create(username='mouser-nobody')
    viewer.set_password('pw')
    viewer.save()
    c2 = Client()
    c2.login(username='mouser-nobody', password='pw')
    check('no PO permission -> no access', c2.get('/plugin/mouser/api/orders/').status_code in (401, 403))

    # ------------------------------------------------------------------ UI registration
    section('UI')
    items = plugin.get_ui_dashboard_items(None, {})
    check('dashboard item', items and items[0]['source'].endswith('mouser_ui.js:renderMouserDashboard'))
    check('panel on a Mouser PO', plugin.get_ui_panels(None, {'target_model': 'purchaseorder', 'target_id': po.pk}))
    other = Company.objects.create(name='Someone else', is_supplier=True)
    po_other = PurchaseOrder.objects.create(reference=PurchaseOrder.generate_reference(), supplier=other)
    check('no panel on another supplier\'s PO',
          plugin.get_ui_panels(None, {'target_model': 'purchaseorder', 'target_id': po_other.pk}) == [])
    from types import SimpleNamespace
    purchasing = plugin.get_ui_panels(SimpleNamespace(user=admin), {'target_model': 'purchasing'})
    check('Mouser Orders panel on the Purchasing page',
          purchasing and purchasing[0]['source'].endswith('mouser_ui.js:renderMouserPurchasing'), purchasing)
    check('...not for someone who can\'t see purchase orders',
          plugin.get_ui_panels(SimpleNamespace(user=viewer), {'target_model': 'purchasing'}) == [])
    r = client.get('/plugin/mouser/api/orders/?state=imported,linked&limit=200')
    check('orders filtered by state', r.status_code == 200
          and {o['state'] for o in r.json()['orders']} <= {'imported', 'linked'}, r.content[:200])
finally:
    fake.uninstall()

print(f'\nAll {len(CHECKS)} checks passed.')
sys.stdout.flush()
