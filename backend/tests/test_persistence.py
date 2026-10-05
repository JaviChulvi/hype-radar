import asyncio
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from sqlalchemy import delete, func, select

from app.application.market_data import MarketDataService
from app.config import Settings
from app.domain.markets import MarketIdentity
from app.domain.observations import MarketObservation, Metric
from app.domain.rules import (
    ComparisonOperator,
    DeliveryTarget,
    RuleVersion,
    ThresholdPredicate,
)
from app.engine import ObservationWindowStore, RuleEngine
from app.ingestion.client import HyperliquidClient
from app.persistence import SqlAlchemyUnitOfWork, create_engine, create_session_factory
from app.persistence.models import (
    AlertEventModel,
    AlertEvidenceModel,
    AlertRuleModel,
    AlertRuleVersionModel,
    DeliveryModel,
    MarketModel,
    MarketSampleModel,
    NotificationOutboxModel,
    RuleRuntimeModel,
)
from app.persistence.serialization import rule_to_dict
from app.workers.evaluator import EvaluationWorker
from feed import ContextFeed, SharedFeed
from main import app

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL is not configured")
class PostgreSQLPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database_engine = create_engine(Settings(database_url=TEST_DATABASE_URL))
        self.sessions = create_session_factory(self.database_engine)
        self.market_ids = set()
        self.rule_ids = set()
        self.event_ids = set()

    async def asyncTearDown(self):
        async with self.sessions() as session, session.begin():
            if self.event_ids:
                await session.execute(
                    delete(NotificationOutboxModel).where(NotificationOutboxModel.event_id.in_(self.event_ids))
                )
                await session.execute(delete(DeliveryModel).where(DeliveryModel.event_id.in_(self.event_ids)))
                await session.execute(delete(AlertEvidenceModel).where(AlertEvidenceModel.event_id.in_(self.event_ids)))
                await session.execute(delete(AlertEventModel).where(AlertEventModel.id.in_(self.event_ids)))
            if self.rule_ids:
                await session.execute(delete(AlertRuleModel).where(AlertRuleModel.id.in_(self.rule_ids)))
            if self.market_ids:
                await session.execute(delete(MarketSampleModel).where(MarketSampleModel.market_id.in_(self.market_ids)))
                await session.execute(delete(MarketModel).where(MarketModel.id.in_(self.market_ids)))
        await self.database_engine.dispose()

    async def test_shared_market_service_drives_durable_evaluation(self):
        now = datetime.now(UTC)
        market = MarketIdentity("mainnet", "", "BTC")
        rule = RuleVersion(
            id=uuid4(),
            rule_id=uuid4(),
            owner_id=uuid4(),
            version=1,
            name="Shared service integration",
            market=market,
            predicates=(
                ThresholdPredicate(
                    "price", Metric.MARK_PRICE, ComparisonOperator.GREATER_THAN, Decimal("100"), timedelta(minutes=1)
                ),
            ),
            deliveries=(DeliveryTarget("web", "integration-user"),),
        )
        async with SqlAlchemyUnitOfWork(self.sessions) as unit_of_work:
            stored_market = await unit_of_work.markets.ensure(market)
            self.market_ids.add(stored_market.id)
            self.rule_ids.add(rule.rule_id)
            await unit_of_work.rules.add_version(rule, stored_market.id, now)

        markets = MarketDataService(HyperliquidClient())
        with patch("app.workers.evaluator.create_engine", return_value=self.database_engine):
            worker = EvaluationWorker(markets)
        await worker.start()
        ready = asyncio.Event()

        async def feed_run(feed):
            if isinstance(feed, ContextFeed):
                feed.apply({"markPx": "101", "openInterest": "1000"}, datetime.now(UTC))
                ready.set()
            await asyncio.Event().wait()

        with (
            patch.object(SharedFeed, "run", feed_run),
            patch.object(markets.client, "contexts", AsyncMock(return_value={})),
        ):
            task = asyncio.create_task(worker.run())
            try:
                await asyncio.wait_for(ready.wait(), 2)
                # A browser joining and leaving the same book cannot stop evaluation.
                async with markets.subscribe("BTC", ("book",)):
                    self.assertEqual(markets._owners["book:BTC:full:normal"], 2)
                self.assertEqual(markets._owners["book:BTC:full:normal"], 1)
                async with asyncio.timeout(5):
                    while True:
                        async with self.sessions() as session:
                            event = await session.scalar(
                                select(AlertEventModel).where(
                                    AlertEventModel.rule_version_id == rule.id,
                                    AlertEventModel.status == "confirmed",
                                )
                            )
                            if event is not None:
                                self.event_ids.add(event.id)
                                self.assertIsNotNone(
                                    await session.scalar(
                                        select(AlertEvidenceModel).where(AlertEvidenceModel.event_id == event.id)
                                    )
                                )
                                self.assertIsNotNone(
                                    await session.scalar(
                                        select(NotificationOutboxModel).where(
                                            NotificationOutboxModel.event_id == event.id
                                        )
                                    )
                                )
                                self.assertTrue((await session.get(RuleRuntimeModel, rule.id)).episode_triggered)
                                self.assertIsNotNone(
                                    await session.scalar(
                                        select(MarketSampleModel).where(MarketSampleModel.market_id == stored_market.id)
                                    )
                                )
                                break
                        await asyncio.sleep(0.02)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await markets.close()
        self.assertEqual(markets._tasks, {})

    async def test_event_evidence_outbox_and_runtime_are_committed_atomically(self):
        now = datetime.now(UTC)
        market = MarketIdentity("mainnet", "", "BTC")
        rule = RuleVersion(
            id=uuid4(),
            rule_id=uuid4(),
            owner_id=uuid4(),
            version=1,
            name="Integration threshold",
            market=market,
            predicates=(
                ThresholdPredicate(
                    id="price",
                    metric=Metric.MARK_PRICE,
                    operator=ComparisonOperator.GREATER_THAN,
                    threshold=Decimal("100"),
                    max_age=timedelta(minutes=1),
                ),
            ),
            deliveries=(DeliveryTarget("web", "integration-user"),),
        )
        observation = MarketObservation(
            market=market,
            source="integration",
            channel="fixture",
            observed_at=now,
            received_at=now,
            values={Metric.MARK_PRICE: Decimal("101")},
        )

        async with SqlAlchemyUnitOfWork(self.sessions) as unit_of_work:
            market_model = await unit_of_work.markets.ensure(market)
            self.market_ids.add(market_model.id)
            self.rule_ids.add(rule.rule_id)
            await unit_of_work.rules.add_version(rule, market_model.id, now)
            self.assertTrue(await unit_of_work.samples.add(market_model.id, observation))

        windows = ObservationWindowStore()
        windows.add(observation)
        engine = RuleEngine(windows)
        async with SqlAlchemyUnitOfWork(self.sessions) as unit_of_work:
            runtime = await unit_of_work.runtime.get_for_update(rule.id)
            outcome = engine.evaluate(rule, runtime, evaluated_at=now)
            self.event_ids.add(outcome.event.id)
            self.assertTrue(await unit_of_work.persist_outcome(outcome))

        async with self.sessions() as session:
            self.assertEqual(
                await session.scalar(
                    select(func.count(AlertEventModel.id)).where(AlertEventModel.id == outcome.event.id)
                ),
                1,
            )
            self.assertEqual(
                await session.scalar(
                    select(func.count(AlertEvidenceModel.id)).where(AlertEvidenceModel.event_id == outcome.event.id)
                ),
                1,
            )
            self.assertEqual(
                await session.scalar(
                    select(func.count(NotificationOutboxModel.id)).where(
                        NotificationOutboxModel.event_id == outcome.event.id
                    )
                ),
                1,
            )
            runtime_model = await session.get(RuleRuntimeModel, rule.id)
            self.assertTrue(runtime_model.episode_triggered)
            self.assertFalse(runtime_model.blocked_event_recorded)
            self.assertEqual(runtime_model.trigger_sequence, 1)

        replacement = replace(rule, id=uuid4(), version=2, name="Replacement threshold")
        async with SqlAlchemyUnitOfWork(self.sessions) as unit_of_work:
            market_model = await unit_of_work.markets.get_by_identity(market)
            await unit_of_work.rules.add_version(replacement, market_model.id, now)
        async with SqlAlchemyUnitOfWork(self.sessions) as unit_of_work:
            active = await unit_of_work.rules.list_active(market_model.id)
            self.assertEqual([item.id for item in active], [replacement.id])
        with self.assertRaises(ValueError):
            async with SqlAlchemyUnitOfWork(self.sessions) as unit_of_work:
                await unit_of_work.rules.add_version(
                    replace(replacement, name="Mutated in place"), market_model.id, now
                )

        # The read API uses the immutable event version, never the current rule name,
        # and projects only display fields (not ownership or delivery recipients).
        markets = MarketDataService(HyperliquidClient())
        app.state.markets = markets
        app.state.alert_sessions = self.sessions
        app.state.evaluator = SimpleNamespace(monitoring=lambda _: "evaluator_unavailable")
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/alerts?view=history&limit=1")
                self.assertEqual(response.status_code, 200)
                event_row = response.json()["items"][0]
                self.assertEqual(event_row["id"], str(outcome.event.id))
                self.assertEqual(event_row["name"], rule.name)
                self.assertEqual(event_row["version"], 1)
                self.assertEqual(event_row["status"], "confirmed")
                self.assertNotIn("owner_id", event_row)
                self.assertNotIn("deliveries", event_row)
                response = await client.get("/api/alerts?view=rules")
                rule_row = next(item for item in response.json()["items"] if item["id"] == str(replacement.id))
                self.assertEqual(rule_row["name"], replacement.name)
                self.assertEqual(rule_row["predicates"][0]["threshold"], "100")
                response = await client.get("/api/alerts?symbol=ETH&view=history")
                self.assertNotIn(str(outcome.event.id), [item["id"] for item in response.json()["items"]])
                self.assertEqual((await client.get("/api/alerts?symbol=HYPE")).status_code, 404)
                self.assertEqual((await client.get("/api/alerts?symbol=UNKNOWN")).status_code, 404)
                for query in ("limit=0", "limit=101", "view=unknown"):
                    self.assertEqual((await client.get(f"/api/alerts?{query}")).status_code, 422)
        finally:
            await markets.close()

        async with SqlAlchemyUnitOfWork(self.sessions) as unit_of_work:
            self.assertFalse(await unit_of_work.persist_outcome(outcome))

        async with self.sessions() as session:
            self.assertEqual(
                await session.scalar(
                    select(func.count(AlertEventModel.id)).where(AlertEventModel.id == outcome.event.id)
                ),
                1,
            )

    async def test_rule_creation_and_updates_reject_unsupported_markets(self):
        now = datetime.now(UTC)
        supported = [
            MarketIdentity("mainnet", "", "BTC"),
            MarketIdentity("mainnet", "", "ETH"),
            MarketIdentity("mainnet", "xyz", "SP500"),
            MarketIdentity("mainnet", "xyz", "XYZ100"),
            MarketIdentity("mainnet", "xyz", "BRENTOIL"),
        ]
        original = RuleVersion(
            id=uuid4(), rule_id=uuid4(), owner_id=uuid4(), version=1,
            name="Supported market", market=supported[0],
            predicates=(ThresholdPredicate(
                "price", Metric.MARK_PRICE, ComparisonOperator.GREATER_THAN,
                Decimal("100"), timedelta(minutes=1),
            ),),
        )
        for identity in supported:
            rule = original if identity == original.market else replace(
                original, id=uuid4(), rule_id=uuid4(), market=identity
            )
            async with SqlAlchemyUnitOfWork(self.sessions) as unit_of_work:
                market = await unit_of_work.markets.ensure(identity)
                self.market_ids.add(market.id)
                self.rule_ids.add(rule.rule_id)
                await unit_of_work.rules.add_version(rule, market.id, now)
            async with self.sessions() as session:
                self.assertEqual((await session.get(AlertRuleModel, rule.rule_id)).active_version_id, rule.id)
                self.assertEqual((await session.get(AlertRuleVersionModel, rule.id)).definition, rule_to_dict(rule))

        for identity in (
            MarketIdentity("mainnet", "", "HYPE"),
            MarketIdentity("mainnet", "", "UNKNOWN"),
            MarketIdentity("mainnet", "", "SP500"),
            MarketIdentity("testnet", "", "BTC"),
        ):
            with self.subTest(market=identity.key):
                rejected = replace(original, id=uuid4(), rule_id=uuid4(), market=identity)
                replacement = replace(
                    original, id=uuid4(), version=2, name="Rejected update",
                    market=identity, cooldown=timedelta(seconds=60),
                )
                self.rule_ids.add(rejected.rule_id)
                async with SqlAlchemyUnitOfWork(self.sessions) as unit_of_work:
                    market = await unit_of_work.markets.ensure(identity)
                    self.market_ids.add(market.id)
                    # Catch inside the transaction: rejection must leave nothing to commit.
                    with self.assertRaisesRegex(ValueError, "Unsupported alert market"):
                        await unit_of_work.rules.add_version(rejected, market.id, now)
                    with self.assertRaisesRegex(ValueError, "Unsupported alert market"):
                        await unit_of_work.rules.add_version(replacement, market.id, now, status="disabled")
                async with self.sessions() as session:
                    self.assertIsNone(await session.get(AlertRuleModel, rejected.rule_id))
                    self.assertIsNone(await session.get(AlertRuleVersionModel, rejected.id))
                    self.assertIsNone(await session.get(AlertRuleVersionModel, replacement.id))
                    stored = await session.get(AlertRuleModel, original.rule_id)
                    self.assertEqual(stored.active_version_id, original.id)
                    self.assertEqual(stored.name, original.name)
                    self.assertEqual(stored.status, "active")
                    self.assertEqual(stored.cooldown_seconds, 0)

    async def test_alert_views_match_picker_markets_before_pagination(self):
        identities = [
            MarketIdentity("mainnet", "", "BTC"),
            MarketIdentity("mainnet", "xyz", "SP500"),
            MarketIdentity("mainnet", "", "HYPE"),
            MarketIdentity("testnet", "", "BTC"),
            MarketIdentity("mainnet", "", "SP500"),
        ]
        for index, identity in enumerate(identities):
            # Excluded records are newest and must not consume the page limit.
            now = datetime.now(UTC) + timedelta(seconds=index)
            rule = RuleVersion(
                id=uuid4(), rule_id=uuid4(), owner_id=uuid4(), version=1,
                name=identity.key, market=identity,
                predicates=(ThresholdPredicate(
                    "price", Metric.MARK_PRICE, ComparisonOperator.GREATER_THAN,
                    Decimal("100"), timedelta(minutes=1),
                ),),
            )
            windows = ObservationWindowStore()
            windows.add(MarketObservation(
                market=identity, source="integration", channel="fixture",
                observed_at=now, received_at=now, values={Metric.MARK_PRICE: Decimal("101")},
            ))
            async with SqlAlchemyUnitOfWork(self.sessions) as unit_of_work:
                market = await unit_of_work.markets.ensure(identity)
                self.market_ids.add(market.id)
                self.rule_ids.add(rule.rule_id)
                # Insert legacy rows directly: unsupported markets cannot be created now.
                stored_rule = AlertRuleModel(
                    id=rule.rule_id, owner_id=rule.owner_id, name=rule.name,
                    status="active", quality_policy=rule.quality_policy.value,
                    cooldown_seconds=0, created_at=now, updated_at=now,
                )
                unit_of_work.session.add(stored_rule)
                await unit_of_work.session.flush()
                unit_of_work.session.add(AlertRuleVersionModel(
                    id=rule.id, rule_id=rule.rule_id, market_id=market.id,
                    version=rule.version, definition=rule_to_dict(rule),
                    created_at=now, confirmed_at=now,
                ))
                await unit_of_work.session.flush()
                stored_rule.active_version_id = rule.id
                runtime = await unit_of_work.runtime.get_for_update(rule.id)
                outcome = RuleEngine(windows).evaluate(rule, runtime, evaluated_at=now)
                self.event_ids.add(outcome.event.id)
                await unit_of_work.persist_outcome(outcome)

        markets = MarketDataService(HyperliquidClient())
        app.state.markets = markets
        app.state.alert_sessions = self.sessions
        app.state.evaluator = SimpleNamespace(monitoring=lambda _: "evaluator_unavailable")
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                for view in ("rules", "history"):
                    page = (await client.get(f"/api/alerts?view={view}&limit=1")).json()
                    self.assertTrue(page["has_more"])
                    self.assertEqual([item["name"] for item in page["items"]], ["mainnet:xyz:SP500"])
                    page = (await client.get(f"/api/alerts?view={view}")).json()
                    self.assertEqual(
                        [item["name"] for item in page["items"]], ["mainnet:xyz:SP500", "mainnet:native:BTC"]
                    )
                    self.assertFalse(page["has_more"])
                    page = (await client.get(f"/api/alerts?view={view}&symbol=SP500")).json()
                    self.assertEqual([item["name"] for item in page["items"]], ["mainnet:xyz:SP500"])
        finally:
            await markets.close()


if __name__ == "__main__":
    unittest.main()
