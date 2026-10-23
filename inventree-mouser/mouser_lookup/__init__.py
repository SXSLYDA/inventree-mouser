"""
Mouser Electronics API client - no Django or InvenTree dependency, so OMG
Harness and the InvenTree plugin (inventree_mouser) use the same parsing.

    search_by_mpn(mpn, search_key)             part search (Search API key)
    orders.order_history / orders.order_detail your Mouser orders (Order API key)
    cart.add_items                             a cart on your Mouser account (Order API key)
    prefill.to_inventree_payload               a search result as Part fields
"""

from . import cart, orders, prefill
from .client import map_mouser_response, search_by_mpn
from .http import MouserError
from .version import MOUSER_LOOKUP_VERSION as __version__

__all__ = ["search_by_mpn", "map_mouser_response", "prefill", "orders", "cart", "MouserError", "__version__"]
