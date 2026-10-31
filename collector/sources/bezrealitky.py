"""Bezrealitky.cz via its public GraphQL API (the one the website uses).

The query lives in bezrealitky_query.graphql so field names can be fixed without touching code.
If the API rejects a field, the error from the server is logged verbatim.
"""
import logging
from pathlib import Path

log = logging.getLogger(__name__)

API = "https://api.bezrealitky.cz/graphql/"
QUERY = (Path(__file__).with_name("bezrealitky_query.graphql")).read_text(encoding="utf-8")
OFFER = {"PRODEJ": "prodej", "PRONAJEM": "pronajem"}
ESTATE = {"BYT": "byt", "DUM": "dum"}


def parse(a: dict) -> dict:
    gps = a.get("gps") or {}
    disp = a.get("disposition")  # e.g. DISP_2_KK
    if disp and disp.startswith("DISP_"):
        disp = disp[5:].replace("_KK", "+kk").replace("_", "+").lower()
    return {
        "source": "bezrealitky",
        "listing_id": str(a.get("id")),
        "offer_type": OFFER.get(a.get("offerType"), a.get("offerType")),
        "estate_type": ESTATE.get(a.get("estateType"), a.get("estateType")),
        "title": a.get("imageAltText") or a.get("address"),  # "Prodej bytu 4+1 82 m², Prušánecká, Brno"
        "price": (a.get("price") or None) if a.get("currency", "CZK") == "CZK" else None,
        "area_m2": a.get("surface"),
        "disposition": disp,
        "locality": a.get("address"),
        "lat": gps.get("lat"),
        "lon": gps.get("lng"),
        "url": f"https://www.bezrealitky.cz/nemovitosti-byty-domy/{a['uri']}" if a.get("uri") else None,
    }


def iter_listings(http, cfg: dict, max_pages: int | None = None):
    size = cfg.get("page_size", 100)
    for offer in cfg["offer_types"]:
        for estate in cfg["estate_types"]:
            offset, page = 0, 1
            while True:
                variables = {"offerType": [offer], "estateType": [estate],
                             "regionOsmIds": cfg["region_osm_ids"], "limit": size, "offset": offset}
                data = http.post_json(API, {"operationName": "ListAdverts",
                                            "variables": variables, "query": QUERY})
                if data.get("errors"):
                    raise RuntimeError(f"bezrealitky GraphQL error: {data['errors']}")
                block = data["data"]["listAdverts"]
                items = block.get("list") or []
                if page == 1:
                    log.info("bezrealitky %s/%s: %s listings reported", estate, offer, block.get("totalCount"))
                if not items:
                    break
                for a in items:
                    yield parse(a), a
                offset += len(items)
                page += 1
                if offset >= (block.get("totalCount") or 0) or (max_pages and page > max_pages):
                    break


fetch_detail = None  # list payload already carries what we need
