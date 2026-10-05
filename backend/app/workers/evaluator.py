import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from time import monotonic

from app.application import EvaluationService
from app.application.market_data import MarketDataService
from app.domain.evaluations import ConditionState
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
        self._rules_lock = asyncio.Lock()
        self._feeds: dict = {}
        self._running = False
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

    def monitoring(self, rule_id) -> str:
        if not self._running:
            return "evaluator_unavailable"
        rule = next((rule for rule in self.rules if rule.rule_id == rule_id), None)
        if rule is None:
            return "inactive"
        task = self._feeds.get(rule.market)
        if task is None or task.done():
            return "evaluator_unavailable"
        result = self._engine.evaluate(rule, evaluated_at=datetime.now(UTC)).result
        if any(item.reason_code and item.reason_code.startswith("STALE_") for item in result.predicates) or any(
            item.reason_code == "GAP"
            or (item.check == "freshness" and item.status == "block" and item.evidence.get("age_seconds") is not None)
            for item in result.checks
        ):
            return "stale"
        if result.condition is ConditionState.UNKNOWN or any(
            item.check == "freshness" and item.status == "block" for item in result.checks
        ):
            return "warming_up"
        return "monitoring"

    async def reload_rules(self) -> None:
        async with self._rules_lock:
            async with SqlAlchemyUnitOfWork(self._sessions) as uow:
                active = await uow.rules.list_active()
            if {rule.id for rule in active} != {rule.id for rule in self.rules}:
                self.rules = await self._load_rules_and_warm_up()
            if self._running:
                await self._sync_feeds()

    async def _sync_feeds(self) -> None:
        wanted = {rule.market for rule in self.rules}
        for market in list(self._feeds):
            if market not in wanted:
                task = self._feeds.pop(market)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        for market in wanted:
            if market not in self._feeds:
                self._feeds[market] = asyncio.create_task(self._consume(market))

    async def run(self) -> None:
        self._running = True
        try:
            async with self._rules_lock:
                await self._sync_feeds()
            next_refresh = monotonic() + 5
            while True:
                tick = asyncio.create_task(asyncio.sleep(self._tick_interval))
                try:
                    await asyncio.wait([tick, *self._feeds.values()], return_when=asyncio.FIRST_COMPLETED)
                finally:
                    tick.cancel()
                    await asyncio.gather(tick, return_exceptions=True)
                # Reconcile committed changes even if the creating HTTP request was disconnected.
                if monotonic() >= next_refresh:
                    await self.reload_rules()
                    next_refresh = monotonic() + 5
                async with self._rules_lock:
                    for task in self._feeds.values():
                        if task.done():
                            await task
                            raise RuntimeError("Market subscription ended unexpectedly")
                    for rule in self.rules:
                        outcome = await self._service.evaluate(rule)
                        if outcome.event is not None:
                            logger.info(
                                "Timer event %s status=%s market=%s",
                                outcome.event.id,
                                outcome.event.status.value,
                                outcome.event.market.key,
                            )
        finally:
            self._running = False
            for task in self._feeds.values():
                task.cancel()
            await asyncio.gather(*self._feeds.values(), return_exceptions=True)
            self._feeds.clear()

    async def _load_rules_and_warm_up(self) -> list[RuleVersion]:
        windows = ObservationWindowStore()
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
                    windows.add(observation)
        self._windows = windows
        self._engine = RuleEngine(windows)
        self._service = EvaluationService(windows, self._engine, lambda: SqlAlchemyUnitOfWork(self._sessions))
        return rules

    async def _consume(self, market) -> None:
        async with self.markets.subscribe(market, ("context", "book")) as updates:
            async for update in updates:
                if update.observation is None:
                    continue
                async with self._rules_lock:
                    rules = [rule for rule in self.rules if rule.market == market]
                    processed = await self._service.process(update.observation, rules)
                for outcome in processed.outcomes:
                    if outcome.event is not None:
                        logger.info(
                            "Alert event %s status=%s market=%s",
                            outcome.event.id,
                            outcome.event.status.value,
                            outcome.event.market.key,
                        )

    @staticmethod
    def _required_horizon(rule: RuleVersion) -> timedelta:
        horizons = [
            timedelta(minutes=1),
            *rule.quality.stale_after.values(),
            *(predicate.max_age for predicate in rule.predicates),
        ]
        horizons.extend(
            predicate.window + predicate.baseline_tolerance
            for predicate in rule.predicates
            if isinstance(predicate, OpenInterestChangePredicate)
        )
        return max(horizons)
