"""Unit tests that need no InvenTree: mouser_lookup's parsing and the part-number matching rules."""

import json
import os
from decimal import Decimal

import pytest

from mouser_lookup import MouserError, cart, http, orders
from mouser_lookup.orders import _decimal, map_order_detail, map_order_summary

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


def test_order_detail_from_real_order():
    d = map_order_detail(fixture('order_282378302.json'))
    assert d['sales_order'] == '282378302'
    assert d['web_order'] == '40699983'
    assert d['currency'] == 'AUD'
    assert d['order_date'] == '2026-10-08'
    assert d['totals']['total'] == Decimal('960.15')
    assert len(d['lines']) == 5
    line = d['lines'][2]
    assert line == {**line, 'mouser_pn': '571-DT04-12PA-E005', 'mpn': 'DT04-12PA-E005', 'quantity': 75,
                    'unit_price': Decimal('4.94'), 'invoices': ['92944398'], 'line_no': 3}
    assert line['ext_price'] == Decimal('370.5')


def test_ext_price_worked_out_when_missing():
    d = map_order_detail({'SalesOrderId': '1', 'OrderLines': [
        {'Quantity': 10, 'UnitPrice': 0.621, 'ProductInfo': {'MouserPartNumber': '650-RKEF135'}}]})
    assert d['lines'][0]['ext_price'] == Decimal('6.210')


def test_summary_row():
    row = map_order_summary({'DateCreated': '2026-10-08T14:21:00', 'SalesOrderNumber': '282378302',
                             'WebOrderNumber': '40699983', 'PoNumber': 'PO-0042', 'OrderStatusDisplay': 'Complete'})
    assert row['order_date'] == '2026-10-08' and row['po_number'] == 'PO-0042'


@pytest.mark.parametrize('text,value', [
    ('$1,234.50', Decimal('1234.50')), ('1.234,50 €', Decimal('1234.50')), ('0,621 €', Decimal('0.621')),
    ('$0.621', Decimal('0.621')), ('1,234', Decimal('1234')), (4.94, Decimal('4.94')), ('', None), ('n/a', None),
])
def test_prices(text, value):
    assert _decimal(text) == value


def test_mouser_errors_raise(monkeypatch):
    class R:
        status_code = 200
        text = ''

        def json(self):
            return {'Errors': [{'Message': 'Invalid unique identifier.'}]}
    monkeypatch.setattr(http.requests, 'request', lambda *a, **k: R())
    with pytest.raises(MouserError, match='Invalid unique identifier'):
        orders.order_detail('key', sales_order='1')


def test_bare_message_error(monkeypatch):
    class R:
        status_code = 200
        text = ''

        def json(self):
            return {'Message': 'An error has occurred.'}
    monkeypatch.setattr(http.requests, 'request', lambda *a, **k: R())
    with pytest.raises(MouserError, match='An error has occurred'):
        orders.order_detail('key', sales_order='1')


def test_search_price_breaks_any_format():
    from mouser_lookup import map_mouser_response
    parts = map_mouser_response({'SearchResults': {'Parts': [{'ManufacturerPartNumber': 'X', 'PriceBreaks': [
        {'Quantity': 1, 'Price': '$1,234.50', 'Currency': 'AUD'},
        {'Quantity': 10, 'Price': '1,45 €', 'Currency': 'EUR'},
        {'Quantity': 100, 'Price': '$0.621', 'Currency': 'AUD'}]}]}})
    assert [pb['price'] for pb in parts[0]['price_breaks']] == [1234.5, 1.45, 0.621]


def test_cart_replace(monkeypatch):
    sent = {}

    def fake(method, path, api_key, params=None, json=None):
        sent.update(path=path, json=json)
        return {'CartKey': json['CartKey'], 'CartItems': []}
    monkeypatch.setattr(cart, 'request', fake)
    result = cart.replace_items('k', 'abc', [{'mouser_pn': '650-RKEF135', 'quantity': 5}], country='AU')
    assert sent['path'] == '/cart' and sent['json']['CartKey'] == 'abc' and result['cart_key'] == 'abc'


def test_no_key():
    from datetime import date
    with pytest.raises(MouserError, match='No Mouser API key'):
        orders.order_history('', date(2026, 10, 1), date(2026, 10, 2))


def test_dates_sent_mm_dd_yyyy(monkeypatch):
    from datetime import date
    seen = {}

    def fake(method, path, api_key, params=None, json=None):
        seen.update(params)
        return {'OrderHistoryItems': [{'SalesOrderNumber': '2', 'DateCreated': '2026-10-02T00:00:00'},
                                      {'SalesOrderNumber': '1', 'DateCreated': '2026-10-01T00:00:00'}, {}]}
    monkeypatch.setattr(orders, 'request', fake)
    rows = orders.order_history('k', date(2026, 10, 1), date(2026, 10, 22))
    assert seen == {'startDate': '10/01/2026', 'endDate': '10/22/2026'}
    assert [r['sales_order'] for r in rows] == ['2', '1']


