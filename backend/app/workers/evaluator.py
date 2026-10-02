import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from app.application import EvaluationService
from app.application.market_data import MarketDataService
from app.domain.rules import OpenInterestChangePredicate, RuleVersion
from app.engine import ObservationWindowStore, RuleEngine
from app.persistence import SqlAlchemyUnitOfWork, create_engine, create_session_factory

logger = logging.getLogger(__name__)


class EvaluationWorker:
    def __init__(self, markets: MarketDataService, tick_interval: float = 1.0):
        self.markets = markets
        self.rules: list[RuleVersion] = []
        self._tick_interval = tick_interval
        self._database_engine = create_engine()
        self._sessions = create_session_factory(self._database_engine)
        self._windows = ObservationWindowStore()
        self._engine = RuleEngine(self._windows)
        self._service = EvaluationService(
            self._windows,
            self._engine,
            lambda: SqlAlchemyUnitOfWork(self._sessions),
        )

    async def start(self) -> None:
        # Awaited before accepting HTTP requests: database failures fail startup.
        self.rules = await self._load_rules_and_warm_up()
        for rule in self.rules:
            self.markets.resolve(rule.market)

    async def close(self) -> None:
        await self._database_engine.dispose()

    async def run(self) -> None:
        rules_by_market = defaultdict(list)
        for rule in self.rules:
            rules_by_market[rule.market].append(rule)
        tasks = [asyncio.create_task(self._consume(market, rules)) for market, rules in rules_by_market.items()]
        tasks.append(asyncio.create_task(self._tick(self.rules)))
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _load_rules_and_warm_up(self) -> list[RuleVersion]:
        async with SqlAlchemyUnitOfWork(self._sessions) as unit_of_work:
            rules = await unit_of_work.rules.list_active()
            now = datetime.now(UTC)
            rules_by_market = defaultdict(list)
            for rule in rules:
                rules_by_market[rule.market].append(rule)
            for identity, market_rules in rules_by_market.items():
                market = await unit_of_work.markets.get_by_identity(identity)
                if market is None:
                    raise ValueError(f"Rule market is not registered: {identity.key}")
                horizon = max(self._required_horizon(rule) for rule in market_rules)
                observations = await unit_of_work.samples.list_since(
                    market.id,
                    identity,
                    now - horizon,
                )
                for observation in observations:
                    self._windows.add(observation)
        return rules

    async def _consume(self, market, rules: list[RuleVersion]) -> None:
        async with self.markets.subscribe(market, ("context", "book")) as updates:
            async for update in updates:
                if update.observation is None:
                    continue
                processed = await self._service.process(update.observation, rules)
                for outcome in processed.outcomes:
                    if outcome.event is not None:
                        logger.info(
                            "Alert event %s status=%s market=%s",
                            outcome.event.id,
                            outcome.event.status.value,
                            outcome.event.market.key,
                        )

    async def _tick(self, rules: list[RuleVersion]) -> None:
        while True:
            await asyncio.sleep(self._tick_interval)
            for rule in rules:
                outcome = await self._service.evaluate(rule)
                if outcome.event is not None:
                    logger.info(
                        "Timer event %s status=%s market=%s",
                        outcome.event.id,
                        outcome.event.status.value,
                        outcome.event.market.key,
                    )

    @staticmethod
    def _required_horizon(rule: RuleVersion) -> timedelta:
        horizons = [timedelta(minutes=1), *rule.quality.stale_after.values()]
        horizons.extend(
            predicate.window + predicate.baseline_tolerance
            for predicate in rule.predicates
            if isinstance(predicate, OpenInterestChangePredicate)
        )
        return max(horizons)
