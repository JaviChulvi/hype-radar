import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from app.application import EvaluationService
from app.domain.rules import OpenInterestChangePredicate, RuleVersion
from app.engine import ObservationWindowStore, RuleEngine
from app.ingestion import HyperliquidObservationFeed
from app.persistence import SqlAlchemyUnitOfWork, create_engine, create_session_factory

logger = logging.getLogger(__name__)


class EvaluationWorker:
    def __init__(self, tick_interval: float = 1.0):
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

    async def run(self) -> None:
        rules = await self._load_rules_and_warm_up()
        if not rules:
            raise RuntimeError("No active rule versions are registered")
        rules_by_market = defaultdict(list)
        for rule in rules:
            rules_by_market[rule.market].append(rule)
        queue: asyncio.Queue = asyncio.Queue(maxsize=1_000)

        async def publish(observation) -> None:
            await queue.put(observation)

        feed_tasks = [
            asyncio.create_task(HyperliquidObservationFeed(market, publish).run()) for market in rules_by_market
        ]
        consumer = asyncio.create_task(self._consume(queue, rules_by_market))
        ticker = asyncio.create_task(self._tick(rules))
        tasks = [*feed_tasks, consumer, ticker]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._database_engine.dispose()

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

    async def _consume(self, queue: asyncio.Queue, rules_by_market: dict) -> None:
        while True:
            observation = await queue.get()
            try:
                processed = await self._service.process(observation, rules_by_market[observation.market])
                for outcome in processed.outcomes:
                    if outcome.event is not None:
                        logger.info(
                            "Alert event %s status=%s market=%s",
                            outcome.event.id,
                            outcome.event.status.value,
                            outcome.event.market.key,
                        )
            finally:
                queue.task_done()

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
            predicate.window for predicate in rule.predicates if isinstance(predicate, OpenInterestChangePredicate)
        )
        return max(horizons)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(EvaluationWorker().run())
    except KeyboardInterrupt:
        logger.info("Evaluation worker stopped")


if __name__ == "__main__":
    main()
