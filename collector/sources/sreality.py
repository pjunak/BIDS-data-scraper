"""Sreality.cz via the JSON API used by the website itself (/api/v1).

Category codes: category_main_cb 1=byty, 2=domy; category_type_cb 1=prodej, 2=pronájem.
locality_region_id 14 = Jihomoravský kraj.
"""
import logging
import re
import unicodedata

from ..normalize import area_from_text, disposition_from_text

log = logging.getLogger(__name__)

API = "https://www.sreality.cz/api/v1/estates"
MAIN = {1: "byt", 2: "dum"}
TYPE = {1: "prodej", 2: "pronajem"}
# Bulky or personal parts of the API responses that are not stored: photos, nearby POIs,
# and the broker's name, e-mail and phone numbers.
DROP_LIST = ("advert_images", "advert_images_all")
DROP_DETAIL = DROP_LIST + ("user", "premise", "labels", "labels_extended", "videos", "panorama_data")


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9+]+", "-", ascii_text.lower()).strip("-")


def _code(estate: dict, key: str):
    return (estate.get(key) or {}).get("value")


def _locality(loc: dict) -> str | None:
    """'Komárovská, Brno - Komárov', the same shape the website shows."""
    city, part = loc.get("city"), loc.get("citypart")
    place = f"{city} - {part}" if city and part and part != city else city
    return ", ".join(p for p in (loc.get("street"), place) if p) or None


def parse(estate: dict, main: int, typ: int) -> dict:
    loc = estate.get("locality") or {}
    title = estate.get("advert_name")
    sub = (estate.get("category_sub_cb") or {}).get("name") or ""
    main = _code(estate, "category_main_cb") or main
    typ = _code(estate, "category_type_cb") or typ
    # price_summary is normalised to the whole property (sale) or one month (rent); 0 = "cena na dotaz"
    price = estate.get("price_summary_czk") or estate.get("price_czk") or None
    hid = estate.get("hash_id")
    return {
        "source": "sreality",
        "listing_id": str(hid),
        "offer_type": TYPE.get(typ),
        "estate_type": MAIN.get(main),
        "title": title,
        "price": int(price) if price else None,
        "area_m2": area_from_text(title),
        "disposition": disposition_from_text(sub) or disposition_from_text(title),
        "locality": _locality(loc),
        "lat": loc.get("gps_lat"),
        "lon": loc.get("gps_lon"),
        # The site redirects any slug to the canonical one; only type/category/subcategory must match.
        "url": (f"https://www.sreality.cz/detail/{TYPE.get(typ)}/{MAIN.get(main)}/{_slug(sub) or 'x'}/x/{hid}"
                if hid else None),
    }


def iter_listings(http, cfg: dict, max_pages: int | None = None):
    per_page = cfg.get("per_page", 100)
    region = cfg["region_id"]
    for cat in cfg["categories"]:
        main, typ = cat["main"], cat["type"]
        page, offset, other_region = 1, 0, 0
        while True:
            params = {"category_main_cb": main, "category_type_cb": typ,
                      "locality_region_id": region, "limit": per_page, "offset": offset}
            data = http.get(f"{API}/search", params=params).json()
            estates = data.get("results")
            total = (data.get("pagination") or {}).get("total")
            if estates is None or total is None:
                raise RuntimeError(f"sreality: unexpected response {str(data)[:300]}")
            if page == 1:
                log.info("sreality %s/%s: %s listings reported", MAIN[main], TYPE[typ], total)
            if not estates:
                break
            for e in estates:
                if (e.get("locality") or {}).get("region_id") not in (None, region):
                    other_region += 1  # promoted listings from elsewhere
                    continue
                yield parse(e, main, typ), {k: v for k, v in e.items() if k not in DROP_LIST}
            offset += len(estates)
            page += 1
            if offset >= total or (max_pages and page > max_pages):
                break
        if other_region:
            log.info("sreality %s/%s: skipped %s listings from other regions", MAIN[main], TYPE[typ], other_region)


def fetch_detail(http, listing_id: str):
    result = http.get(f"{API}/{listing_id}").json().get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"sreality detail {listing_id}: no result")
    return {k: v for k, v in result.items() if k not in DROP_DETAIL}
