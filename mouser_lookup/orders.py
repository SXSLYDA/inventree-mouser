"""
Mouser Order History API - the orders placed on your Mouser account.
Needs the Order API key (My Mouser > APIs), not the Search key.

    from mouser_lookup import orders
    for o in orders.order_history(key, date(2026, 10, 1), date(2026, 10, 22)):
        detail = orders.order_detail(key, sales_order=o["sales_order"])

Everything comes back normalized (see map_order_detail) so callers never
read Mouser's raw field names.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from .http import MouserError, request


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _decimal(value: Any) -> Optional[Decimal]:
    """A price from a number or a formatted string ("$1,234.50", "1.234,50 €"), else None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = "".join(ch for ch in str(value) if ch.isdigit() or ch in ".,-")
    if not text:
        return None
    if "," in text and "." in text:
        # whichever comes last is the decimal mark
        text = text.replace(",", "") if text.rfind(".") > text.rfind(",") else text.replace(".", "").replace(",", ".")
    elif "," in text:
        whole, _, frac = text.rpartition(",")
        thousands = len(frac) == 3 and whole.lstrip("-") not in ("", "0")
        text = text.replace(",", "") if thousands else f"{whole.replace(',', '')}.{frac}"
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _date(value: Any) -> str:
    """ISO date (YYYY-MM-DD) from Mouser's date-time text, else ''."""
    text = _text(value)
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10]


def _mouser_date(value: date) -> str:
    return value.strftime("%m/%d/%Y")


def map_order_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    """One row of an order history list."""
    return {
        "sales_order": _text(item.get("SalesOrderNumber")),
        "web_order": _text(item.get("WebOrderNumber")),
        "po_number": _text(item.get("PoNumber")),
        "order_date": _date(item.get("DateCreated")),
        "status": _text(item.get("OrderStatusDisplay")),
        "buyer": _text(item.get("BuyerName")),
    }


# Mouser documents a shipment ("Activity") as invoice number + date only; if it also sends how
# many were in it, it'll be under one of these.
SHIPMENT_QUANTITY_KEYS = ("Quantity", "QuantityShipped", "ShippedQuantity", "ShipQuantity", "ShippedQty")


def _shipment_quantity(activity: Dict[str, Any]) -> Optional[int]:
    for key in SHIPMENT_QUANTITY_KEYS:
        value = activity.get(key)
        if value not in (None, ""):
            try:
                return int(Decimal(str(value)))
            except (InvalidOperation, ValueError):
                return None
    return None


