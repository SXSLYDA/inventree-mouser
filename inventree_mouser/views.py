"""JSON API for the Mouser plugin UI (served under /plugin/mouser/api/)."""

import logging

from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from mouser_lookup import MouserError

from .models import ImportRun, MouserOrder, MouserOrderLine

logger = logging.getLogger('inventree')

PO_URL = '/web/purchasing/purchase-order/{pk}'
PART_URL = '/web/part/{pk}'
VIEW_PERM = 'order.view_purchaseorder'
CHANGE_PERM = 'order.add_purchaseorder'


def get_plugin():
    from plugin.registry import registry

    plugin = registry.get_plugin('mouser')
    if plugin is None:
        raise MouserError('The Mouser plugin is not active.')
    return plugin


class CanView(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.has_perm(VIEW_PERM))


class MouserView(APIView):
    """Errors from Mouser or the import come back as {"error": ...} with a 400."""

    permission_classes = [CanView]

    def handle_exception(self, exc):
        if isinstance(exc, MouserError):
            return Response({'error': str(exc)}, status=400)
        return super().handle_exception(exc)

    def require_change(self, request):
        if not request.user.has_perm(CHANGE_PERM):
            raise MouserError('You need permission to add purchase orders for this.')

    def importer(self):
        from .order_import import OrderImporter

        return OrderImporter(get_plugin())


def part_row(part):
    if part is None:
        return None
    image = ''
    try:
        if part.image:
            image = part.image.url
    except Exception:
        pass
    return {'pk': part.pk, 'name': part.name, 'IPN': part.IPN or '', 'description': part.description or '',
            'active': part.active, 'url': PART_URL.format(pk=part.pk), 'image': image}


def po_row(po):
    if po is None:
        return None
    return {'pk': po.pk, 'reference': po.reference, 'url': PO_URL.format(pk=po.pk),
            'status': po.get_status_display() if hasattr(po, 'get_status_display') else po.status}


def order_row(order, lines=False):
    row = {
        'pk': order.pk, 'sales_order': order.sales_order, 'web_order': order.web_order,
        'po_number': order.po_number, 'order_date': order.order_date.isoformat() if order.order_date else '',
        'mouser_status': order.mouser_status, 'currency': order.currency, 'state': order.state,
        'state_label': order.get_state_display(), 'message': order.message,
        'purchase_order': po_row(order.purchase_order), 'line_count': order.lines.count(),
        'waiting': sum(1 for ln in order.lines.all() if ln.needs_person) if order.state == MouserOrder.STATE_HELD else 0,
        'total': ((order.detail or {}).get('totals') or {}).get('total'),
    }
    detail_lines = [ln for ln in (order.detail or {}).get('lines') or [] if (ln.get('quantity') or 0) > 0]
    if order.purchase_order_id and detail_lines:
        # shipped = Mouser has sent it at least once (back-ordered lines haven't been)
        row['shipped_lines'] = sum(1 for ln in detail_lines if ln.get('shipments') or ln.get('invoices'))
        row['mouser_lines'] = len(detail_lines)
    if lines:
        from part.models import Part

        all_lines = list(order.lines.select_related('part', 'supplier_part'))
        pks = {pk for ln in all_lines for pk in ln.candidates or []}
        candidates = {p.pk: p for p in Part.objects.filter(pk__in=pks)}
        mouser = {ln.get('line_no'): ln for ln in (order.detail or {}).get('lines') or []}
        received = {}
        if order.purchase_order_id:
            for pl in order.purchase_order.lines.all():
                if pl.part_id:
                    have = received.setdefault(pl.part_id, [0, 0])
                    have[0] += float(pl.received or 0)
                    have[1] += float(pl.quantity or 0)

        def shipping(ln):
            m = mouser.get(ln.line_no) or {}
            got = received.get(ln.supplier_part_id) if ln.supplier_part_id else None
            return {
                'mouser_quantity': m.get('quantity', ln.quantity),
                'shipments': m.get('shipments') or [{'invoice': i, 'date': '', 'quantity': None}
                                                    for i in m.get('invoices') or []],
                'shipped_quantity': m.get('shipped_quantity'),
                'received': got[0] if got else None,
                'ordered': got[1] if got else None,
            }
        row['lines'] = [{
            'pk': ln.pk, 'line_no': ln.line_no, 'mouser_pn': ln.mouser_pn, 'mpn': ln.mpn,
            'manufacturer': ln.manufacturer, 'description': ln.description, 'customer_pn': ln.customer_pn,
            'quantity': ln.quantity, 'unit_price': str(ln.unit_price) if ln.unit_price is not None else '',
            'match': ln.match, 'match_label': ln.get_match_display() if ln.match else '',
            'decision': ln.decision, 'needs_person': ln.needs_person, 'message': ln.message,
            'part': part_row(ln.part), 'supplier_part': ln.supplier_part.SKU if ln.supplier_part else '',
            'candidates': [part_row(candidates[pk]) for pk in ln.candidates or [] if pk in candidates],
            **shipping(ln),
        } for ln in all_lines]
        detail = order.detail or {}
        row['tracking'] = detail.get('tracking') or []
        row['invoices'] = sorted({i for ln in detail.get('lines') or [] for i in ln.get('invoices') or []})
        row['totals'] = detail.get('totals') or {}
        row['backorder_shipping_method'] = detail.get('backorder_shipping_method') or ''
    return row


