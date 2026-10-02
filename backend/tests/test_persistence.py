import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, func, select

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
from app.persistence import SqlAlchemyUnitOfWork, create_engine, create_session_factory
from app.persistence.models import (
    AlertEventModel,
    AlertEvidenceModel,
    AlertRuleModel,
    DeliveryModel,
    MarketModel,
    MarketSampleModel,
    NotificationOutboxModel,
    RuleRuntimeModel,
)

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

    async def test_event_evidence_outbox_and_runtime_are_committed_atomically(self):
        now = datetime.now(UTC)
        market = MarketIdentity("integration", "", f"HYPE-{uuid4()}")
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

        async with SqlAlchemyUnitOfWork(self.sessions) as unit_of_work:
            self.assertFalse(await unit_of_work.persist_outcome(outcome))

        async with self.sessions() as session:
            self.assertEqual(
                await session.scalar(
                    select(func.count(AlertEventModel.id)).where(AlertEventModel.id == outcome.event.id)
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()
