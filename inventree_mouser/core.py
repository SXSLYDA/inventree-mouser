"""InvenTree plugin entry point: Mouser part import, order import into purchase orders, and carts from purchase orders."""

import logging

from django.core.validators import MaxValueValidator, MinValueValidator

from plugin import InvenTreePlugin
from plugin.mixins import AppMixin, ScheduleMixin, SettingsMixin, UrlsMixin, UserInterfaceMixin

from . import PLUGIN_VERSION
from .supplier import MouserSupplierMixin

logger = logging.getLogger('inventree')


class MouserIntegrationPlugin(MouserSupplierMixin, AppMixin, ScheduleMixin, SettingsMixin, UrlsMixin,
                              UserInterfaceMixin, InvenTreePlugin):
    """
    Mouser Electronics for InvenTree.

    - Import from Supplier wizard: search Mouser and create parts (supplier.py)
    - Order import: Mouser orders become purchase orders, once a day or on demand,
      with orders that need a person held for review (order_import.py)
    - Send to Mouser cart: a purchase order's Mouser lines into a cart (cart.py)

    Hooks for other plugins (e.g. OMG Harness Import) are in hooks.py.
    """

    NAME = 'MouserIntegration'
    SLUG = 'mouser'
    TITLE = 'Mouser Integration'
    DESCRIPTION = ('Mouser in the Import from Supplier wizard, Mouser orders imported as purchase orders, '
                   'and purchase orders sent to a Mouser cart')
    VERSION = PLUGIN_VERSION
    AUTHOR = 'Tyler Brannigan / OMG Harness'
    WEBSITE = 'https://github.com/SXSLYDA/inventree-mouser'
    LICENSE = 'MIT'
    MIN_VERSION = '1.6.0'

    # Ticks hourly; the import itself runs once a day at the ORDER_IMPORT_HOUR setting
    SCHEDULED_TASKS = {
        'mouser_order_import': {
            'func': 'run_scheduled_import',
            'schedule': 'H',
        },
    }

    SETTINGS = {
        # ---------------------------------------------------------------- Mouser account
        'SEARCH_API_KEY': {
            'name': 'Search API key',
            'description': 'My Mouser > APIs > Search API. Used to search Mouser and create parts',
            'default': '',
            'protected': True,
        },
        'ORDER_API_KEY': {
            'name': 'Order API key',
            'description': 'My Mouser > APIs > Order API (a different key). Used to read your orders and fill carts',
            'default': '',
            'protected': True,
        },
        'MOUSER_SITE': {
            'name': 'Mouser website',
            'description': 'Your Mouser site, for links, e.g. au.mouser.com',
            'default': 'www.mouser.com',
        },
        'COUNTRY_CODE': {
            'name': 'Country code',
            'description': ('Two-letter country Mouser prices carts for, e.g. AU (Mouser needs one). Blank = '
                            'worked out from Currency code, else InvenTree\'s default currency'),
            'default': '',
        },
        'CURRENCY_CODE': {
            'name': 'Currency code',
            'description': 'Currency for carts, e.g. AUD. Blank = the country\'s currency',
            'default': '',
        },
        # ---------------------------------------------------------------- creating parts
        'DOWNLOAD_IMAGES': {
            'name': 'Download part images',
            'description': 'Put Mouser\'s picture on a part created from Mouser (when it has none)',
            'validator': bool,
            'default': False,
        },
        'NEW_PART_CATEGORY': {
            'name': 'New parts category',
            'description': ('Where the order import creates parts that weren\'t in InvenTree, to be moved to their '
                            'own category. Blank = "Mouser Imports" is created the first time'),
            'model': 'part.partcategory',
        },
        # ---------------------------------------------------------------- order import
        'ORDER_IMPORT': {
            'name': 'Import Mouser orders daily',
            'description': 'Check Mouser once a day for new orders and make purchase orders from them',
            'validator': bool,
            'default': False,
        },
        'ORDER_IMPORT_START': {
            'name': 'Import orders placed from',
            'description': ('YYYY-MM-DD. Orders placed before this date are never imported (so orders already '
                            'entered by hand aren\'t doubled up). Required for the daily check'),
            'default': '',
        },
        'ORDER_IMPORT_HOUR': {
            'name': 'Check at hour',
            'description': 'Hour of the day (0-23, server time) the daily check runs at or after',
            'validator': [int, MinValueValidator(0), MaxValueValidator(23)],
            'default': 6,
        },
        'ORDER_LOOKBACK_DAYS': {
            'name': 'Look back (days)',
            'description': 'Each check asks Mouser for orders placed in this many days (never before the start date)',
            'validator': [int, MinValueValidator(1), MaxValueValidator(365)],
            'default': 14,
        },
        'PO_STATUS': {
            'name': 'New purchase orders are',
            'description': ('Placed: ready to receive (and sent on to Xero by the Xero plugin, if it exports placed '
                            'orders). Pending: left for a person to place'),
            'choices': [('placed', 'Placed'), ('pending', 'Pending')],
            'default': 'placed',
        },
        'PO_DESTINATION': {
            'name': 'Receive into',
            'description': 'Default stock location for the purchase orders the import makes (optional)',
            'model': 'stock.stocklocation',
        },
        'PO_RESPONSIBLE': {
            'name': 'Responsible',
            'description': ('User or group name set as responsible on imported purchase orders. Needed if '
                            'InvenTree requires a responsible owner on purchase orders'),
            'default': '',
        },
        'ADD_FEES': {
            'name': 'Add Mouser fees',
            'description': 'Add the order\'s additional fees (tariffs, handling) as an extra line',
            'validator': bool,
            'default': True,
        },
        # ---------------------------------------------------------------- carts
        'CART_UPDATE_PRICES': {
            'name': 'Copy cart prices to the purchase order',
            'description': 'On: every line gets Mouser\'s cart price. Off: only lines with no price',
            'validator': bool,
            'default': False,
        },
    }

    # ------------------------------------------------------------------ schedule
    def run_scheduled_import(self, *args, **kwargs):
        """Hourly tick from the background worker (see order_import.run_scheduled)."""
        from .order_import import run_scheduled

        return run_scheduled(self)

    def run_order_check(self, *args, **kwargs):
        """Background-task target for the dashboard's "Check Mouser now"."""
        from .order_import import OrderImporter

        run = OrderImporter(self).check(source='manual')
        return {'ok': run.ok, 'message': run.message}

    # ------------------------------------------------------------------ URLs
    def setup_urls(self):
        from django.urls import path

        from . import views

        return [
            path('api/status/', views.StatusView.as_view(), name='api-status'),
            path('api/orders/', views.OrdersView.as_view(), name='api-orders'),
            path('api/orders/check/', views.CheckNowView.as_view(), name='api-orders-check'),
            path('api/orders/import/', views.ImportNumberView.as_view(), name='api-orders-import'),
            path('api/orders/<int:pk>/', views.OrderDetailView.as_view(), name='api-order'),
            path('api/orders/<int:pk>/<str:action>/', views.OrderActionView.as_view(), name='api-order-action'),
            path('api/lines/<int:pk>/', views.LineDecisionView.as_view(), name='api-line'),
            path('api/po/<int:pk>/', views.PurchaseOrderView.as_view(), name='api-po'),
            path('api/po/<int:pk>/cart/', views.CartView.as_view(), name='api-po-cart'),
        ]

    # ------------------------------------------------------------------ UI
    def api_context(self):
        return {'api': f'{self.base_url}api/'}

    def get_ui_dashboard_items(self, request, context, **kwargs):
        return [{
            'key': 'mouser-orders',
            'title': 'Mouser Orders',
            'description': 'Mouser orders imported as purchase orders, and the ones waiting for you to choose a part',
            'icon': 'ti:shopping-cart:outline',
            'source': self.plugin_static_file('mouser_ui.js:renderMouserDashboard'),
            'context': self.api_context(),
            'options': {'width': 6, 'height': 8},
        }]

    def get_ui_panels(self, request, context, **kwargs):
        """A "Mouser" panel on purchase orders from the Mouser supplier."""
        if (context or {}).get('target_model') != 'purchaseorder' or not (context or {}).get('target_id'):
            return []
        from order.models import PurchaseOrder

        pk = self.get_setting('SUPPLIER')
        po = PurchaseOrder.objects.filter(pk=context['target_id']).only('supplier_id').first()
        if not pk or po is None or str(po.supplier_id) != str(pk):
            return []
        return [{
            'key': 'mouser',
            'title': 'Mouser',
            'description': 'The Mouser order behind this purchase order, or send it to a Mouser cart',
            'icon': 'ti:shopping-cart:outline',
            'source': self.plugin_static_file('mouser_ui.js:renderMouserPanel'),
            'context': {**self.api_context(), 'pk': context['target_id']},
        }]
