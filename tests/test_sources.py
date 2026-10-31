import json
import unittest

import requests

from helpers import IDNES_HTML, Resp, fixture
from collector.sources import bezrealitky, idnes, sreality


class SrealityTest(unittest.TestCase):
    """tests/fixtures/sreality_*.json are trimmed real responses of /api/v1/estates."""

    def test_parse_real_search_result(self):
        e = json.loads(fixture("sreality_search.json"))["results"][0]
        r = sreality.parse(e, 1, 1)
        self.assertEqual((r["listing_id"], r["offer_type"], r["estate_type"]), ("567742540", "prodej", "byt"))
        self.assertEqual((r["price"], r["area_m2"], r["disposition"]), (13715000, 81.0, "2+kk"))
        self.assertEqual(r["locality"], "Komárovská, Brno - Komárov")
        self.assertAlmostEqual(r["lat"], 49.18, places=2)
        self.assertEqual(r["url"], "https://www.sreality.cz/detail/prodej/byt/2+kk/x/567742540")

    def test_house_url_slug_and_price_on_request(self):
        r = sreality.parse({"hash_id": 6, "advert_name": "Prodej rodinného domu 120 m²", "price_czk": 0,
                            "price_summary_czk": 0, "category_sub_cb": {"name": "Rodinný"}}, 2, 1)
        self.assertIsNone(r["price"])
        self.assertIsNone(r["disposition"])
        self.assertEqual(r["url"], "https://www.sreality.cz/detail/prodej/dum/rodinny/x/6")

    def test_list_payload_drops_images(self):
        class H:
            def get(self, url, params=None, **kw):
                return Resp(json.loads(fixture("sreality_search.json")))
        [(rec, raw)] = list(sreality.iter_listings(H(), {"region_id": 14, "categories": [{"main": 1, "type": 1}]}))
        self.assertNotIn("advert_images_all", raw)
        self.assertEqual(raw["hash_id"], 567742540)

    def test_detail_drops_broker_contacts(self):
        class H:
            def get(self, url, **kw):
                return Resp(json.loads(fixture("sreality_detail.json")))
        d = sreality.fetch_detail(H(), "567742540")
        self.assertNotIn("user", d)
        self.assertNotIn("labels", d)
        self.assertEqual(d["building_condition"]["name"], "Novostavba")

    def test_unexpected_response_fails_loudly(self):
        class H:
            def get(self, url, params=None, **kw):
                return Resp({"status_code": 401, "status_message": "Unauthorized"})
        with self.assertRaisesRegex(RuntimeError, "unexpected response"):
            list(sreality.iter_listings(H(), {"region_id": 14, "categories": [{"main": 1, "type": 1}]}))


class BezrealitkyTest(unittest.TestCase):
    def test_parse_real_item(self):
        a = json.loads(fixture("bezrealitky_list.json"))["data"]["listAdverts"]["list"][0]
        r = bezrealitky.parse(a)
        self.assertEqual(r["source"], "bezrealitky")
        self.assertTrue(r["title"].startswith(("Prodej", "Pronájem")))
        self.assertGreater(r["price"], 0)
        self.assertGreater(r["area_m2"], 0)
        self.assertIsNotNone(r["lat"])
        self.assertTrue(r["url"].startswith("https://www.bezrealitky.cz/nemovitosti-byty-domy/"))

    def test_parse_disposition_codes(self):
        for code, expected in [("DISP_2_KK", "2+kk"), ("DISP_3_1", "3+1"), (None, None)]:
            r = bezrealitky.parse({"id": 1, "offerType": "PRONAJEM", "estateType": "DUM", "disposition": code})
            self.assertEqual(r["disposition"], expected)
            self.assertEqual((r["offer_type"], r["estate_type"]), ("pronajem", "dum"))

    def test_foreign_currency_price_is_none(self):
        self.assertIsNone(bezrealitky.parse({"id": 1, "price": 900, "currency": "EUR"})["price"])

    def test_graphql_errors_are_raised(self):
        class H:
            def post_json(self, url, payload, **kw):
                return {"errors": [{"message": "Cannot query field 'surfaceLand'"}]}
        cfg = {"region_osm_ids": ["R1"], "offer_types": ["PRODEJ"], "estate_types": ["BYT"]}
        with self.assertRaisesRegex(RuntimeError, "surfaceLand"):
            list(bezrealitky.iter_listings(H(), cfg))


class IdnesTest(unittest.TestCase):
    def test_parse_real_page(self):
        items = idnes.parse_page(fixture("idnes_page.html"), "prodej", "byt")
        self.assertEqual(len(items), 2, "the ad block is skipped")
        rec, raw = items[0]
        self.assertEqual((rec["price"], rec["area_m2"], rec["disposition"]), (6290000, 63.0, "3+1"))
        self.assertEqual(rec["locality"], "Brněnská, Kuřim, okres Brno-venkov")
        self.assertEqual(rec["listing_id"], "6a79e618eb57444e470e460f")
        self.assertIn("Tip regionu", raw["badges"])

    def test_layout_change_fails_loudly(self):
        class H:
            def get(self, url, **kw):
                return Resp(text="<html>jiný vzhled</html>")
        with self.assertRaises(RuntimeError):
            list(idnes.iter_listings(H(), {"searches": [{"url": "u", "offer": "prodej", "estate": "byt"}]}))

    def test_404_after_last_page_ends_search(self):
        class H:
            def get(self, url, **kw):
                if "page=" in url:
                    resp = requests.Response()
                    resp.status_code = 404
                    raise requests.HTTPError(response=resp)
                return Resp(text=IDNES_HTML)
        items = list(idnes.iter_listings(H(), {"searches": [{"url": "u", "offer": "prodej", "estate": "byt"}]}))
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
