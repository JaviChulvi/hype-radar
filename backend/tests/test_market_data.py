import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.application.market_data import MarketDataService, MarketUnavailable, SlowConsumer
from app.domain.markets import resolve_market
from app.ingestion.client import HyperliquidClient
from feed import SharedFeed
from main import app


def context():
    return {
        "markPx": "110",
        "oraclePx": "109",
        "openInterest": "2500",
        "funding": "0.00001",
        "prevDayPx": "100",
        "dayNtlVlm": "1000000",
    }


def book(coin="BTC", at=None):
    return {
        "coin": coin,
        "time": int((at or datetime.now(UTC)).timestamp() * 1000),
        "levels": [[{"px": "109", "sz": "2"}], [{"px": "111", "sz": "3"}]],
    }


class MarketDataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = HyperliquidClient()
        self.markets = MarketDataService(self.client)

    async def asyncTearDown(self):
        await self.markets.close()

    async def test_concurrent_cold_reads_share_requests_and_return_decimal_provenance(self):
        with (
            patch.object(self.client, "contexts", AsyncMock(return_value={"BTC": context()})) as contexts,
            patch.object(self.client, "book", AsyncMock(return_value=book())) as books,
        ):
            results = await asyncio.gather(*(self.markets.get_snapshot("BTC") for _ in range(5)))
            contexts.assert_awaited_once_with("")
            books.assert_awaited_once_with("BTC", None)
        result = results[0]
        self.assertEqual(result.fields["mark_price"].value, Decimal("110"))
        self.assertEqual(result.fields["change_24h_percent"].value, Decimal("10"))
        self.assertEqual(result.fields["bid_depth"].value, Decimal("218"))
        self.assertEqual(result.fields["mid_price"].value, Decimal("110"))
        self.assertEqual(result.fields["mark_price"].timestamp_basis, "reception")
        self.assertIsNone(result.fields["mark_price"].source_at)
        self.assertEqual(result.fields["bid_price"].timestamp_basis, "exchange")
        self.assertEqual(result.model_dump(mode="json")["fields"]["mark_price"]["value"], "110")
        self.assertEqual(result.depth.bid_levels, 1)
        self.assertIsNone(result.depth.precision)
        self.assertFalse(result.depth.fast)
        self.assertEqual(self.markets._tasks, {})

    async def test_fresh_book_does_not_refresh_old_oi_and_missing_values_stay_missing(self):
        _, ctx = self.markets._feed("BTC", "context")
        old = datetime.now(UTC) - timedelta(seconds=60)
        ctx.apply(context(), old)
        _, depth = self.markets._feed("BTC", "book")
        depth.apply(book(), datetime.now(UTC))
        with patch.object(self.client, "contexts", AsyncMock(side_effect=httpx.ConnectError("offline"))):
            result = await self.markets.get_snapshot("BTC")
        self.assertEqual(result.fields["open_interest"].status, "stale")
        self.assertEqual(result.fields["open_interest"].received_at, old)
        self.assertEqual(result.fields["bid_price"].status, "fresh")
        self.assertEqual(result.fields["change_24h_percent"].status, "stale")
        ctx.apply({"markPx": "111"}, datetime.now(UTC))
        result = await self.markets.get_snapshot("BTC")
        self.assertIsNone(result.fields["open_interest"].value)
        self.assertEqual(result.fields["open_interest"].status, "missing")
        self.assertIsNone(result.fields["change_24h_percent"].value)

    async def test_partial_snapshot_and_total_outage(self):
        with (
            patch.object(self.client, "contexts", AsyncMock(return_value={"BTC": context()})),
            patch.object(self.client, "book", AsyncMock(side_effect=httpx.ConnectError("offline"))),
        ):
            snapshot = await self.markets.get_snapshot("BTC")
            self.assertEqual(snapshot.fields["bid_price"].status, "missing")
            self.assertEqual(snapshot.fields["mark_price"].status, "fresh")
        with (
            patch.object(self.client, "contexts", AsyncMock(side_effect=httpx.ConnectError("offline"))),
            patch.object(self.client, "book", AsyncMock(side_effect=httpx.ConnectError("offline"))),
        ):
            with self.assertRaises(MarketUnavailable):
                await self.markets.get_snapshot("SP500")

    async def test_rest_context_cannot_overwrite_a_live_update_arriving_in_flight(self):
        async def contexts(dex):
            _, ctx = self.markets._feed("BTC", "context")
            ctx.apply({**context(), "markPx": "120"}, datetime.now(UTC))
            return {"BTC": context()}

        with (
            patch.object(self.client, "contexts", contexts),
            patch.object(self.client, "book", AsyncMock(return_value=book())),
        ):
            snapshot = await self.markets.get_snapshot("BTC")
        self.assertEqual(snapshot.fields["mark_price"].value, Decimal("120"))
        self.assertEqual(snapshot.fields["mark_price"].channel, "activeAssetCtx")

    async def test_book_variants_do_not_replace_canonical_depth(self):
        raw = book()
        raw["levels"][0] = [{"px": str(109 - i), "sz": "1"} for i in range(20)]
        raw["levels"][1] = [{"px": str(111 + i), "sz": "1"} for i in range(20)]
        with (
            patch.object(self.client, "book", AsyncMock(return_value=raw)),
            patch.object(self.client, "contexts", AsyncMock(return_value={"BTC": context()})),
        ):
            grouped = await self.markets.get_order_book("BTC", precision=4)
            snapshot = await self.markets.get_snapshot("BTC")
        self.assertEqual(len(grouped.levels[0]), 5)
        self.assertEqual(snapshot.depth.bid_levels, 20)
        self.assertEqual(snapshot.fields["bid_depth"].value, sum(Decimal(109 - i) for i in range(20)))

    async def test_trade_read_uses_temporary_subscription_and_cancels_it(self):
        async def run(feed):
            feed.apply([{"coin": "BTC", "px": "100", "sz": "1", "time": 1, "side": "B", "tid": 1}])
            await asyncio.Event().wait()

        with patch("feed.TradeFeed.run", run):
            result = await self.markets.get_recent_trades("BTC")
        self.assertEqual(result[0].tid, 1)
        self.assertEqual(self.markets._tasks, {})
        self.assertEqual(self.markets._owners, {})

    async def test_cancelled_subscription_releases_all_ownership(self):
        ready = asyncio.Event()

        async def idle(*args):
            await asyncio.Event().wait()

        async def consumer():
            async with self.markets.subscribe("BTC", ("book", "trades")):
                ready.set()
                await asyncio.Event().wait()

        with patch.object(SharedFeed, "run", idle):
            task = asyncio.create_task(consumer())
            await ready.wait()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.markets._tasks, {})
        self.assertEqual(self.markets._owners, {})

    async def test_deadline_returns_partial_data_and_cancels_upstream_work(self):
        stopped = asyncio.Event()
        timeout = asyncio.timeout

        async def blocked_book(*args):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        with (
            patch.object(self.client, "contexts", AsyncMock(return_value={"BTC": context()})),
            patch.object(self.client, "book", blocked_book),
            patch("app.application.market_data.asyncio.timeout", side_effect=lambda seconds: timeout(0.03)) as deadline,
        ):
            result = await self.markets.get_snapshot("BTC")
        deadline.assert_called_once_with(10)
        self.assertEqual(result.fields["mark_price"].status, "fresh")
        self.assertEqual(result.fields["bid_price"].status, "missing")
        self.assertTrue(stopped.is_set())

    async def test_closing_service_wakes_subscribers(self):
        async def idle(*args):
            await asyncio.Event().wait()

        with patch.object(SharedFeed, "run", idle):
            async with self.markets.subscribe("BTC", ("book",)) as updates:
                await anext(updates)
                await self.markets.close()
                with self.assertRaises(SlowConsumer):
                    await anext(updates)
        self.assertEqual(self.markets._owners, {})

    async def test_identity_and_options_are_validated_before_io(self):
        self.assertEqual(self.markets.resolve("SP500"), self.markets.resolve("xyz:SP500"))
        self.assertEqual(len(await self.markets.list_markets()), 6)
        with self.assertRaises(KeyError):
            self.markets.resolve(replace(resolve_market("BTC"), network="testnet"))
        with self.assertRaises(KeyError):
            await self.markets.get_snapshot("DOGE")
        for kwargs in ({"interval": "2m"}, {"limit": 201}):
            with self.assertRaises(ValueError):
                await self.markets.get_candles("BTC", **kwargs)
        with self.assertRaises(ValueError):
            await self.markets.get_order_book("BTC", precision=6)


