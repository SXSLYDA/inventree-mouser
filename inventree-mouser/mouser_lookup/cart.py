"""
Mouser Cart API - put parts into a cart on your Mouser account, to check
out on Mouser's website. Needs the Order API key (My Mouser > APIs).

    from mouser_lookup import cart
    result = cart.add_items(key, [{"mouser_pn": "571-DT04-2P-E005", "quantity": 100,
                                   "customer_pn": "DT04-2P-E005"}], country="AU", currency="AUD")
    result["cart_key"]   # pass it back in to add to the same cart
"""

from typing import Any, Dict, List, Optional

# Mouser needs a country code on cart calls (it sets the cart's currency and pricing);
# the currency a country gets, for working the country out from a currency.
CURRENCY_COUNTRIES = {'AUD': 'AU', 'CAD': 'CA', 'CNY': 'CN', 'GBP': 'GB', 'JPY': 'JP', 'NZD': 'NZ',
                      'USD': 'US', 'EUR': 'DE', 'SGD': 'SG', 'HKD': 'HK', 'INR': 'IN', 'CHF': 'CH'}

from .http import request
from .orders import _decimal, _text


def map_cart(body: Dict[str, Any]) -> Dict[str, Any]:
    """Mouser's cart response as {cart_key, currency, merchandise_total, item_count, lines, errors}."""
    lines = []
    for item in body.get("CartItems") or []:
        if not isinstance(item, dict):
            continue
        lines.append({
            "mouser_pn": _text(item.get("MouserPartNumber")),
            "mpn": _text(item.get("MfrPartNumber")),
            "customer_pn": _text(item.get("CartItemCustPartNumber")),
            "quantity": int(item.get("Quantity") or 0),
            "unit_price": _decimal(item.get("UnitPrice")),
            "ext_price": _decimal(item.get("ExtendedPrice")),
            "availability": item.get("MouserATS"),
            "min_qty": item.get("SalesMinimumOrderQty"),
            "multiple_qty": item.get("SalesMultipleQty"),
            "errors": [_text(e.get("Message")) for e in item.get("Errors") or [] if isinstance(e, dict)],
            "info": [_text(m) for m in item.get("InfoMessages") or [] if m],
        })
    return {
        "cart_key": _text(body.get("CartKey")),
        "currency": _text(body.get("CurrencyCode")).upper(),
        "merchandise_total": _decimal(body.get("MerchandiseTotal")),
        "item_count": body.get("TotalItemCount"),
        "lines": lines,
    }


def add_items(api_key: str, items: List[Dict[str, Any]], *, cart_key: str = "",
              country: Optional[str] = None, currency: Optional[str] = None) -> Dict[str, Any]:
    """
    Add items ({mouser_pn, quantity, customer_pn?}) to a cart - a new cart
    when cart_key is blank. Returns map_cart(); per-line problems (unknown
    part, below minimum quantity...) are in each line's "errors", not raised.
    """
    return map_cart(request("POST", "/cart/items/insert", api_key, params=_params(country, currency),
                            json=_payload(items, cart_key)))


def replace_items(api_key: str, cart_key: str, items: List[Dict[str, Any]], *,
                  country: Optional[str] = None, currency: Optional[str] = None) -> Dict[str, Any]:
    """
    Make an existing cart hold exactly these items (Mouser's update-cart call) -
    so sending the same purchase order again updates its cart instead of making
    another one. Raises MouserError if Mouser no longer has that cart.
    """
    return map_cart(request("POST", "/cart", api_key, params=_params(country, currency),
                            json=_payload(items, cart_key)))


def _payload(items, cart_key):
    return {
        "CartKey": cart_key or None,
        "CartItems": [
            {"MouserPartNumber": i["mouser_pn"], "Quantity": int(i["quantity"]),
             **({"CustomerPartNumber": str(i["customer_pn"])} if i.get("customer_pn") else {})}
            for i in items
        ],
    }


def _params(country, currency):
    return {k: v for k, v in (("countryCode", country), ("currencyCode", currency)) if v}
