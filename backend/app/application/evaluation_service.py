import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.observations import MarketObservation
from app.domain.rules import RuleVersion
from app.engine.rule_engine import EvaluationOutcome, RuleEngine
from app.engine.windows import ObservationWindowStore, WindowUpdate
from app.persistence.unit_of_work import SqlAlchemyUnitOfWork


@dataclass(frozen=True, slots=True)
class ProcessedObservation:
    window_update: WindowUpdate
    sample_inserted: bool
    outcomes: tuple[EvaluationOutcome, ...]


class EvaluationService:
    def __init__(
        self,
        windows: ObservationWindowStore,
        engine: RuleEngine,
        unit_of_work_factory: Callable[[], SqlAlchemyUnitOfWork],
    ):
        self._windows = windows
        self._engine = engine
        self._unit_of_work_factory = unit_of_work_factory
        self._lock = asyncio.Lock()

    async def process(
        self,
        observation: MarketObservation,
        rules: Sequence[RuleVersion] | None = None,
    ) -> ProcessedObservation:
        async with self._lock:
            return await self._process_locked(observation, rules)

    async def evaluate(self, rule: RuleVersion, evaluated_at: datetime | None = None) -> EvaluationOutcome:
        async with self._lock:
            async with self._unit_of_work_factory() as unit_of_work:
                runtime = await unit_of_work.runtime.get_for_update(rule.id)
                evaluation_time = evaluated_at or datetime.now(UTC)
                outcome = self._engine.evaluate(rule, runtime, evaluated_at=evaluation_time)
                await unit_of_work.persist_outcome(outcome)
                return outcome

    async def _process_locked(
        self,
        observation: MarketObservation,
        rules: Sequence[RuleVersion] | None,
    ) -> ProcessedObservation:
        window_update = self._windows.add(observation)
        async with self._unit_of_work_factory() as unit_of_work:
            market = await unit_of_work.markets.ensure(observation.market)
            sample_inserted = await unit_of_work.samples.add(market.id, observation)
            active_rules = list(rules) if rules is not None else await unit_of_work.rules.list_active(market.id)

        outcomes = []
        evaluated_at = datetime.now(UTC)
        for rule in active_rules:
            if rule.market != observation.market:
                continue
            async with self._unit_of_work_factory() as unit_of_work:
                runtime = await unit_of_work.runtime.get_for_update(rule.id)
                outcome = self._engine.evaluate(rule, runtime, evaluated_at=evaluated_at)
                await unit_of_work.persist_outcome(outcome)
                outcomes.append(outcome)
        return ProcessedObservation(window_update, sample_inserted, tuple(outcomes))
