"""
Turns one normalized Mouser result (see client.map_mouser_response) into
the field shape each consumer actually needs — so neither OMG nor the
InvenTree plugin has to know Mouser's raw attribute names, and a change to
one target's fields doesn't touch the other.
"""

from typing import Any, Dict, Optional


def to_inventree_payload(result: Dict[str, Any], category_pk: Optional[int] = None) -> Dict[str, Any]:
    """
    Prefill dict for creating a Part directly in InvenTree from a Mouser
    result. Matches InvenTree's Part.create()/Part.save() field names.

    This does NOT create anything — the caller (the InvenTree plugin, since
    it runs inside InvenTree's own process) still decides whether to create
    or to check for an existing part by IPN first.
    """
    return {
        'name': result.get('mpn') or result.get('spn') or '',
        'IPN': result.get('mpn') or '',
        'description': result.get('description') or '',
        'link': result.get('url') or '',
        'category': category_pk,
        'active': True,
        'virtual': False,
        'purchaseable': True,
        # Datasheet is an attachment in InvenTree, not a plain field — if you
        # want it pulled in automatically, upload result['datasheet'] as a
        # PartAttachment after the Part is created, not as part of this dict.
    }
