"""Validated, instance-scoped alert management shared by the agent and API."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, tuple_

from app.domain.markets import UI_MARKETS, market_symbol
from app.domain.observations import Metric
from app.domain.rules import ChangeMode, Combinator, ComparisonOperator, QualityPolicy
from app.persistence import SqlAlchemyUnitOfWork
from app.persistence.models import (
    AlertEventModel,
    AlertEvidenceModel,
    AlertRuleModel,
    AlertRuleVersionModel,
    MarketModel,
)
from app.persistence.repositories import AlertReadRepository
from app.persistence.serialization import rule_from_dict

Symbol = Literal["BTC", "ETH", "SP500", "XYZ100", "BRENTOIL"]
INSTANCE_OWNER = uuid5(NAMESPACE_URL, "hype-radar:local-instance")
FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False, max_digits=38, decimal_places=18)]
NonNegativeDecimal = Annotated[FiniteDecimal, Field(ge=0)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ThresholdInput(InputModel):
    type: Literal["threshold"] = "threshold"
    metric: Metric
    operator: ComparisonOperator
    threshold: FiniteDecimal
    max_age_seconds: int = Field(default=30, ge=1, le=300)


class OIChangeInput(InputModel):
    type: Literal["open_interest_change"] = "open_interest_change"
    operator: ComparisonOperator
    threshold: FiniteDecimal
    window_seconds: int = Field(ge=1, le=86400)
    max_age_seconds: int = Field(default=30, ge=1, le=300)
    baseline_tolerance_seconds: int = Field(default=60, ge=1, le=300)
    mode: ChangeMode = ChangeMode.PERCENT


class QualityInput(InputModel):
    max_spread_bps: NonNegativeDecimal | None = None
    min_bid_depth: NonNegativeDecimal | None = None
    min_ask_depth: NonNegativeDecimal | None = None
    require_verified_oracle: bool = False


class AlertSpec(InputModel):
    name: str = Field(min_length=1, max_length=200)
    market: Symbol
    predicates: list[Annotated[ThresholdInput | OIChangeInput, Field(discriminator="type")]] = Field(
        min_length=1, max_length=8
    )
    combinator: Combinator = Combinator.ALL
    persistence_seconds: int = Field(default=0, ge=0, le=86400)
    cooldown_seconds: int = Field(default=300, ge=0, le=604800)
    quality_policy: QualityPolicy = QualityPolicy.BLOCK
    quality: QualityInput = Field(default_factory=QualityInput)


def public_definition(definition: dict) -> dict:
    return {key: value for key, value in definition.items() if key not in {"owner_id", "deliveries"}}


def describe_rule(definition: dict) -> str:
    labels = {metric.value: metric.value.replace("_", " ").capitalize() for metric in Metric}
    operators = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤"}
    conditions = []
    for predicate in definition["predicates"]:
        if predicate["type"] == "open_interest_change":
            label = f"OI change over {predicate['window_seconds']:g}s"
            unit = "%" if predicate["mode"] == "percent" else " base units"
            label += f" (baseline tolerance {predicate['baseline_tolerance_seconds']:g}s)"
        else:
            label = labels[predicate["metric"]]
            unit = " (hourly fraction)" if predicate["metric"] == "funding_rate" else ""
        conditions.append(
            f"{label} {operators[predicate['operator']]} {predicate['threshold']}{unit} "
            f"(data age ≤ {predicate['max_age_seconds']:g}s)"
        )
    quality = definition["quality"]
    details = [
        f"{definition['market']['coin']} · "
        + (" AND " if definition["combinator"] == "all" else " OR ").join(conditions),
        f"Hold for {definition['persistence_seconds']:g}s · Cooldown {definition['cooldown_seconds']:g}s",
        f"Quality policy: {definition['quality_policy']}",
    ]
    for key, label in (
        ("max_spread_bps", "Maximum spread (bps)"),
        ("min_bid_depth", "Minimum bid depth (quote notional)"),
        ("min_ask_depth", "Minimum ask depth (quote notional)"),
    ):
        if quality.get(key) is not None:
            details.append(f"{label}: {quality[key]} (book data age ≤ 15s)")
    if quality.get("require_verified_oracle"):
        details.append("Verified oracle required")
    details.append("Web history only. Level conditions may trigger immediately if already met.")
    return "\n".join(details)


class AlertService:
    def __init__(self, sessions, markets, evaluator):
        self.sessions = sessions
        self.markets = markets
        self.evaluator = evaluator
        self._writes = asyncio.Lock()

    def _visible(self):
        return [self.markets.resolve(symbol) for symbol in UI_MARKETS]

    async def preview(self, spec: AlertSpec, request_id: UUID) -> dict:
        identity = self.markets.resolve(spec.market)

        def decimal_key(value: Decimal) -> str:
            # Normalize formatting without rounding to Decimal's current precision.
            text = format(value, "f") if value else "0"
            return text.rstrip("0").rstrip(".") if "." in text else text

        conditions = spec.model_dump(exclude={"name"})
        conditions["predicates"] = sorted(
            json.dumps(item, sort_keys=True, default=decimal_key) for item in conditions["predicates"]
        )
        rule_id = uuid5(request_id, json.dumps(conditions, sort_keys=True, default=decimal_key))
        version_id = uuid5(rule_id, "1")
        predicates = [
            dict(item.model_dump(mode="json"), id=f"condition_{i + 1}") for i, item in enumerate(spec.predicates)
        ]
        ages = {}
        for item in predicates:
            metric = item.get("metric", "open_interest")
            ages[metric] = min(ages.get(metric, 300), item["max_age_seconds"])
        quality = spec.quality.model_dump(mode="json")
        if quality["max_spread_bps"] is not None:
            ages.update(bid_price=min(ages.get("bid_price", 15), 15), ask_price=min(ages.get("ask_price", 15), 15))
        for metric in ("bid_depth", "ask_depth"):
            if quality[f"min_{metric}"] is not None:
                ages[metric] = min(ages.get(metric, 15), 15)
        definition = {
            "schema_version": 1,
            "id": str(version_id),
            "rule_id": str(rule_id),
            "owner_id": str(INSTANCE_OWNER),
            "version": 1,
            "name": spec.name,
            "market": {"network": identity.network, "dex": identity.dex, "coin": identity.coin},
            "predicates": predicates,
            "combinator": spec.combinator.value,
            "persistence_seconds": spec.persistence_seconds,
            "cooldown_seconds": spec.cooldown_seconds,
            "quality_policy": spec.quality_policy.value,
            "quality": {**quality, "stale_after_seconds": ages},
            "deliveries": [],
        }
        rule = rule_from_dict(definition)
        async with self._writes, SqlAlchemyUnitOfWork(self.sessions) as uow:
            # A retried preview must never demote an already activated rule.
            if await uow.session.get(AlertRuleModel, rule_id) is None:
                market = await uow.markets.ensure(identity)
                await uow.rules.add_version(rule, market.id, datetime.now(UTC), status="draft")
            stored = await uow.session.get(AlertRuleVersionModel, version_id)
            definition = stored.definition
        return {
            "preview_id": str(version_id),
            "definition": public_definition(definition),
            "summary": describe_rule(definition),
            "expires_at": (stored.created_at + timedelta(hours=1)).isoformat(),
            "delivery": "Web event history only; external notifications are not sent.",
            "semantics": "Thresholds test the current level, not a crossing. One event per true episode.",
        }

    async def create(self, preview_id: UUID) -> dict:
        async with self._writes:
            async with self.sessions() as session, session.begin():
                version = await session.get(AlertRuleVersionModel, preview_id)
                if version is None:
                    raise ValueError("Alert preview not found")
                rule = await session.get(AlertRuleModel, version.rule_id, with_for_update=True)
                self._check_market(version)
                if rule.active_version_id != preview_id:
                    raise ValueError("This preview has been superseded by another rule version.")
                if version.confirmed_at is None:
                    if rule.status != "draft" or datetime.now(UTC) - version.created_at > timedelta(hours=1):
                        raise ValueError("Alert preview expired; request a new preview.")
                    version.confirmed_at = datetime.now(UTC)
                    rule.status = "active"
                    rule.updated_at = version.confirmed_at
                result = {
                    "alert_id": str(rule.id),
                    "version_id": str(version.id),
                    "status": rule.status,
                    "definition": public_definition(version.definition),
                    "summary": describe_rule(version.definition),
                }
            # Retry also reconciles a previous committed activation whose response was lost.
            await self.evaluator.reload_rules()
            return {**result, "monitoring": self.evaluator.monitoring(rule.id)}

    def _check_market(self, version):
        if rule_from_dict(version.definition).market not in self._visible():
            raise ValueError("Unsupported alert market")

    async def set_status(self, alert_id: UUID, status: Literal["active", "paused"]) -> dict:
        if status not in {"active", "paused"}:
            raise ValueError("Status must be active or paused")
        async with self._writes:
            async with self.sessions() as session, session.begin():
                rule = await session.get(AlertRuleModel, alert_id, with_for_update=True)
                if rule is None:
                    raise ValueError("Alert not found")
                version = await session.get(AlertRuleVersionModel, rule.active_version_id)
                if version is None or version.confirmed_at is None:
                    raise ValueError("Draft alerts cannot be resumed; create the alert first")
                self._check_market(version)
                rule.status = status
                rule.updated_at = datetime.now(UTC)
            await self.evaluator.reload_rules()
            return {"alert_id": str(alert_id), "status": status, "monitoring": self.evaluator.monitoring(alert_id)}

    async def list_alerts(
        self,
        market: Symbol | None = None,
        status: str | None = None,
        limit: int = 30,
        view: Literal["rules", "history"] = "rules",
        offset: int = 0,
    ) -> dict:
        identities = [self.markets.resolve(market)] if market else self._visible()
        if view == "history":
            if status is not None:
                raise ValueError("Active/paused status applies to rules, not event history")
            async with self.sessions() as session:
                rows = await AlertReadRepository(session).list_rows("history", identities, limit + 1, offset=offset)
            return {
                "scope": "instance",
                "has_more": len(rows) > limit,
                "next_offset": offset + limit if len(rows) > limit else None,
                "items": [
                    {
                        **row,
                        "id": str(row["id"]),
                        "event_id": str(row["id"]),
                        "evaluated_at": row["evaluated_at"].isoformat(),
                    }
                    for row in rows[:limit]
                ],
            }
        statement = (
            select(AlertRuleModel, AlertRuleVersionModel)
            .join(AlertRuleVersionModel, AlertRuleVersionModel.id == AlertRuleModel.active_version_id)
            .join(MarketModel, MarketModel.id == AlertRuleVersionModel.market_id)
            .where(
                tuple_(MarketModel.network, MarketModel.dex, MarketModel.coin).in_(
                    [(item.network, item.dex, item.coin) for item in identities]
                )
            )
            .where(AlertRuleModel.status.in_([status] if status else ["active", "paused"]))
            .order_by(AlertRuleModel.created_at.desc(), AlertRuleModel.id)
            .limit(limit + 1)
            .offset(offset)
        )
        async with self.sessions() as session:
            rows = (await session.execute(statement)).all()
        return {
            "scope": "instance",
            "has_more": len(rows) > limit,
            "next_offset": offset + limit if len(rows) > limit else None,
            "items": [
                {
                    "alert_id": str(rule.id),
                    "status": rule.status,
                    "monitoring": self.evaluator.monitoring(rule.id),
                    "definition": public_definition(version.definition),
                }
                for rule, version in rows[:limit]
            ],
        }

    async def get_event(self, event_id: UUID) -> dict:
        async with self.sessions() as session:
            event = await session.get(AlertEventModel, event_id)
            if event is None:
                raise ValueError("Alert event not found")
            version = await session.get(AlertRuleVersionModel, event.rule_version_id)
            self._check_market(version)
            evidence = await session.scalar(select(AlertEvidenceModel).where(AlertEvidenceModel.event_id == event_id))
            return {
                "event_id": str(event.id),
                "evaluated_at": event.evaluated_at.isoformat(),
                "status": event.status,
                "condition": event.condition_state,
                "quality": event.quality_status,
                "definition": public_definition(version.definition),
                "evidence": evidence.payload if evidence else None,
                "market": market_symbol(rule_from_dict(version.definition).market),
            }
