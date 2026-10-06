import asyncio
import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.application.market_data import MarketDataService, SlowConsumer
from app.domain.markets import resolve_market
from app.domain.observations import MarketObservation, Metric
from app.domain.rules import ComparisonOperator, QualityPolicy, QualitySettings, ThresholdPredicate
from app.engine import ObservationWindowStore, RuleEngine
from app.engine.quality import QualityEvaluator
from app.ingestion.client import HyperliquidClient
from app.persistence.serialization import rule_from_dict
from app.workers.evaluator import EvaluationWorker
from feed import ContextFeed, OrderBookFeed, SharedFeed


class SharedObservationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = HyperliquidClient()
        self.markets = MarketDataService(self.client)
        self.market = resolve_market("HYPE")
        self.at = datetime.now(UTC)

    async def asyncTearDown(self):
        await self.markets.close()

    async def test_context_reception_timestamp_and_book_source_timestamp(self):
        context = ContextFeed(self.client, self.market)
        context.apply({"markPx": "42", "openInterest": "1000", "funding": "0.0001"}, self.at)
        self.assertEqual(context.observation.observed_at, self.at)
        self.assertEqual(context.observation.values[Metric.OPEN_INTEREST], Decimal("1000"))
        book = OrderBookFeed(self.client, self.market, None, False)
        book.gap = True
        book.apply(
            {"coin": "HYPE", "time": 1790935199000, "levels": [[{"px": "100", "sz": "2"}], [{"px": "101", "sz": "3"}]]},
            self.at,
        )
        self.assertTrue(book.observation.gap)
        self.assertNotEqual(book.observation.observed_at, self.at)
        self.assertEqual(book.observation.values[Metric.MID_PRICE], Decimal("100.5"))
        with self.assertRaises(ValueError):
            book.apply({"coin": "BTC", "time": 1, "levels": [[], []]}, self.at)

    async def test_monitoring_distinguishes_unavailable_warmup_and_stale_data(self):
        worker = EvaluationWorker(self.markets)
        fixture = Path(__file__).parent / "fixtures/hype_breakout.json"
        rule = replace(
            rule_from_dict(json.loads(fixture.read_text())["rule"]),
            predicates=(
                ThresholdPredicate(
                    "price", Metric.MARK_PRICE, ComparisonOperator.GREATER_THAN, Decimal("100"), timedelta(seconds=30)
                ),
            ),
            quality=QualitySettings(),
        )
        worker.rules = [rule]
        task = asyncio.create_task(asyncio.Event().wait())
        try:
            self.assertEqual(worker.monitoring(rule.rule_id), "evaluator_unavailable")
            worker._running = True
            worker._feeds[rule.market] = task
            self.assertEqual(worker.monitoring(rule.rule_id), "warming_up")
            worker._windows.add(
                MarketObservation(
                    rule.market, "fixture", "context", self.at, self.at, {Metric.MARK_PRICE: Decimal("101")}
                )
            )
            self.assertEqual(worker.monitoring(rule.rule_id), "monitoring")
            with patch("app.workers.evaluator.datetime") as clock:
                clock.now.return_value = self.at + timedelta(seconds=31)
                self.assertEqual(worker.monitoring(rule.rule_id), "stale")
            worker._windows.add(MarketObservation(rule.market, "fixture", "context", self.at, self.at, {}, gap=True))
            self.assertEqual(worker.monitoring(rule.rule_id), "stale")
            worker._windows = ObservationWindowStore()
            worker._engine = RuleEngine(worker._windows)
            worker.rules = []
            self.assertEqual(worker.monitoring(rule.rule_id), "inactive")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await worker.close()

    async def test_evaluator_ownership_survives_browsers_and_variants_share_trades(self):
        async def idle(*args):
            await asyncio.Event().wait()

        with patch.object(SharedFeed, "run", idle), patch.object(self.client, "contexts", AsyncMock(return_value={})):
            async with self.markets.subscribe("HYPE"):
                async with self.markets.subscribe("HYPE"):
                    self.assertEqual(len(self.markets._tasks), 2)
                    self.assertEqual(self.markets._owners["book:HYPE:full:normal"], 2)
                self.assertEqual(self.markets._owners["book:HYPE:full:normal"], 1)
                async with self.markets.subscribe("HYPE", ("book", "trades"), precision=5, fast=True):
                    async with self.markets.subscribe("HYPE", ("book", "trades"), precision=4, fast=True):
                        self.assertEqual(len(self.markets._tasks), 5)
                        self.assertEqual(self.markets._owners["trades:HYPE"], 2)
                self.assertEqual(len(self.markets._tasks), 2)
            self.assertEqual(self.markets._tasks, {})

    async def test_older_book_preserves_latest_state_and_blocks_alert_quality(self):
        fixture = Path(__file__).parent / "fixtures/hype_breakout.json"
        rule = replace(
            rule_from_dict(json.loads(fixture.read_text())["rule"]),
            quality=QualitySettings(),
            quality_policy=QualityPolicy.BLOCK,
        )
        windows = ObservationWindowStore()
        feed = OrderBookFeed(self.client, self.market, None, False)
        queue = asyncio.Queue()
        feed.clients.add(queue)
        raw = {
            "coin": "HYPE",
            "time": int(self.at.timestamp() * 1000),
            "levels": [[{"px": "100", "sz": "2"}], [{"px": "101", "sz": "3"}]],
        }
        feed.apply(raw, self.at)
        latest = queue.get_nowait()
        windows.add(latest.observation)
        received = self.at + timedelta(seconds=1)
        feed.apply({**raw, "time": raw["time"] - 1000, "levels": [[], []]}, received)
        update = await asyncio.wait_for(queue.get(), 0.1)
        windows.add(update.observation)

        self.assertIs(update.data, latest.data)
        self.assertIs(feed.observation, latest.observation)
        self.assertEqual(feed.received_at, self.at)
        self.assertEqual(update.observation.received_at, received)
        self.assertTrue(update.observation.out_of_order)
        quality, checks = QualityEvaluator(windows).evaluate(rule, received)
        self.assertEqual(quality.value, "blocked")
        self.assertIn("OUT_OF_ORDER", [check.reason_code for check in checks])

    async def test_slow_evaluator_stops_and_releases_subscriptions(self):
        worker = EvaluationWorker(self.markets)
        fixture = Path(__file__).parent / "fixtures/hype_breakout.json"
        worker.rules = [rule_from_dict(json.loads(fixture.read_text())["rule"])]
        entered = asyncio.Event()
        release = asyncio.Event()
        ready = asyncio.Event()

        async def process(*args):
            entered.set()
            await release.wait()
            return SimpleNamespace(outcomes=())

        async def idle(feed):
            if isinstance(feed, ContextFeed):
                feed.apply({"markPx": "101"}, datetime.now(UTC))
                ready.set()
            await asyncio.Event().wait()

        with (
            patch.object(SharedFeed, "run", idle),
            patch.object(self.client, "contexts", AsyncMock(return_value={})),
            patch.object(worker._service, "process", process),
        ):
            task = asyncio.create_task(worker.run())
            try:
                await asyncio.wait_for(ready.wait(), 1)
                await asyncio.wait_for(entered.wait(), 1)
                feed = self.markets._feeds["context:HYPE"]
                for _ in range(40):
                    feed.apply({"markPx": "101"}, datetime.now(UTC))
                release.set()
                with self.assertRaises(SlowConsumer):
                    await asyncio.wait_for(task, 1)
                self.assertEqual(self.markets._tasks, {})
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await worker.close()
