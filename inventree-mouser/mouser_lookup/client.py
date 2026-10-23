"""
Mouser API client — no Django dependency, so this works the same whether
it's imported from OMG Harness or from inside the InvenTree plugin process.

Usage:
    from mouser_lookup import search_by_mpn

    results = search_by_mpn('DT04-12PA', api_key='your-key')
    # -> list of normalized dicts (see map_mouser_response for the shape)
"""

import logging
import re
from typing import Any, Dict, List, Optional

from . import http
from .orders import _decimal

logger = logging.getLogger(__name__)



def _clean_str(val: Any) -> str:
    # None must become "", not "None": str(None) is the literal text
    # "None", which then got used as a real value - an image URL of
    # "None", a product link of "None", even a manufacturer Company named
    # "None" in InvenTree for parts Mouser lists without one.
    if val is None:
        return ""
    try:
        return str(val).strip()
    except Exception:
        return ""


def _to_int(val: Any) -> Optional[int]:
    try:
        s = ''.join(ch for ch in str(val) if ch.isdigit())
        return int(s) if s else None
    except Exception:
        return None


def _to_float(val: Any) -> Optional[float]:
    try:
        s = str(val or '')
        m = re.search(r'([0-9]+(?:\.[0-9]+)?)', s)
        return float(m.group(1)) if m else None
    except Exception:
        return None


def _extract_contact_count(attrs: Dict[str, str], text_fields: List[str]) -> Optional[int]:
    """Extract contact/position count from attributes or description text."""
    attr_keys = [
        'Number of Positions', 'Positions', 'Pins', 'Contacts',
        'Circuits', 'Ways', 'Poles', 'Positions Per Row',
        'Rows', 'Contacts Per Port', 'Positions Loaded'
    ]

    best = None
    for key in attr_keys:
        val = attrs.get(key)
        if not val:
            continue
        num = _to_int(val)
        if num and num > 0:
            best = max(best or 0, num)

    ppr = _to_int(attrs.get('Positions Per Row'))
    rows = _to_int(attrs.get('Rows'))
    if ppr and rows and ppr * rows > (best or 0):
        best = ppr * rows

    if best:
        return best

    patterns = [
        r'(\d+)\s*(positions?|pins?|contacts?|ways|poles|circuits?)',
        r'\b(\d{1,2})\s*P\b',
        r'\b(\d{1,2})\s*way(s)?\b',
        r'DT\d{2}-(\d{1,2})[A-Z]',
    ]
    for text in text_fields:
        if not text:
            continue
        for pat in patterns:
            m = re.search(pat, str(text), re.I)
            if m:
                try:
                    return int(m.group(1))
                except (ValueError, IndexError):
                    pass
    return None


def _extract_gender(attrs: Dict[str, str], text_fields: List[str]) -> Optional[str]:
    """Returns 'M', 'F', 'E', or None."""
    gender_keys = ['Gender', 'Connector Gender', 'Contact Gender']
    for key in gender_keys:
        val = attrs.get(key)
        if not val:
            continue
        g = str(val).lower().strip()
        if any(x in g for x in ('female', 'socket', 'receptacle', 'rcpt', 'recp')):
            return 'F'
        if any(x in g for x in ('male', 'plug', 'pin', 'header', 'tab')):
            return 'M'
        if 'eyelet' in g:
            return 'E'

    for text in text_fields:
        if not text:
            continue
        g = str(text).lower()
        if any(x in g for x in ('female', 'socket', 'receptacle', 'rcpt', 'recp')):
            return 'F'
        if any(x in g for x in ('male', 'plug', 'pin', 'header', 'tab')):
            return 'M'
        if 'eyelet' in g:
            return 'E'
    return None


def _extract_conductor_sizes(attrs: Dict[str, str]) -> Dict[str, Optional[float]]:
    """Handles both AWG and mm2 formats. Returns conductor_size_min/max."""
    result = {'conductor_size_min': None, 'conductor_size_max': None}

    def parse_awg_range(val):
        s = str(val or '').strip()
        m = re.search(r'(\d{1,2})\s*(?:-|to)\s*(\d{1,2})\s*AWG', s, re.I)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            return min(a, b), max(a, b)
        m = re.search(r'(\d{1,2})\s*AWG', s, re.I)
        if m:
            n = int(m.group(1))
            return n, n
        return None, None

    def parse_mm2_range(val):
        s = str(val or '').strip()
        m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*(?:-|to)\s*([0-9]+(?:\.[0-9]+)?)\s*mm\^?2', s, re.I)
        if m:
            return float(m.group(1)), float(m.group(2))
        m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*mm\^?2', s, re.I)
        if m:
            n = float(m.group(1))
            return n, n
        return None, None

    awg_keys = [
        'Wire Gauge', 'Wire Gauge (AWG)', 'Conductor Size (AWG)',
        'Contact Termination - Wire Size', 'Wire Size', 'Wire Size Range'
    ]
    for key in awg_keys:
        val = attrs.get(key)
        if val:
            mn, mx = parse_awg_range(val)
            if mn is not None:
                result['conductor_size_min'] = mn
                result['conductor_size_max'] = mx
                return result

    mm2_keys = ['Conductor Size (mm^2)', 'Cross Sectional Area', 'CSA']
    for key in mm2_keys:
        val = attrs.get(key)
        if val:
            mn, mx = parse_mm2_range(val)
            if mn is not None:
                result['conductor_size_min'] = mn
                result['conductor_size_max'] = mx
                return result

    return result


