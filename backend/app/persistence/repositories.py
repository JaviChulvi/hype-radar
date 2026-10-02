from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.evaluations import ConditionState
from app.domain.markets import UI_MARKETS, MarketIdentity, resolve_market
from app.domain.observations import MarketObservation, Metric
from app.domain.rules import RuleVersion
from app.engine.rule_engine import RuleRuntimeState

from .models import (
    AlertEventModel,
    AlertRuleModel,
    AlertRuleVersionModel,
    MarketModel,
    MarketSampleModel,
    RuleRuntimeModel,
)
from .serialization import rule_from_dict, rule_to_dict


class MarketRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_identity(self, identity: MarketIdentity) -> MarketModel | None:
        statement = select(MarketModel).where(
            MarketModel.network == identity.network,
            MarketModel.dex == identity.dex,
            MarketModel.coin == identity.coin,
        )
        return await self._session.scalar(statement)

    async def ensure(self, identity: MarketIdentity) -> MarketModel:
        existing = await self.get_by_identity(identity)
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        statement = (
            insert(MarketModel)
            .values(
                network=identity.network,
                dex=identity.dex,
                coin=identity.coin,
                metadata_version=1,
                created_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_market_identity")
            .returning(MarketModel.id)
        )
        await self._session.execute(statement)
        market = await self.get_by_identity(identity)
        if market is None:
            raise RuntimeError(f"Failed to register market: {identity.key}")
        return market


class MarketSampleRepository:
    _METRIC_COLUMNS = {
        Metric.MARK_PRICE: "mark_price",
        Metric.ORACLE_PRICE: "oracle_price",
        Metric.BID_PRICE: "bid_price",
        Metric.ASK_PRICE: "ask_price",
        Metric.MID_PRICE: "mid_price",
        Metric.OPEN_INTEREST: "open_interest",
        Metric.FUNDING_RATE: "funding_rate",
        Metric.BID_DEPTH: "bid_depth",
        Metric.ASK_DEPTH: "ask_depth",
    }

    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, market_id: UUID, observation: MarketObservation) -> bool:
        values = {column: observation.values.get(metric) for metric, column in self._METRIC_COLUMNS.items()}
        statement = (
            insert(MarketSampleModel)
            .values(
                market_id=market_id,
                source=observation.source,
                channel=observation.channel,
                source_at=observation.observed_at,
                received_at=observation.received_at,
                gap=observation.gap,
                out_of_order=observation.out_of_order,
                schema_version=observation.schema_version,
                payload=_json_compatible(dict(observation.raw_payload)),
                **values,
            )
            .on_conflict_do_nothing(constraint="uq_market_sample_source")
            .returning(MarketSampleModel.id)
        )
        return await self._session.scalar(statement) is not None

    async def list_since(
        self,
        market_id: UUID,
        market: MarketIdentity,
        since: datetime,
    ) -> list[MarketObservation]:
        statement = (
            select(MarketSampleModel)
            .where(
                MarketSampleModel.market_id == market_id,
                MarketSampleModel.received_at >= since,
            )
            .order_by(MarketSampleModel.received_at, MarketSampleModel.id)
        )
        models = (await self._session.scalars(statement)).all()
        observations = []
        for model in models:
            values = {
                metric: getattr(model, column)
                for metric, column in self._METRIC_COLUMNS.items()
                if getattr(model, column) is not None
            }
            observations.append(
                MarketObservation(
                    market=market,
                    source=model.source,
                    channel=model.channel,
                    observed_at=model.source_at,
                    received_at=model.received_at,
                    values=values,
                    schema_version=model.schema_version,
                    gap=model.gap,
                    out_of_order=model.out_of_order,
                    raw_payload=model.payload,
                )
            )
        return observations


class RuleRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_active(self, market_id: UUID | None = None) -> list[RuleVersion]:
        statement = (
            select(AlertRuleVersionModel)
            .join(AlertRuleModel, AlertRuleVersionModel.rule_id == AlertRuleModel.id)
            .where(
                AlertRuleModel.status == "active",
                AlertRuleVersionModel.id == AlertRuleModel.active_version_id,
            )
        )
        if market_id is not None:
            statement = statement.where(AlertRuleVersionModel.market_id == market_id)
        models = (await self._session.scalars(statement)).all()
        return [rule_from_dict(model.definition) for model in models]

    async def add_version(
        self,
        rule: RuleVersion,
        market_id: UUID,
        created_at: datetime,
        status: str = "active",
    ) -> None:
        network = get_settings().hyperliquid_network
        if rule.market not in {resolve_market(symbol, network) for symbol in UI_MARKETS}:
            raise ValueError(
                f"Unsupported alert market: {rule.market.key}. "
                f"Supported markets on {network}: {', '.join(UI_MARKETS)}"
            )
        serialized = rule_to_dict(rule)
        rule_model = await self._session.get(AlertRuleModel, rule.rule_id)
        if rule_model is None:
            rule_model = AlertRuleModel(
                id=rule.rule_id,
                owner_id=rule.owner_id,
                name=rule.name,
                status=status,
                quality_policy=rule.quality_policy.value,
                cooldown_seconds=int(rule.cooldown.total_seconds()),
                created_at=created_at,
                updated_at=created_at,
            )
            self._session.add(rule_model)
        else:
            if rule_model.owner_id != rule.owner_id:
                raise ValueError("Rule ownership is immutable")
            rule_model.name = rule.name
            rule_model.status = status
            rule_model.quality_policy = rule.quality_policy.value
            rule_model.cooldown_seconds = int(rule.cooldown.total_seconds())
            rule_model.updated_at = created_at
        existing_version = await self._session.get(AlertRuleVersionModel, rule.id)
        if existing_version is None:
            self._session.add(
                AlertRuleVersionModel(
                    id=rule.id,
                    rule_id=rule.rule_id,
                    market_id=market_id,
                    version=rule.version,
                    definition=serialized,
                    created_at=created_at,
                    confirmed_at=created_at,
                )
            )
        elif existing_version.definition != serialized:
            raise ValueError("Confirmed rule versions are immutable")
        await self._session.flush()
        rule_model.active_version_id = rule.id


class AlertReadRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_rows(self, view: str, markets: list[MarketIdentity], limit: int) -> list[dict]:
        if view == "rules":
            statement = (
                select(AlertRuleVersionModel, MarketModel)
                .join(MarketModel, AlertRuleVersionModel.market_id == MarketModel.id)
                .join(AlertRuleModel, AlertRuleVersionModel.rule_id == AlertRuleModel.id)
                .where(
                    AlertRuleModel.status == "active",
                    AlertRuleModel.active_version_id == AlertRuleVersionModel.id,
                )
                .order_by(AlertRuleVersionModel.created_at.desc(), AlertRuleVersionModel.id.desc())
            )
        else:
            statement = (
                select(AlertRuleVersionModel, MarketModel, AlertEventModel)
                .join(MarketModel, AlertRuleVersionModel.market_id == MarketModel.id)
                .join(AlertEventModel, AlertEventModel.rule_version_id == AlertRuleVersionModel.id)
                .order_by(AlertEventModel.evaluated_at.desc(), AlertEventModel.id.desc())
            )
        statement = statement.where(
            tuple_(MarketModel.network, MarketModel.dex, MarketModel.coin).in_(
                [(market.network, market.dex, market.coin) for market in markets]
            )
        )
        rows = (await self._session.execute(statement.limit(limit))).all()
        result = []
        for version, identity, *events in rows:
            definition = version.definition
            item = {
                "id": version.id,
                "name": definition["name"],
                "version": version.version,
                "market": {"network": identity.network, "dex": identity.dex, "coin": identity.coin},
                "predicates": definition["predicates"],
                "combinator": definition["combinator"],
                "persistence_seconds": definition["persistence_seconds"],
                "cooldown_seconds": definition["cooldown_seconds"],
                "quality_policy": definition["quality_policy"],
            }
            if events:
                event = events[0]
                item.update(
                    id=event.id,
                    status=event.status,
                    condition=event.condition_state,
                    quality=event.quality_status,
                    evaluated_at=event.evaluated_at,
                )
            result.append(item)
        return result


class RuntimeRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_for_update(self, rule_version_id: UUID) -> RuleRuntimeState:
        now = datetime.now(UTC)
        await self._session.execute(
            insert(RuleRuntimeModel)
            .values(
                rule_version_id=rule_version_id,
                condition_state=ConditionState.UNKNOWN.value,
                trigger_sequence=0,
                episode_triggered=False,
                blocked_event_recorded=False,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[RuleRuntimeModel.rule_version_id])
        )
        statement = (
            select(RuleRuntimeModel).where(RuleRuntimeModel.rule_version_id == rule_version_id).with_for_update()
        )
        model = await self._session.scalar(statement)
        if model is None:
            return RuleRuntimeState(rule_version_id)
        return RuleRuntimeState(
            rule_version_id=model.rule_version_id,
            condition=ConditionState(model.condition_state),
            true_since=model.true_since,
            last_triggered_at=model.last_triggered_at,
            trigger_sequence=model.trigger_sequence,
            episode_triggered=model.episode_triggered,
            blocked_event_recorded=model.blocked_event_recorded,
            last_processed_at=model.last_processed_at,
        )

    async def save(self, runtime: RuleRuntimeState, updated_at: datetime) -> None:
        model = await self._session.get(RuleRuntimeModel, runtime.rule_version_id)
        if model is None:
            model = RuleRuntimeModel(rule_version_id=runtime.rule_version_id)
            self._session.add(model)
        model.condition_state = runtime.condition.value
        model.true_since = runtime.true_since
        model.last_triggered_at = runtime.last_triggered_at
        model.trigger_sequence = runtime.trigger_sequence
        model.episode_triggered = runtime.episode_triggered
        model.blocked_event_recorded = runtime.blocked_event_recorded
        model.last_processed_at = runtime.last_processed_at
        model.updated_at = updated_at


def _json_compatible(value):
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "is_finite"):
        return str(value)
    return value
