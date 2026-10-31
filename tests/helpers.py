"""Shared fakes for offline tests – no test ever touches the real portals."""
import os
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class Resp:
    def __init__(self, data=None, text=""):
        self._d, self.text = data, text

    def json(self):
        return self._d


IDNES_HTML = """
<div class="c-products__item"><a class="c-products__link" href="/detail/prodej/byt/brno/abcdef0123456789abcdef01/">
  <h2 class="c-products__title">prodej bytu 3+kk 72 m²</h2></a>
  <p class="c-products__info">Brno - Žabovřesky</p><p class="c-products__price"><strong>7 990 000 Kč</strong></p></div>
<div class="c-products__item c-products__item--advertisment">reklama</div>
"""


def _sreality_estate(hid, title, price, city, part):
    return {"hash_id": hid, "advert_name": title, "price_summary_czk": price,
            "category_main_cb": {"value": 1}, "category_type_cb": {"value": 1}, "category_sub_cb": {"name": "2+kk"},
            "locality": {"city": city, "citypart": part, "region_id": 14, "gps_lat": 49.22, "gps_lon": 16.59}}


def fixture(name):
    return (Path(ROOT) / "tests" / "fixtures" / name).read_text(encoding="utf-8")


class FakeHttp:
    """Mimics the three portals: Sreality (3 listings over 2 pages), Bezrealitky (1), iDNES (1)."""

    def __init__(self, broken=()):
        self.broken = set(broken)

    def get(self, url, params=None, **kw):
        for b in self.broken:
            if b in url:
                raise ConnectionError(f"{b} is down")
        if "sreality" in url and params:
            estates = [_sreality_estate(101, "Prodej bytu 2+kk 54 m²", 5490000, "Brno", "Královo Pole")]
            if params["offset"] == 0:
                estates.append(_sreality_estate(999, "Prodej bytu 3+1 80 m²", 0, "Kuřim", None))
            else:
                estates[0]["hash_id"] = 102
            return Resp({"pagination": {"total": 3}, "results": estates})
        if "sreality" in url:
            return Resp({"result": {"building_condition": {"name": "Novostavba", "value": 6}}})
        if "idnes" in url:
            return Resp(text="<div></div>" if "page=" in url else IDNES_HTML)
        raise AssertionError(f"unexpected URL {url}")

    def post_json(self, url, payload, **kw):
        if "bezrealitky" in self.broken:
            raise ConnectionError("bezrealitky is down")
        items = [] if payload["variables"]["offset"] else [{
            "id": "1", "uri": "x-1", "offerType": "PRODEJ", "estateType": "BYT", "disposition": "DISP_2_KK",
            "surface": 55, "price": 6000000, "address": "Brno", "gps": {"lat": 49.2, "lng": 16.6}}]
        return {"data": {"listAdverts": {"totalCount": 1, "list": items}}}


def listings_cfg(db_path):
    return {"db_path": db_path, "user_agent": "test", "listings": {
        "sreality": {"region_id": 14, "categories": [{"main": 1, "type": 1}], "fetch_details_for_new": True},
        "bezrealitky": {"region_osm_ids": ["R1"], "offer_types": ["PRODEJ"], "estate_types": ["BYT"]},
        "idnes": {"searches": [{"url": "https://reality.idnes.cz/s/prodej/byty/x/", "offer": "prodej", "estate": "byt"}]},
    }}