class StatusView(MouserView):
    def get(self, request):
        from InvenTree.helpers import str2bool

        plugin = get_plugin()
        importer = self.importer()
        last = ImportRun.objects.first()
        supplier_pk = plugin.get_setting('SUPPLIER')
        return Response({
            'search_key': bool(plugin.get_setting('SEARCH_API_KEY')),
            'order_key': bool(plugin.get_setting('ORDER_API_KEY')),
            'supplier_set': bool(supplier_pk),
            'daily': str2bool(plugin.get_setting('ORDER_IMPORT')),
            'start_date': importer.start_date.isoformat() if importer.start_date else '',
            'hour': plugin.get_setting('ORDER_IMPORT_HOUR'),
            'po_status': plugin.get_setting('PO_STATUS'),
            'held': MouserOrder.objects.filter(state=MouserOrder.STATE_HELD).count(),
            'failed': MouserOrder.objects.filter(state=MouserOrder.STATE_FAILED).count(),
            'last_run': {'started': last.started.isoformat(), 'finished': last.finished.isoformat() if last.finished else None,
                         'ok': last.ok, 'message': last.message, 'source': last.source} if last else None,
            'can_change': request.user.has_perm(CHANGE_PERM),
        })


class OrdersView(MouserView):
    def get(self, request):
        qs = MouserOrder.objects.select_related('purchase_order').prefetch_related('lines')
        state = request.query_params.get('state')
        if state:
            qs = qs.filter(state__in=state.split(','))
        try:
            limit = min(int(request.query_params.get('limit', 30)), 200)
        except ValueError:
            limit = 30
        # orders needing a person first, then newest
        held = [o for o in qs if o.state in (MouserOrder.STATE_HELD, MouserOrder.STATE_FAILED)]
        rest = [o for o in qs if o.state not in (MouserOrder.STATE_HELD, MouserOrder.STATE_FAILED)][:limit]
        return Response({'orders': [order_row(o) for o in held + rest]})


class CheckNowView(MouserView):
    def post(self, request):
        from InvenTree.tasks import offload_task

        self.require_change(request)
        importer = self.importer()
        if not importer.order_key:
            raise MouserError('Set the Mouser Order API key first (plugin settings).')
        if importer.start_date is None:
            raise MouserError('Set "Import orders placed from" in the plugin settings first.')
        queued = offload_task('plugin.registry.call_plugin_function', 'mouser', 'run_order_check', force_async=True)
        if not queued:
            raise MouserError('Could not start the check - is InvenTree\'s background worker running?')
        return Response({'queued': True})


class ImportNumberView(MouserView):
    def post(self, request):
        self.require_change(request)
        order = self.importer().import_number(str((request.data or {}).get('number') or ''), user=request.user)
        return Response({'order': order_row(order, lines=True)})


class OrderDetailView(MouserView):
    def get(self, request, pk):
        order = MouserOrder.objects.filter(pk=pk).select_related('purchase_order').first()
        if order is None:
            return Response({'error': 'Not found'}, status=404)
        return Response({'order': order_row(order, lines=True)})


class OrderActionView(MouserView):
    """POST retry | ignore | reopen."""

    def post(self, request, pk, action):
        self.require_change(request)
        order = MouserOrder.objects.filter(pk=pk).first()
        if order is None:
            return Response({'error': 'Not found'}, status=404)
        if action == 'retry':
            if order.state not in (MouserOrder.STATE_HELD, MouserOrder.STATE_FAILED):
                raise MouserError('This order is already done.')
            order.state = MouserOrder.STATE_HELD
            order.save()
            order = self.importer().process(order, user=request.user)
        elif action == 'ignore':
            if order.purchase_order_id:
                raise MouserError('This order already has a purchase order.')
            order.state, order.message = MouserOrder.STATE_IGNORED, f'Ignored by {request.user.username}.'
            order.save()
        elif action == 'reopen':
            if order.state != MouserOrder.STATE_IGNORED:
                raise MouserError('Only an ignored order can be reopened.')
            order.state, order.message = MouserOrder.STATE_HELD, ''
            order.save()
            order = self.importer().process(order, user=request.user)
        else:
            return Response({'error': f'Unknown action {action}'}, status=400)
        return Response({'order': order_row(order, lines=True)})