def test_cart_payload_and_mapping(monkeypatch):
    sent = {}

    def fake(method, path, api_key, params=None, json=None):
        sent.update(path=path, params=params, json=json)
        return {'CartKey': 'k-1', 'CurrencyCode': 'aud', 'MerchandiseTotal': 81.0, 'CartItems': [
            {'MouserPartNumber': '650-RKEF135', 'Quantity': 10, 'UnitPrice': 0.7, 'ExtendedPrice': 7.0,
             'CartItemCustPartNumber': 'RKEF135', 'Errors': []},
            {'MouserPartNumber': '650-X', 'Quantity': 1, 'Errors': [{'Message': 'Part number not found.'}]}]}
    monkeypatch.setattr(cart, 'request', fake)
    result = cart.add_items('k', [{'mouser_pn': '650-RKEF135', 'quantity': 10, 'customer_pn': 'RKEF135'},
                                  {'mouser_pn': '650-X', 'quantity': 1}], country='AU', currency='AUD')
    assert sent['path'] == '/cart/items/insert'
    assert sent['params'] == {'countryCode': 'AU', 'currencyCode': 'AUD'}
    assert sent['json']['CartKey'] is None
    assert sent['json']['CartItems'][0] == {'MouserPartNumber': '650-RKEF135', 'Quantity': 10, 'CustomerPartNumber': 'RKEF135'}
    assert 'CustomerPartNumber' not in sent['json']['CartItems'][1]
    assert result['cart_key'] == 'k-1' and result['currency'] == 'AUD'
    assert result['lines'][0]['unit_price'] == Decimal('0.7')
    assert result['lines'][1]['errors'] == ['Part number not found.']


# ---------------------------------------------------------------------- matching rules (pure parts)

matching = pytest.importorskip('inventree_mouser.matching')


@pytest.mark.parametrize('a,b,expected', [
    ('DT04-12PA', 'DT04-12PA-E005', True),        # variant suffix
    ('DT04-12PA-B016', 'DT04-12PA-E005', True),   # sibling variants - flagged for a person, never auto-linked
    ('dt04 12pa', 'DT04-12PA', True),             # spacing / case
    ('DT04-12PA', 'DT04-12PB', False),            # different key
    ('DT04-2P', 'DT04-12PA', False),
    ('RKEF135', 'RKEF110', False),
    ('ABC-1', 'ABC-2', False),                    # stem too short to mean anything
    ('', 'X', False),
])
def test_close(a, b, expected):
    assert matching.close(a, b) is expected


def test_stems():
    assert matching.stems('DT04-12PA-E005') == ['DT04-12PA-E005', 'DT04-12PA']
    assert matching.stems('1-1418390-1') == ['1-1418390-1', '1-1418390']


def test_network_error_never_shows_the_key(monkeypatch):
    def refuse(method, url, params=None, **kwargs):
        full = f"{url}?apiKey={params['apiKey']}"
        raise http.requests.exceptions.ConnectionError(f"Max retries exceeded with url: {full} (refused)")
    monkeypatch.setattr(http.requests, 'request', refuse)
    with pytest.raises(MouserError) as caught:
        orders.order_history('SECRET-KEY-123', __import__('datetime').date(2026, 10, 1),
                             __import__('datetime').date(2026, 10, 2))
    assert 'SECRET-KEY-123' not in str(caught.value) and 'apiKey=***' in str(caught.value)
    assert caught.value.__cause__ is None      # the original (with the key) isn't chained into tracebacks


def test_http_error_never_shows_the_key(monkeypatch):
    class R:
        status_code = 403
        text = 'Forbidden for /api/v1/search/partnumber?apiKey=SECRET-KEY-123'

        def json(self):
            raise ValueError
    monkeypatch.setattr(http.requests, 'request', lambda *a, **k: R())
    with pytest.raises(MouserError) as caught:
        orders.order_detail('SECRET-KEY-123', sales_order='1')
    assert 'SECRET-KEY-123' not in str(caught.value)


def test_search_goes_through_request_and_logs_no_key(monkeypatch, caplog):
    from mouser_lookup import client, search_by_mpn
    seen = []

    def fake(method, path, api_key, *, params=None, json=None):
        seen.append((method, path, json))
        raise MouserError(http.redact('Couldn\'t reach Mouser: url ?apiKey=SECRET-KEY-123', api_key))
    monkeypatch.setattr(client.http, 'request', fake)
    with caplog.at_level('WARNING'):
        assert search_by_mpn('DT04-2P', 'SECRET-KEY-123') == []
    assert seen == [('POST', '/search/partnumber', {'SearchByPartRequest': {'mouserPartNumber': 'DT04-2P'}})]
    assert 'SECRET-KEY-123' not in caplog.text


def test_search_result_has_mouser_category():
    from mouser_lookup import map_mouser_response
    parts = map_mouser_response({'SearchResults': {'Parts': [
        {'ManufacturerPartNumber': 'DT04-2P-E005', 'Category': 'Automotive Connectors'},
        {'ManufacturerPartNumber': 'RKEF135'}]}})
    assert [p['category'] for p in parts] == ['Automotive Connectors', '']
