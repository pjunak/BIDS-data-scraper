"""reality.idnes.cz via HTML scraping of the paginated search results.

HTML scraping is the most fragile source: if the page layout changes, the parser finds 0 items
on page 1 and the run fails loudly instead of silently saving nothing.
"""
import logging
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..normalize import area_from_text, disposition_from_text, price_from_text

log = logging.getLogger(__name__)

BASE = "https://reality.idnes.cz"
ID_RE = re.compile(r"/([0-9a-f]{20,})/?$")


def _text(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node else None


def parse_page(html: str, offer: str, estate: str) -> list[tuple[dict, dict]]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for item in soup.select(".c-products__item"):
        if "advertisment" in " ".join(item.get("class", [])):
            continue
        link = item.select_one("a.c-products__link") or item.select_one("a[href*='/detail/']")
        if not link or not link.get("href"):
            continue
        url = urljoin(BASE, link["href"].split("?")[0])
        m = ID_RE.search(url)
        listing_id = m.group(1) if m else url
        title = _text(item.select_one(".c-products__title"))
        info = _text(item.select_one(".c-products__info"))
        price_txt = _text(item.select_one(".c-products__price"))
        badges = [_text(b) for b in item.select(".badges__item, .badges-tip__item")]  # e.g. Zlevněno, Tip regionu
        raw = {"url": url, "title": title, "info": info, "price": price_txt, "badges": badges}
        rec = {
            "source": "idnes",
            "listing_id": listing_id,
            "offer_type": offer,
            "estate_type": estate,
            "title": title,
            "price": price_from_text(price_txt),
            "area_m2": area_from_text(title),
            "disposition": disposition_from_text(title),
            "locality": info,
            "lat": None,
            "lon": None,
            "url": url,
        }
        out.append((rec, raw))
    return out


def iter_listings(http, cfg: dict, max_pages: int | None = None):
    limit = min(cfg.get("max_pages", 300), max_pages or 10**9)
    for s in cfg["searches"]:
        seen_ids = set()
        for page in range(1, limit + 1):
            url = s["url"] if page == 1 else f"{s['url']}?page={page}"
            try:
                html = http.get(url).text
            except requests.HTTPError as e:
                if page > 1 and e.response is not None and e.response.status_code == 404:
                    break  # past the last page
                raise
            items = parse_page(html, s["offer"], s["estate"])
            if not items:
                if page == 1:
                    raise RuntimeError(f"idnes: 0 items parsed on first page of {s['url']} - layout changed?")
                break
            fresh = [(r, raw) for r, raw in items if r["listing_id"] not in seen_ids]
            if not fresh:  # site returned the last page again
                break
            for r, raw in fresh:
                seen_ids.add(r["listing_id"])
                yield r, raw
        log.info("idnes %s %s: %s listings", s["estate"], s["offer"], len(seen_ids))


fetch_detail = None
