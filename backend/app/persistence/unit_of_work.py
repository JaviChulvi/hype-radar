from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.events import AlertEventDraft
from app.engine.rule_engine import EvaluationOutcome

from .models import (
    AlertEventModel,
    AlertEvidenceModel,
    NotificationOutboxModel,
)
from .repositories import (
    MarketRepository,
    MarketSampleRepository,
    RuleRepository,
    RuntimeRepository,
)


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory
        self.session: AsyncSession | None = None
        self.markets: MarketRepository
        self.samples: MarketSampleRepository
        self.rules: RuleRepository
        self.runtime: RuntimeRepository

    async def __aenter__(self) -> Self:
        self.session = self._session_factory()
        self.markets = MarketRepository(self.session)
        self.samples = MarketSampleRepository(self.session)
        self.rules = RuleRepository(self.session)
        self.runtime = RuntimeRepository(self.session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        try:
            if exc_type is None:
                await self.session.commit()
            else:
                await self.session.rollback()
        finally:
            await self.session.close()

    async def persist_outcome(self, outcome: EvaluationOutcome) -> bool:
        if self.session is None:
            raise RuntimeError("Unit of work has not been entered")
        await self.runtime.save(outcome.runtime, outcome.result.evaluated_at)
        if outcome.event is None:
            return False
        return await self._persist_event(outcome.event)

    async def _persist_event(self, event: AlertEventDraft) -> bool:
        market = await self.markets.get_by_identity(event.market)
        if market is None:
            raise ValueError(f"Market is not registered: {event.market.key}")
        now = datetime.now(UTC)
        statement = (
            insert(AlertEventModel)
            .values(
                id=event.id,
                rule_version_id=event.rule_version_id,
                market_id=market.id,
                evaluated_at=event.evaluated_at,
                status=event.status.value,
                condition_state=event.condition.value,
                quality_status=event.quality.value,
                transition=event.transition,
                fingerprint=event.fingerprint,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=[AlertEventModel.fingerprint])
            .returning(AlertEventModel.id)
        )
        inserted_id = await self.session.scalar(statement)
        if inserted_id is None:
            return False
        self.session.add(
            AlertEvidenceModel(
                event_id=event.id,
                schema_version=event.evidence.get("schema_version", 1),
                payload=dict(event.evidence),
                created_at=now,
            )
        )
        for target in event.deliveries:
            outbox_id = uuid5(NAMESPACE_URL, f"{event.id}:{target.channel}:{target.recipient}")
            self.session.add(
                NotificationOutboxModel(
                    id=outbox_id,
                    event_id=event.id,
                    channel=target.channel,
                    recipient=target.recipient,
                    payload={
                        "event_id": str(event.id),
                        "status": event.status.value,
                        "market": event.market.key,
                    },
                    status="pending",
                    attempts=0,
                    available_at=now,
                    created_at=now,
                )
            )
        return True
