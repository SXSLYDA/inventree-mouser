"""
A fake api.mouser.com for tests: install() swaps mouser_lookup.http.request
for one that answers from in-memory orders, search listings and carts, and
records every call. No network.

    fake = FakeMouser()
    fake.add_order(detail_json)          # Mouser's raw OrderDetail shape
    fake.install()
    ...
    fake.uninstall()
"""

import copy
import json
import os
import uuid

from mouser_lookup import http

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


def load_fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


class FakeMouser:
    def __init__(self):
        self.orders = {}       # sales order -> raw OrderDetail
        self.listings = {}     # Mouser PN / MPN (upper) -> raw search Part
        self.prices = {}       # Mouser PN (upper) -> cart unit price
        self.bad_parts = set()  # Mouser PNs the cart refuses
        self.carts = {}        # cart key -> {Mouser PN: quantity}
        self.calls = []
        self._original = None

    # ------------------------------------------------------------------ data
    def add_order(self, detail, status_display=None, po_number=None):
        detail = copy.deepcopy(detail)
        # an order Mouser hasn't processed yet has no sales order number (SalesOrderId '')
        self.orders[str(detail['SalesOrderId'] or f"web-{detail['WebOrderId']}")] = detail
        detail.setdefault('_summary', {})
        if status_display:
            detail['_summary']['OrderStatusDisplay'] = status_display
        if po_number is not None:
            detail['_summary']['PoNumber'] = po_number
        return detail

    def add_listing(self, mouser_pn, mpn, manufacturer, description, price=1.0, attributes=None, category=''):
        part = {
            'MouserPartNumber': mouser_pn, 'ManufacturerPartNumber': mpn, 'Manufacturer': manufacturer,
            'Description': description, 'ProductDetailUrl': f'https://www.mouser.com/ProductDetail/{mouser_pn}',
            'ImagePath': '', 'DataSheetUrl': '', 'AvailabilityInStock': '100', 'Category': category,
            'PriceBreaks': [{'Quantity': 1, 'Price': f'${price}', 'Currency': 'AUD'},
                            {'Quantity': 10, 'Price': f'${round(price * 0.9, 3)}', 'Currency': 'AUD'}],
            'ProductAttributes': [{'AttributeName': k, 'AttributeValue': v} for k, v in (attributes or {}).items()],
        }
        self.listings[mouser_pn.upper()] = part
        self.listings[mpn.upper()] = part
        self.prices[mouser_pn.upper()] = price

    # ------------------------------------------------------------------ patching
    def install(self):
        self._original = http.request
        http.request = self.request
        # modules that imported request by name
        from mouser_lookup import cart, orders
        orders.request = self.request
        cart.request = self.request

    def uninstall(self):
        from mouser_lookup import cart, orders
        http.request = self._original
        orders.request = self._original
        cart.request = self._original

    # ------------------------------------------------------------------ answers
    def request(self, method, path, api_key, *, params=None, json=None):
        self.calls.append((method, path, dict(params or {}), json))
        if not api_key:
            raise http.MouserError('No Mouser API key is set.')
        params = params or {}
        if path == '/search/partnumber':
            term = ((json or {}).get('SearchByPartRequest') or {}).get('mouserPartNumber', '').upper()
            part = self.listings.get(term)
            return {'Errors': [], 'SearchResults': {'NumberOfResult': 1 if part else 0, 'Parts': [part] if part else []}}
        if path == '/orderhistory/ByDateRange':
            from datetime import datetime
            start = datetime.strptime(params['startDate'], '%m/%d/%Y').date()
            end = datetime.strptime(params['endDate'], '%m/%d/%Y').date()
            items = []
            for number, d in self.orders.items():
                day = datetime.fromisoformat(d['OrderDate']).date()
                if start <= day <= end:
                    s = d.get('_summary', {})
                    items.append({'DateCreated': d['OrderDate'], 'SalesOrderNumber': d['SalesOrderId'] or None,
                                  'WebOrderNumber': str(d.get('WebOrderId') or ''),
                                  'PoNumber': s.get('PoNumber', (d.get('PaymentDetail') or {}).get('PoNumber', '')),
                                  'BuyerName': d.get('BuyerName', ''),
                                  'OrderStatusDisplay': s.get('OrderStatusDisplay', d.get('OrderStatusName', ''))})
            return {'Errors': [], 'NumberOfOrders': len(items), 'OrderHistoryItems': items}
        if path == '/orderhistory/salesOrderNumber':
            d = self.orders.get(str(params['salesOrderNumber']))
            if d is None:
                raise http.MouserError('Order not found.')
            return {k: v for k, v in d.items() if k != '_summary'}
        if path == '/orderhistory/webOrderNumber':
            for d in self.orders.values():
                if str(d.get('WebOrderId')) == str(params['webOrderNumber']):
                    return {k: v for k, v in d.items() if k != '_summary'}
            raise http.MouserError('Order not found.')
        if path in ('/cart/items/insert', '/cart'):
            if not params.get('countryCode'):
                raise http.MouserError('Required')  # Mouser wants a country code on cart calls
            key = (json or {}).get('CartKey')
            if path == '/cart':
                if key not in self.carts:
                    raise http.MouserError('Invalid unique identifier.')
                self.carts[key] = {}  # update-cart replaces the contents
            key = key or str(uuid.uuid4())
            cart = self.carts.setdefault(key, {})
            for item in (json or {}).get('CartItems', []):
                pn = item['MouserPartNumber']
                cart[pn] = cart.get(pn, 0) + item['Quantity']
            items = []
            total = 0
            for pn, qty in cart.items():
                item = {'MouserPartNumber': pn, 'Quantity': qty, 'CustomerPartNumber': next(
                    (i.get('CustomerPartNumber', '') for i in json['CartItems'] if i['MouserPartNumber'] == pn), '')}
                if pn.upper() in self.bad_parts:
                    items.append({'MouserPartNumber': pn, 'Quantity': item['Quantity'],
                                  'Errors': [{'Message': 'Part number not found.'}]})
                    continue
                price = self.prices.get(pn.upper(), 1.0)
                total += price * item['Quantity']
                items.append({'MouserPartNumber': pn, 'MfrPartNumber': pn.split('-', 1)[-1],
                              'Quantity': item['Quantity'], 'UnitPrice': price,
                              'ExtendedPrice': round(price * item['Quantity'], 2),
                              'CartItemCustPartNumber': item.get('CustomerPartNumber', ''), 'Errors': []})
            return {'Errors': [], 'CartKey': key, 'CurrencyCode': params.get('currencyCode', 'AUD'),
                    'CartItems': items, 'TotalItemCount': len(items), 'MerchandiseTotal': round(total, 2)}
        raise http.MouserError(f'Fake Mouser has no {path}')
