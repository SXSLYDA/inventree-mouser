"""
Which InvenTree part a Mouser order line is.

match_line() tries, in order, and stops at the first that answers:
  1. a Mouser supplier part with this Mouser part number as its SKU
     -> "supplier_part" (nothing to create)
  2. the customer part number on the Mouser line (what a cart sent from a
     purchase order carries - the part's IPN or name), when exactly one
     part has it -> "part"
  3. exact part number: a part named / IPN'd with the MPN, a manufacturer
     part with the MPN (e.g. an Amphenol MPN added to a TE part), or any
     supplier's SKU equal to the MPN or Mouser number
       one part  -> "part"  (a Mouser supplier part is added to it)
       several   -> "ambiguous" (a person picks)
  4. close part number: the same once spaces / dashes / case are ignored,
     or the same apart from a trailing -suffix (packaging, variant: DT04-12PA
     vs DT04-12PA-E005) -> "possible" (a person decides - never auto-linked)
  5. nothing -> "new" (created from Mouser)

Only 1, 2, 3-with-one-part and 5 let an order through on its own; 3-several
and 4 hold the order for review (the hybrid rule in order_import.py).
"""

import re

from django.db.models import Q

CANDIDATE_LIMIT = 10
MIN_STEM = 5


def norm(value):
    """Part number with case, spaces and punctuation ignored."""
    return re.sub(r'[^A-Z0-9]', '', (value or '').upper())


def stems(value):
    """The part number and its shorter forms with trailing -/ segments removed (at least MIN_STEM characters)."""
    value = (value or '').strip()
    out = [value] if value else []
    while True:
        cut = max(value.rfind('-'), value.rfind('/'))
        if cut <= 0:
            break
        value = value[:cut].strip()
        if len(norm(value)) < MIN_STEM:
            break
        out.append(value)
    return out


def close(a, b):
    """True when a and b are the same part number apart from punctuation or a trailing -suffix."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    forms_a = {norm(s) for s in stems(a)}
    forms_b = {norm(s) for s in stems(b)}
    return bool(forms_a & forms_b) and min(len(na), len(nb)) >= MIN_STEM


class LineMatch:
    """What match_line found."""

    def __init__(self, match, part=None, supplier_part=None, candidates=None, message=''):
        self.match = match
        self.part = part
        self.supplier_part = supplier_part
        self.candidates = candidates or []
        self.message = message


def _parts_exact(values):
    """Parts that ARE one of these part numbers (name, IPN, a manufacturer MPN, any supplier SKU)."""
    from company.models import ManufacturerPart, SupplierPart
    from part.models import Part

    values = [v for v in {(v or '').strip() for v in values} if v]
    if not values:
        return []
    q_part, q_mp, q_sp = Q(), Q(), Q()
    for v in values:
        q_part |= Q(name__iexact=v) | Q(IPN__iexact=v)
        q_mp |= Q(MPN__iexact=v)
        q_sp |= Q(SKU__iexact=v)
    pks = set(Part.objects.filter(q_part).values_list('pk', flat=True))
    pks |= set(ManufacturerPart.objects.filter(q_mp).values_list('part_id', flat=True))
    pks |= set(SupplierPart.objects.filter(q_sp).values_list('part_id', flat=True))
    pks.discard(None)
    return list(Part.objects.filter(pk__in=pks).order_by('-active', 'name'))


def _parts_close(mpn):
    """Parts whose part number is close() to mpn but not exactly it."""
    from company.models import ManufacturerPart, SupplierPart
    from part.models import Part

    forms = stems(mpn)
    if not forms:
        return []
    search = forms[-1]  # the shortest form: finds every variant sharing the stem
    found = {}
    for part in Part.objects.filter(Q(name__icontains=search) | Q(IPN__icontains=search))[:50]:
        if close(mpn, part.name) or close(mpn, part.IPN):
            found[part.pk] = part
    for mp in ManufacturerPart.objects.filter(MPN__icontains=search).select_related('part')[:50]:
        if mp.part and close(mpn, mp.MPN):
            found[mp.part.pk] = mp.part
    for sp in SupplierPart.objects.filter(SKU__icontains=search).select_related('part')[:50]:
        if sp.part and close(mpn, sp.SKU):
            found[sp.part.pk] = sp.part
    return sorted(found.values(), key=lambda p: (not p.active, p.name))[:CANDIDATE_LIMIT]


def _usable(part):
    """A part a purchase order line can go to without asking: active and purchaseable."""
    return part.active and part.purchaseable


def match_line(line, mouser_company):
    """
    LineMatch for one order line (dict or object with mouser_pn, mpn, customer_pn).
    mouser_company: the Company that is Mouser (the plugin's Supplier setting).
    """
    from company.models import SupplierPart
    from part.models import Part

    get = line.get if isinstance(line, dict) else (lambda k, d='': getattr(line, k, d))
    mouser_pn = (get('mouser_pn', '') or '').strip()
    mpn = (get('mpn', '') or '').strip()
    customer_pn = (get('customer_pn', '') or '').strip()

    # 1. the Mouser supplier part
    if mouser_pn:
        sp = (SupplierPart.objects.filter(supplier=mouser_company, SKU__iexact=mouser_pn)
              .select_related('part').order_by('-active', 'pk').first())
        if sp and sp.part:
            return LineMatch('supplier_part', part=sp.part, supplier_part=sp)

    # 2. the customer part number (set by a cart sent from a purchase order)
    if customer_pn:
        parts = list(Part.objects.filter(Q(IPN__iexact=customer_pn) | Q(name__iexact=customer_pn))[:2])
        if len(parts) == 1 and _usable(parts[0]):
            return LineMatch('part', part=parts[0], message=f'Matched on customer part number {customer_pn}.')

    # 3. exact part number
    parts = _parts_exact([mpn, mouser_pn])
    if len(parts) == 1 and _usable(parts[0]):
        return LineMatch('part', part=parts[0])
    if len(parts) == 1:
        why = 'inactive' if not parts[0].active else 'not purchaseable'
        return LineMatch('possible', candidates=[parts[0].pk],
                         message=f'{parts[0].name} is this part number but is {why} - use it anyway '
                                 '(fix the part first) or create a new part.')
    if len(parts) > 1:
        return LineMatch('ambiguous', candidates=[p.pk for p in parts[:CANDIDATE_LIMIT]],
                         message=f'{len(parts)} parts have the part number {mpn or mouser_pn} - pick one.')

    # 4. close part number
    parts = _parts_close(mpn) if mpn else []
    if parts:
        names = ', '.join(p.name for p in parts[:3])
        return LineMatch('possible', candidates=[p.pk for p in parts],
                         message=f'Not in InvenTree as {mpn}, but close: {names}. Use one of them or create a new part.')

    # 5. new
    return LineMatch('new')
