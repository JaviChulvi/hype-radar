import unittest
from datetime import UTC, datetime
from decimal import Decimal

from app.domain.markets import MarketIdentity
from app.domain.observations import Metric
from app.ingestion import HyperliquidNormalizer


class HyperliquidNormalizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.normalizer = HyperliquidNormalizer()
        self.market = MarketIdentity("mainnet", "", "HYPE")
        self.at = datetime(2026, 10, 2, tzinfo=UTC)

    def test_asset_context_keeps_economic_values_as_decimal(self):
        observation = self.normalizer.asset_context(
            self.market,
            {
                "markPx": "42.5",
                "oraclePx": "42.4",
                "openInterest": "1000000.25",
                "funding": "0.0001",
            },
            self.at,
            self.at,
        )

        self.assertEqual(observation.values[Metric.MARK_PRICE], Decimal("42.5"))
        self.assertEqual(observation.values[Metric.OPEN_INTEREST], Decimal("1000000.25"))
        self.assertEqual(observation.channel, "activeAssetCtx")

    def test_order_book_calculates_mid_and_usd_depth(self):
        observation = self.normalizer.order_book(
            self.market,
            [
                [{"px": "100", "sz": "2"}, {"px": "99", "sz": "3"}],
                [{"px": "101", "sz": "4"}],
            ],
            self.at,
            self.at,
        )

        self.assertEqual(observation.values[Metric.MID_PRICE], Decimal("100.5"))
        self.assertEqual(observation.values[Metric.BID_DEPTH], Decimal("497"))
        self.assertEqual(observation.values[Metric.ASK_DEPTH], Decimal("404"))

    def test_non_finite_values_are_rejected(self):
        with self.assertRaises(ValueError):
            self.normalizer.asset_context(
                self.market,
                {"markPx": "NaN"},
                self.at,
                self.at,
            )


if __name__ == "__main__":
    unittest.main()
