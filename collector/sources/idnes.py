"""reality.idnes.cz via HTML scraping of the paginated search results.

The most fragile source: when the page layout changes, page 1 parses to 0 items and the run
fails loudly instead of silently saving nothing. The pages carry no GPS, only the address text.
"""
import logging
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..normalize import area_from_text, disposition_from_text, price_from_text

log = logging.getLogger(__name__)

BASE = "https://reality.idnes.cz"
LISTING_ID = re.compile(r"/([0-9a-f]{20,})/?$")


def _text(node) -> str | None:
    return " ".join(node.get_text(" ", strip=True).split()) if node else None


def parse_page(html: str, offer: str, estate: str) -> list[tuple[dict, dict]]:
    """(record, raw) for every listing on one result page; ads are skipped."""
    items = BeautifulSoup(html, "html.parser").select(".c-products__item")
    parsed = (_parse_item(item, offer, estate) for item in items
              if "advertisment" not in " ".join(item.get("class", [])))
    return [listing for listing in parsed if listing]


def _parse_item(item, offer: str, estate: str) -> tuple[dict, dict] | None:
    link = item.select_one("a.c-products__link") or item.select_one("a[href*='/detail/']")
    if not link or not link.get("href"):
        return None
    url = urljoin(BASE, link["href"].split("?")[0])
    match = LISTING_ID.search(url)
    title = _text(item.select_one(".c-products__title"))
    info = _text(item.select_one(".c-products__info"))
    price = _text(item.select_one(".c-products__price"))
    badges = [_text(b) for b in item.select(".badges__item, .badges-tip__item")]  # e.g. Zlevněno, Tip regionu
    raw = {"url": url, "title": title, "info": info, "price": price, "badges": badges}
    record = {
        "source": "idnes",
        "listing_id": match.group(1) if match else url,
        "offer_type": offer,
        "estate_type": estate,
        "title": title,
        "price": price_from_text(price),
        "area_m2": area_from_text(title),
        "disposition": disposition_from_text(title),
        "locality": info,
        "lat": None,
        "lon": None,
        "url": url,
    }
    return record, raw


def _fetch_page(http, url: str, page: int) -> str | None:
    """HTML of one result page, or None past the last page (the site answers 404 there)."""
    try:
        return http.get(url if page == 1 else f"{url}?page={page}").text
    except requests.HTTPError as e:
        if page > 1 and e.response is not None and e.response.status_code == 404:
            return None
        raise


def iter_listings(http, cfg: dict, max_pages: int | None = None):
    page_limit = min(cfg.get("max_pages", 300), max_pages or 10**6)
    for search in cfg["searches"]:
        seen = set()
        for page in range(1, page_limit + 1):
            html = _fetch_page(http, search["url"], page)
            listings = parse_page(html, search["offer"], search["estate"]) if html is not None else []
            if not listings and page == 1:
                raise RuntimeError(f"idnes: 0 items parsed on first page of {search['url']} - layout changed?")
            fresh = [(rec, raw) for rec, raw in listings if rec["listing_id"] not in seen]
            if not fresh:  # past the last page, or the site repeated the last page
                break
            for rec, raw in fresh:
                seen.add(rec["listing_id"])
                yield rec, raw
        log.info("idnes %s %s: %s listings", search["estate"], search["offer"], len(seen))


fetch_detail = None  # the result page already carries everything
