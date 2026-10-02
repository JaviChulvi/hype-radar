from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from app.domain.markets import MarketIdentity
from app.domain.observations import Metric
from app.domain.rules import (
    ChangeMode,
    Combinator,
    ComparisonOperator,
    DeliveryTarget,
    OpenInterestChangePredicate,
    QualityPolicy,
    QualitySettings,
    RuleVersion,
    ThresholdPredicate,
)


def rule_to_dict(rule: RuleVersion) -> dict:
    predicates = []
    for predicate in rule.predicates:
        if isinstance(predicate, ThresholdPredicate):
            predicates.append(
                {
                    "type": "threshold",
                    "id": predicate.id,
                    "metric": predicate.metric.value,
                    "operator": predicate.operator.value,
                    "threshold": str(predicate.threshold),
                    "max_age_seconds": predicate.max_age.total_seconds(),
                }
            )
        else:
            predicates.append(
                {
                    "type": "open_interest_change",
                    "id": predicate.id,
                    "operator": predicate.operator.value,
                    "threshold": str(predicate.threshold),
                    "window_seconds": predicate.window.total_seconds(),
                    "max_age_seconds": predicate.max_age.total_seconds(),
                    "baseline_tolerance_seconds": predicate.baseline_tolerance.total_seconds(),
                    "mode": predicate.mode.value,
                }
            )
    return {
        "schema_version": 1,
        "id": str(rule.id),
        "rule_id": str(rule.rule_id),
        "owner_id": str(rule.owner_id),
        "version": rule.version,
        "name": rule.name,
        "market": {
            "network": rule.market.network,
            "dex": rule.market.dex,
            "coin": rule.market.coin,
        },
        "predicates": predicates,
        "combinator": rule.combinator.value,
        "persistence_seconds": rule.persistence.total_seconds(),
        "cooldown_seconds": rule.cooldown.total_seconds(),
        "quality_policy": rule.quality_policy.value,
        "quality": {
            "stale_after_seconds": {
                metric.value: duration.total_seconds() for metric, duration in rule.quality.stale_after.items()
            },
            "max_clock_skew_seconds": (
                None if rule.quality.max_clock_skew is None else rule.quality.max_clock_skew.total_seconds()
            ),
            "max_spread_bps": _decimal_or_none(rule.quality.max_spread_bps),
            "min_bid_depth": _decimal_or_none(rule.quality.min_bid_depth),
            "min_ask_depth": _decimal_or_none(rule.quality.min_ask_depth),
            "require_verified_oracle": rule.quality.require_verified_oracle,
        },
        "deliveries": [{"channel": target.channel, "recipient": target.recipient} for target in rule.deliveries],
    }


def rule_from_dict(data: dict) -> RuleVersion:
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported rule schema version")
    predicates = []
    for item in data["predicates"]:
        if item["type"] == "threshold":
            predicates.append(
                ThresholdPredicate(
                    id=item["id"],
                    metric=Metric(item["metric"]),
                    operator=ComparisonOperator(item["operator"]),
                    threshold=Decimal(item["threshold"]),
                    max_age=timedelta(seconds=item["max_age_seconds"]),
                )
            )
        elif item["type"] == "open_interest_change":
            predicates.append(
                OpenInterestChangePredicate(
                    id=item["id"],
                    operator=ComparisonOperator(item["operator"]),
                    threshold=Decimal(item["threshold"]),
                    window=timedelta(seconds=item["window_seconds"]),
                    max_age=timedelta(seconds=item["max_age_seconds"]),
                    baseline_tolerance=timedelta(seconds=item["baseline_tolerance_seconds"]),
                    mode=ChangeMode(item["mode"]),
                )
            )
        else:
            raise ValueError(f"Unsupported predicate type: {item['type']}")
    quality_data = data.get("quality", {})
    market_data = data["market"]
    return RuleVersion(
        id=UUID(data["id"]),
        rule_id=UUID(data["rule_id"]),
        owner_id=UUID(data["owner_id"]),
        version=data["version"],
        name=data["name"],
        market=MarketIdentity(**market_data),
        predicates=tuple(predicates),
        combinator=Combinator(data.get("combinator", "all")),
        persistence=timedelta(seconds=data.get("persistence_seconds", 0)),
        cooldown=timedelta(seconds=data.get("cooldown_seconds", 0)),
        quality_policy=QualityPolicy(data.get("quality_policy", "warn")),
        quality=QualitySettings(
            stale_after={
                Metric(metric): timedelta(seconds=seconds)
                for metric, seconds in quality_data.get("stale_after_seconds", {}).items()
            },
            max_clock_skew=(
                None
                if quality_data.get("max_clock_skew_seconds") is None
                else timedelta(seconds=quality_data["max_clock_skew_seconds"])
            ),
            max_spread_bps=_parse_decimal(quality_data.get("max_spread_bps")),
            min_bid_depth=_parse_decimal(quality_data.get("min_bid_depth")),
            min_ask_depth=_parse_decimal(quality_data.get("min_ask_depth")),
            require_verified_oracle=quality_data.get("require_verified_oracle", False),
        ),
        deliveries=tuple(DeliveryTarget(**target) for target in data.get("deliveries", ())),
    )


def _decimal_or_none(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _parse_decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)