class MarketDataHTTPTests(unittest.TestCase):
    def setUp(self):
        # HTTP unit tests isolate the database read; the real evaluator lifecycle still runs.
        self.load_rules = self.enterContext(
            patch("main.EvaluationWorker._load_rules_and_warm_up", new=AsyncMock(return_value=[]))
        )

    def test_http_reads_match_service_and_validate_requests(self):
        with TestClient(app) as client:
            with (
                patch.object(app.state.markets.client, "contexts", AsyncMock(return_value={"BTC": context()})),
                patch.object(app.state.markets.client, "book", AsyncMock(return_value=book())),
            ):
                response = client.get("/api/market-data/BTC/snapshot")
                self.assertEqual(response.status_code, 200)
                direct = client.portal.call(app.state.markets.get_snapshot, "BTC")
                self.assertEqual(response.json()["fields"], direct.model_dump(mode="json")["fields"])
                self.assertEqual(client.get("/api/market-data/BTC/book").json()["levels"][0][0]["px"], "109")
            with patch.object(app.state.markets.client, "book", AsyncMock(return_value=book())) as upstream:
                self.assertEqual(client.get("/api/market-data/BTC/book?precision=4").status_code, 200)
                upstream.assert_awaited_once_with("BTC", 4)
                self.assertEqual(client.get("/api/market-data/BTC/book?precision=full&fast=false").status_code, 200)
            self.assertEqual(len(client.get("/api/market-data").json()), 6)
            self.assertEqual(client.get("/api/market-data/DOGE/snapshot").status_code, 404)
            for suffix in ("candles?limit=201", "candles?interval=2m", "book?precision=6", "trades?limit=0"):
                self.assertEqual(client.get(f"/api/market-data/BTC/{suffix}").status_code, 422)
            self.assertEqual(client.get("/health").json()["evaluator"]["status"], "idle")
            self.load_rules.assert_awaited_once()
            with (
                patch.object(
                    app.state.markets.client, "contexts", AsyncMock(side_effect=httpx.ConnectError("offline"))
                ),
                patch.object(app.state.markets.client, "book", AsyncMock(side_effect=httpx.ConnectError("offline"))),
            ):
                self.assertEqual(client.get("/api/market-data/SP500/snapshot").status_code, 503)

    def test_alert_startup_failure_is_fatal_and_runtime_failure_is_reported(self):
        with patch("main.EvaluationWorker") as worker:
            worker.return_value.start = AsyncMock(side_effect=RuntimeError("database unavailable"))
            worker.return_value.close = AsyncMock()
            with self.assertRaisesRegex(RuntimeError, "database unavailable"), TestClient(app):
                pass
            worker.return_value.close.assert_awaited_once()
        with patch("main.EvaluationWorker") as worker:
            worker.return_value.start = AsyncMock()
            worker.return_value.close = AsyncMock()
            worker.return_value.run = AsyncMock(side_effect=RuntimeError("lost continuity"))
            worker.return_value.rules = [object()]
            with TestClient(app) as client:
                response = client.get("/health")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["evaluator"]["status"], "failed")
            worker.return_value.close.assert_awaited_once()
