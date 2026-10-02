import asyncio
import unittest
from datetime import UTC, datetime
from decimal import Decimal

from app.domain.markets import MarketIdentity
from app.domain.observations import Metric
from app.ingestion import HyperliquidObservationFeed


class HyperliquidObservationFeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.feed = HyperliquidObservationFeed(
            MarketIdentity("mainnet", "", "HYPE"),
            lambda observation: asyncio.sleep(0),
        )
        self.received_at = datetime(2026, 10, 2, 10, tzinfo=UTC)

    def test_active_asset_context_uses_reception_timestamp(self):
        observation = self.feed.decode(
            {
                "channel": "activeAssetCtx",
                "data": {
                    "coin": "HYPE",
                    "ctx": {
                        "markPx": "42",
                        "oraclePx": "41.9",
                        "openInterest": "1000",
                        "funding": "0.0001",
                    },
                },
            },
            self.received_at,
        )

        self.assertEqual(observation.observed_at, self.received_at)
        self.assertEqual(observation.values[Metric.OPEN_INTEREST], Decimal("1000"))

    def test_book_keeps_source_timestamp_and_recovery_gap(self):
        observation = self.feed.decode(
            {
                "channel": "l2Book",
                "data": {
                    "coin": "HYPE",
                    "time": 1790935199000,
                    "levels": [[{"px": "100", "sz": "2"}], [{"px": "101", "sz": "3"}]],
                },
            },
            self.received_at,
            gap=True,
        )

        self.assertTrue(observation.gap)
        self.assertEqual(observation.values[Metric.MID_PRICE], Decimal("100.5"))
        self.assertNotEqual(observation.observed_at, observation.received_at)

    def test_wrong_market_is_rejected(self):
        with self.assertRaises(ValueError):
            self.feed.decode(
                {"channel": "activeAssetCtx", "data": {"coin": "BTC", "ctx": {}}},
                self.received_at,
            )


if __name__ == "__main__":
    unittest.main()