def _extract_price_breaks(part: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Mouser's PriceBreaks array looks like:
      [{"Quantity": 1, "Price": "$1.23", "Currency": "USD"}, ...]
    Returned as [{"quantity": int, "price": float, "currency": str}, ...],
    skipping any row that doesn't parse cleanly rather than raising —
    pricing data is a nice-to-have for the supplier-import wizard, not
    something that should break a search over one malformed row.
    """
    breaks = part.get("PriceBreaks") or []
    if not isinstance(breaks, list):
        return []

    results = []
    for row in breaks:
        if not isinstance(row, dict):
            continue
        qty = row.get("Quantity")
        raw_price = row.get("Price", "")
        currency = _clean_str(row.get("Currency")) or "USD"
        price = _decimal(raw_price)
        price = float(price) if price is not None else None
        if qty is not None and price is not None:
            try:
                results.append({"quantity": int(qty), "price": price, "currency": currency})
            except (TypeError, ValueError):
                continue
    return results


def map_mouser_response(mouser_data: Any) -> List[Dict[str, Any]]:
    """Transform a raw Mouser API response into a normalized part list."""
    if isinstance(mouser_data, tuple):
        mouser_data = mouser_data[0]

    try:
        parts = (mouser_data or {}).get("SearchResults", {}).get("Parts", [])
    except (AttributeError, TypeError):
        parts = []

    if not parts:
        return []

    results = []
    for part in parts:
        attr_lists = []
        for attr_key in ['ProductAttributes', 'Attributes']:
            attr_data = part.get(attr_key)
            if isinstance(attr_data, list):
                attr_lists.extend(attr_data)

        attrs = {
            _clean_str(attr.get("AttributeName", "")): _clean_str(attr.get("AttributeValue", ""))
            for attr in attr_lists if isinstance(attr, dict)
        }

        mpn = _clean_str(
            part.get("ManufacturerPartNumber") or
            part.get("ManufacturerPartNum") or
            part.get("Manufacturer Part Number")
        )
        spn = _clean_str(
            part.get("MouserPartNumber") or
            part.get("Mouser Part Number") or
            part.get("SellerPartNumber")
        )
        manufacturer = _clean_str(
            part.get("Manufacturer") or
            part.get("ManufacturerName") or
            part.get("Brand")
        )
        description = _clean_str(
            part.get("Description") or
            part.get("ProductDescription") or
            part.get("MultilineDescription")
        )
        url = _clean_str(part.get("ProductDetailUrl") or part.get("ProductUrl"))
        image = _clean_str(
            part.get("ImagePath") or
            part.get("ImageURL") or
            part.get("ImageUrl")
        )
        datasheet = _clean_str(
            part.get("DataSheetUrl") or
            part.get("DatasheetURL") or
            part.get("DatasheetUrl")
        )

        text_fields = [description, mpn]
        conductor_sizes = _extract_conductor_sizes(attrs)

        outer_diameter = None
        for key in ['Outside Diameter', 'Outer Diameter', 'Overall Diameter', 'Jacket Diameter']:
            if attrs.get(key):
                outer_diameter = _to_float(attrs.get(key))
                if outer_diameter is not None:
                    break

        primary_color = _clean_str(attrs.get('Primary Color') or attrs.get('Color')) or None
        secondary_color = _clean_str(attrs.get('Secondary Color') or attrs.get('Stripe Color')) or None

        results.append({
            'mpn': mpn,
            'spn': spn,
            'description': description,
            'manufacturer': manufacturer,
            'url': url,
            'image': image,
            'datasheet': datasheet,
            # Mouser's product category ("Automotive Connectors", "Resettable Fuses - PPTC")
            'category': _clean_str(part.get('Category')),
            'attributes': attrs,
            'contact_count': _extract_contact_count(attrs, text_fields),
            'gender': _extract_gender(attrs, text_fields),
            'conductor_size_min': conductor_sizes.get('conductor_size_min'),
            'conductor_size_max': conductor_sizes.get('conductor_size_max'),
            'outer_diameter': outer_diameter,
            'primary_color': primary_color,
            'secondary_color': secondary_color,
            'vendor': 'mouser',
            'price_breaks': _extract_price_breaks(part),
            'in_stock': _to_int(part.get('AvailabilityInStock') or part.get('Availability')),
        })

    return results


def search_by_mpn(mpn: str, api_key: str) -> List[Dict[str, Any]]:
    """
    Search Mouser by manufacturer part number.
    Returns a list of normalized part dicts, or [] on error/no key.
    Goes through http.request like every other call, so its errors (logged,
    not raised) never carry the API key.
    """
    if not api_key:
        return []

    payload = {"SearchByPartRequest": {"mouserPartNumber": mpn}}

    try:
        body = http.request("POST", "/search/partnumber", api_key, json=payload)
    except http.MouserError as exc:
        logger.warning("Mouser API error: %s", exc)
        return []
    try:
        return map_mouser_response(body)
    except Exception as exc:  # malformed response body, etc.
        logger.warning("Mouser response parsing error: %s", http.redact(exc, api_key))
        return []
