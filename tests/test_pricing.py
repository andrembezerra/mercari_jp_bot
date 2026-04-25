import unittest

from src.pricing import convert_price_to_yen


class PricingTests(unittest.TestCase):
    def test_converts_usd_to_yen(self):
        formatted, numeric = convert_price_to_yen("$100", 145.0)

        self.assertEqual(formatted, "¥14.500")
        self.assertEqual(numeric, 14500)

    def test_parses_yen_text_without_symbol(self):
        formatted, numeric = convert_price_to_yen("9,800 yen", 145.0)

        self.assertEqual(formatted, "¥9.800")
        self.assertEqual(numeric, 9800)

    def test_returns_none_for_unparseable_text(self):
        formatted, numeric = convert_price_to_yen("price unavailable", 145.0)

        self.assertIsNone(formatted)
        self.assertIsNone(numeric)


if __name__ == "__main__":
    unittest.main()
