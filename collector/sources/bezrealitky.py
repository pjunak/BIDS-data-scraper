"""Bezrealitky.cz via its public GraphQL API (the one the website uses).

Owner listings only, no agencies. The query lives in bezrealitky_query.graphql, so a field can be
added or fixed without touching code; when the API rejects a field, its error message names it.
"""
import logging
from pathlib import Path

log = logging.getLogger(__name__)

API = "https://api.bezrealitky.cz/graphql/"
QUERY = Path(__file__).with_name("bezrealitky_query.graphql").read_text(encoding="utf-8")
OFFER = {"PRODEJ": "prodej", "PRONAJEM": "pronajem"}
ESTATE = {"BYT": "byt", "DUM": "dum"}


def _disposition(code: str | None) -> str | None:
    """'DISP_2_KK' -> '2+kk', 'DISP_3_1' -> '3+1'."""
    if code and code.startswith("DISP_"):
        return code[5:].replace("_KK", "+kk").replace("_", "+").lower()
    return code


def parse(advert: dict) -> dict:
    gps = advert.get("gps") or {}
    uri = advert.get("uri")
    return {
        "source": "bezrealitky",
        "listing_id": str(advert.get("id")),
        "offer_type": OFFER.get(advert.get("offerType"), advert.get("offerType")),
        "estate_type": ESTATE.get(advert.get("estateType"), advert.get("estateType")),
        "title": advert.get("imageAltText") or advert.get("address"),  # "Prodej bytu 4+1 82 m², Ulice, Brno"
        "price": (advert.get("price") or None) if advert.get("currency", "CZK") == "CZK" else None,
        "area_m2": advert.get("surface"),
        "disposition": _disposition(advert.get("disposition")),
        "locality": advert.get("address"),
        "lat": gps.get("lat"),
        "lon": gps.get("lng"),
        "url": f"https://www.bezrealitky.cz/nemovitosti-byty-domy/{uri}" if uri else None,
    }


def iter_listings(http, cfg: dict, max_pages: int | None = None):
    for offer in cfg["offer_types"]:
        for estate in cfg["estate_types"]:
            yield from _list_category(http, cfg, offer, estate, max_pages)


def _list_category(http, cfg: dict, offer: str, estate: str, max_pages: int | None):
    """All adverts of one offer/estate type, page by page (the API returns at most 100 per page)."""
    page_size = cfg.get("page_size", 100)
    offset = 0
    for page in range(1, (max_pages or 10**6) + 1):
        variables = {"offerType": [offer], "estateType": [estate], "regionOsmIds": cfg["region_osm_ids"],
                     "limit": page_size, "offset": offset}
        data = http.post_json(API, {"operationName": "ListAdverts", "variables": variables, "query": QUERY})
        if data.get("errors"):
            raise RuntimeError(f"bezrealitky GraphQL error: {data['errors']}")
        result = data["data"]["listAdverts"]
        adverts = result.get("list") or []
        total = result.get("totalCount") or 0
        if page == 1:
            log.info("bezrealitky %s/%s: %s listings reported", estate, offer, total)
        for advert in adverts:
            yield parse(advert), advert
        offset += len(adverts)
        if not adverts or offset >= total:
            return


fetch_detail = None  # the list payload already carries everything