def map_order_detail(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    A Mouser order (Order History API "OrderDetail") as a plain dict:
      sales_order, web_order, po_number, status, order_date, currency, buyer,
      totals {merchandise, fees, total}, tracking [{number, link}],
      backorder_shipping_method,
      lines [{line_no, mouser_pn, mpn, manufacturer, description,
              customer_pn, quantity, unit_price, ext_price, invoices, activity_dates,
              shipments, shipped_quantity, fees [{code, amount}]}]
    Prices are Decimals. A line's ext_price is quantity x unit_price when
    Mouser leaves it out.

    shipments: one {invoice, date, quantity} per shipment (Mouser's "Activities"),
    oldest first - a back-ordered line has none yet, a line sent in parts has
    several. Mouser's API documents only the invoice number and date of each;
    quantity is filled only if Mouser includes one (SHIPMENT_QUANTITY_KEYS),
    and shipped_quantity is their total only when every shipment has one.
    """
    raw = raw or {}
    lines = []
    for index, line in enumerate(raw.get("OrderLines") or [], start=1):
        product = line.get("ProductInfo") or {}
        quantity = int(line.get("Quantity") or 0)
        unit = _decimal(line.get("UnitPrice"))
        if unit is None:
            unit = _decimal(line.get("FormattedUnitPrice"))
        ext = _decimal(line.get("ExtPrice"))
        if ext is None:
            ext = _decimal(line.get("FormattedExtendedPrice"))
        if ext is None and unit is not None:
            ext = unit * quantity
        activities = [a for a in (line.get("Activities") or []) if isinstance(a, dict)]
        shipments = sorted(({"invoice": _text(a.get("InvoiceNumber")), "date": _date(a.get("Date")),
                             "quantity": _shipment_quantity(a)} for a in activities),
                           key=lambda sh: (sh["date"], sh["invoice"]))
        known = [sh["quantity"] for sh in shipments]
        lines.append({
            "line_no": index,
            "mouser_pn": _text(product.get("MouserPartNumber")),
            "mpn": _text(product.get("ManufacturerPartNumber")),
            "manufacturer": _text(product.get("ManufacturerName")),
            "description": _text(product.get("PartDescription")),
            "customer_pn": _text(product.get("CustomerPartNumber")),
            "quantity": quantity,
            "unit_price": unit,
            "ext_price": ext,
            "invoices": sorted({str(a["InvoiceNumber"]) for a in activities if a.get("InvoiceNumber")}),
            "activity_dates": sorted({_date(a.get("Date")) for a in activities if a.get("Date")}),
            "shipments": shipments,
            "shipped_quantity": sum(known) if known and None not in known else None,
            # Mouser's additional fees on this line (tariff, handling...) - total for the line
            "fees": [{"code": _text(f.get("code") or f.get("Code")),
                      "amount": _decimal(f.get("extendedAmount") or f.get("ExtendedAmount")
                                         or f.get("amount") or f.get("Amount"))}
                     for f in line.get("AdditionalFees") or [] if isinstance(f, dict)],
        })
    payment = raw.get("PaymentDetail") or {}
    delivery = raw.get("DeliveryDetail") or {}
    summary = raw.get("SummaryDetail") or {}
    return {
        "sales_order": _text(raw.get("SalesOrderId")),
        "web_order": _text(raw.get("WebOrderId")),
        "po_number": _text(payment.get("PoNumber")),
        "payment_method": _text(payment.get("PaymentMethodName")),
        "status": _text(raw.get("OrderStatusName")),
        "order_date": _date(raw.get("OrderDate")),
        "currency": _text(raw.get("CurrencyCode")).upper(),
        "buyer": _text(raw.get("BuyerName")),
        "shipping_method": _text(delivery.get("ShippingMethodName")),
        "backorder_shipping_method": _text(delivery.get("BackOrderShippingMethodName")),
        "tracking": [{"number": _text(t.get("Number")), "link": _text(t.get("Link"))}
                     for t in delivery.get("TrackingDetails") or [] if isinstance(t, dict)],
        "totals": {
            "merchandise": _decimal(summary.get("MerchandiseTotal")),
            "fees": _decimal(summary.get("AdditionalFeesTotal")),
            "total": _decimal(summary.get("OrderTotal")),
        },
        "lines": lines,
    }


def order_history(api_key: str, start: date, end: date) -> List[Dict[str, Any]]:
    """Orders created from start to end (inclusive), newest first."""
    body = request("GET", "/orderhistory/ByDateRange", api_key,
                   params={"startDate": _mouser_date(start), "endDate": _mouser_date(end)})
    rows = [map_order_summary(item) for item in body.get("OrderHistoryItems") or [] if isinstance(item, dict)]
    return sorted((r for r in rows if r["sales_order"] or r["web_order"]),
                  key=lambda r: r["order_date"], reverse=True)


def order_detail(api_key: str, *, sales_order: str = "", web_order: str = "") -> Dict[str, Any]:
    """One order with its lines, by Mouser sales order number or web order number."""
    sales_order, web_order = _text(sales_order), _text(web_order)
    if sales_order:
        body = request("GET", "/orderhistory/salesOrderNumber", api_key, params={"salesOrderNumber": sales_order})
    elif web_order:
        body = request("GET", "/orderhistory/webOrderNumber", api_key, params={"webOrderNumber": web_order})
    else:
        raise MouserError("Give a sales order number or a web order number.")
    detail = map_order_detail(body)
    if not detail["sales_order"] and not detail["lines"]:
        raise MouserError(f"Mouser has no order {sales_order or web_order} on this account.")
    return detail
