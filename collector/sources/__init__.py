"""One module per real-estate portal.

Each module provides:

    iter_listings(http, cfg, max_pages=None)  yields (record, raw_payload) for every active listing;
                                              record has the listing_snapshot columns (source,
                                              listing_id, offer_type, estate_type, title, price, ...)
    fetch_detail(http, listing_id)            the raw detail of one listing, or None if the list
                                              payload already has everything

`cfg` is the portal's section of config.yaml. A layout or API change must raise, not yield nothing.
"""
from . import bezrealitky, idnes, sreality

SOURCES = {
    "sreality": sreality,
    "bezrealitky": bezrealitky,
    "idnes": idnes,
}
