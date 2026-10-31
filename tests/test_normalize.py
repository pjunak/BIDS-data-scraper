import unittest

import helpers  # noqa: F401  (sets sys.path)
from collector.normalize import area_from_text, disposition_from_text, price_from_text


class AreaTest(unittest.TestCase):
    def test_disposition_digit_is_not_part_of_area(self):
        self.assertEqual(area_from_text("Prodej bytu 3+1 80 m²"), 80.0)

    def test_first_area_and_thousands(self):
        self.assertEqual(area_from_text("Prodej domu 150 m², pozemek 1 200 m²"), 150.0)
        self.assertEqual(area_from_text("pozemek 1 200 m²"), 1200.0)

    def test_decimal_and_m2_variants(self):
        self.assertEqual(area_from_text("Pronájem bytu 1+kk 25,5 m²"), 25.5)
        self.assertEqual(area_from_text("Prodej bytu 2+kk 54 m2"), 54.0)
        self.assertEqual(area_from_text("Prodej bytu 3+kk 55 m²"), 55.0)  # Sreality uses a no-break space

    def test_missing(self):
        self.assertIsNone(area_from_text("bez plochy"))
        self.assertIsNone(area_from_text(None))


class DispositionPriceTest(unittest.TestCase):
    def test_disposition(self):
        self.assertEqual(disposition_from_text("Prodej bytu 2+kk 54 m²"), "2+kk")
        self.assertEqual(disposition_from_text("Prodej bytu 4 + 1"), "4+1")
        self.assertIsNone(disposition_from_text("Prodej domu"))

    def test_price(self):
        self.assertEqual(price_from_text("5 490 000 Kč"), 5490000)
        self.assertEqual(price_from_text("16 500 Kč/měsíc"), 16500)
        self.assertIsNone(price_from_text("Cena na vyžádání"))


if __name__ == "__main__":
    unittest.main()