class LineDecisionView(MouserView):
    """POST {decision: link|create|skip|"", part: pk} - then the order is tried again."""

    def post(self, request, pk):
        from part.models import Part

        self.require_change(request)
        line = MouserOrderLine.objects.filter(pk=pk).select_related('order').first()
        if line is None:
            return Response({'error': 'Not found'}, status=404)
        data = request.data or {}
        part = None
        if data.get('part'):
            part = Part.objects.filter(pk=data['part']).first()
            if part is None:
                raise MouserError('That part does not exist.')
            if not part.purchaseable:
                raise MouserError(f'{part.name} is not purchaseable - tick Purchaseable on the part first.')
        importer = self.importer()
        importer.decide(line, str(data.get('decision') or ''), part)
        order = line.order
        if data.get('process', True):
            order = importer.process(order, user=request.user)
        return Response({'order': order_row(order, lines=True)})


class PurchaseOrderView(MouserView):
    """GET - the Mouser order behind a purchase order, and its cart."""

    def get(self, request, pk):
        from order.models import PurchaseOrder

        from .cart import mouser_lines

        from order.status_codes import PurchaseOrderStatus

        po = PurchaseOrder.objects.filter(pk=pk).first()
        if po is None:
            return Response({'error': 'Not found'}, status=404)
        meta = (po.metadata or {}).get('mouser') or {}
        order = MouserOrder.objects.filter(purchase_order=po).first()
        plugin = get_plugin()
        lines, others = (mouser_lines(po, self.importer().mouser) if plugin.get_setting('SUPPLIER') else ([], []))
        site = (plugin.get_setting('MOUSER_SITE') or 'www.mouser.com').strip().strip('/')
        return Response({
            'reference': po.reference,
            'status': po.status,
            'is_pending': po.status == PurchaseOrderStatus.PENDING.value,
            'mouser': meta,
            'order': order_row(order, lines=True) if order else None,
            'mouser_lines': len(lines),
            'other_lines': [ln.part.SKU if ln.part else '(no supplier part)' for ln in others],
            'order_key': bool(plugin.get_setting('ORDER_API_KEY')),
            'cart_url': (site if '://' in site else f'https://{site}') + '/Cart/',
            'can_change': request.user.has_perm('order.change_purchaseorder'),
        })


class CartView(MouserView):
    def post(self, request, pk):
        from order.models import PurchaseOrder

        from .cart import push

        if not request.user.has_perm('order.change_purchaseorder'):
            raise MouserError('You need permission to change purchase orders for this.')
        po = PurchaseOrder.objects.filter(pk=pk).first()
        if po is None:
            return Response({'error': 'Not found'}, status=404)
        return Response(push(get_plugin(), po))


class InvoicesView(MouserView):
    """GET - the Mouser invoices on a purchase order, with what's on each and what's been received."""

    def get(self, request, pk):
        from order.models import PurchaseOrder

        from .receiving import invoices, supplier_invoices

        po = PurchaseOrder.objects.filter(pk=pk).first()
        if po is None:
            return Response({'error': 'Not found'}, status=404)
        return Response({'invoices': invoices(po), 'recorded': supplier_invoices(po),
                         'destination': po.destination_id,
                         'can_receive': request.user.has_perm('order.change_purchaseorder')})


class ReceiveView(MouserView):
    """POST {invoice, lines: [{line, quantity}], location} - receive a Mouser box by its invoice."""

    def post(self, request, pk):
        from django.contrib.auth import get_user_model

        from order.models import PurchaseOrder

        from .receiving import invoices, receive

        if not request.user.has_perm('order.change_purchaseorder'):
            raise MouserError('You need permission to change purchase orders to receive stock.')
        po = PurchaseOrder.objects.filter(pk=pk).first()
        if po is None:
            return Response({'error': 'Not found'}, status=404)
        data = request.data or {}
        user = get_user_model().objects.get(pk=request.user.pk)   # the real User, not the request's wrapper
        entry = receive(get_plugin(), po, str(data.get('invoice') or ''), data.get('lines') or [],
                        location=data.get('location'), user=user)
        po.refresh_from_db()
        return Response({'entry': entry, 'invoices': invoices(po)})
