from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from .markets import MarketIdentity
from .observations import Metric


class ComparisonOperator(StrEnum):
    GREATER_THAN = "gt"
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "lte"


class Combinator(StrEnum):
    ALL = "all"
    ANY = "any"


class QualityPolicy(StrEnum):
    WARN = "warn"
    BLOCK = "block"


class ChangeMode(StrEnum):
    ABSOLUTE = "absolute"
    PERCENT = "percent"


@dataclass(frozen=True, slots=True)
class ThresholdPredicate:
    id: str
    metric: Metric
    operator: ComparisonOperator
    threshold: Decimal
    max_age: timedelta

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Predicate id must not be blank")
        if self.max_age <= timedelta(0):
            raise ValueError("Predicate max_age must be positive")
        threshold = self.threshold if isinstance(self.threshold, Decimal) else Decimal(str(self.threshold))
        if not threshold.is_finite():
            raise ValueError("Predicate threshold must be finite")
        object.__setattr__(self, "threshold", threshold)


@dataclass(frozen=True, slots=True)
class OpenInterestChangePredicate:
    id: str
    operator: ComparisonOperator
    threshold: Decimal
    window: timedelta
    max_age: timedelta
    baseline_tolerance: timedelta
    mode: ChangeMode = ChangeMode.PERCENT

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Predicate id must not be blank")
        if min(self.window, self.max_age, self.baseline_tolerance) <= timedelta(0):
            raise ValueError("OI predicate durations must be positive")
        threshold = self.threshold if isinstance(self.threshold, Decimal) else Decimal(str(self.threshold))
        if not threshold.is_finite():
            raise ValueError("Predicate threshold must be finite")
        object.__setattr__(self, "threshold", threshold)


type Predicate = ThresholdPredicate | OpenInterestChangePredicate


@dataclass(frozen=True, slots=True)
class QualitySettings:
    stale_after: Mapping[Metric, timedelta] = field(default_factory=dict)
    max_clock_skew: timedelta | None = None
    max_spread_bps: Decimal | None = None
    min_bid_depth: Decimal | None = None
    min_ask_depth: Decimal | None = None
    require_verified_oracle: bool = False

    def __post_init__(self) -> None:
        normalized_ages = {}
        for metric, duration in self.stale_after.items():
            if duration <= timedelta(0):
                raise ValueError(f"Freshness budget for {metric.value} must be positive")
            normalized_ages[metric] = duration
        object.__setattr__(self, "stale_after", MappingProxyType(normalized_ages))
        if self.max_clock_skew is not None and self.max_clock_skew <= timedelta(0):
            raise ValueError("max_clock_skew must be positive")
        for name in ("max_spread_bps", "min_bid_depth", "min_ask_depth"):
            value = getattr(self, name)
            if value is None:
                continue
            decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
            if not decimal_value.is_finite() or decimal_value < 0:
                raise ValueError(f"{name} must be a non-negative finite number")
            object.__setattr__(self, name, decimal_value)


@dataclass(frozen=True, slots=True)
class DeliveryTarget:
    channel: str
    recipient: str

    def __post_init__(self) -> None:
        if not self.channel.strip() or not self.recipient.strip():
            raise ValueError("Delivery channel and recipient must not be blank")


@dataclass(frozen=True, slots=True)
class RuleVersion:
    id: UUID
    rule_id: UUID
    owner_id: UUID
    version: int
    name: str
    market: MarketIdentity
    predicates: tuple[Predicate, ...]
    combinator: Combinator = Combinator.ALL
    persistence: timedelta = timedelta(0)
    cooldown: timedelta = timedelta(0)
    quality_policy: QualityPolicy = QualityPolicy.WARN
    quality: QualitySettings = field(default_factory=QualitySettings)
    deliveries: tuple[DeliveryTarget, ...] = ()

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("Rule version must be positive")
        if not self.name.strip():
            raise ValueError("Rule name must not be blank")
        if not self.predicates:
            raise ValueError("Rule must have at least one predicate")
        if len({predicate.id for predicate in self.predicates}) != len(self.predicates):
            raise ValueError("Predicate ids must be unique within a rule")
        if self.persistence < timedelta(0) or self.cooldown < timedelta(0):
            raise ValueError("Persistence and cooldown must not be negative")
