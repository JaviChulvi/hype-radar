from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from .markets import MarketIdentity


class Metric(StrEnum):
    MARK_PRICE = "mark_price"
    ORACLE_PRICE = "oracle_price"
    MID_PRICE = "mid_price"
    BID_PRICE = "bid_price"
    ASK_PRICE = "ask_price"
    OPEN_INTEREST = "open_interest"
    FUNDING_RATE = "funding_rate"
    BID_DEPTH = "bid_depth"
    ASK_DEPTH = "ask_depth"


class OracleRegime(StrEnum):
    EXTERNAL = "external"
    INTERNAL = "internal"
    UNVERIFIED = "unverified"


def require_aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class MarketObservation:
    market: MarketIdentity
    source: str
    channel: str
    observed_at: datetime
    received_at: datetime
    values: Mapping[Metric, Decimal]
    schema_version: int = 1
    gap: bool = False
    out_of_order: bool = False
    oracle_regime: OracleRegime = OracleRegime.UNVERIFIED
    raw_payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.channel.strip():
            raise ValueError("Observation source and channel must not be blank")
        if self.schema_version < 1:
            raise ValueError("Observation schema version must be positive")
        observed_at = require_aware_utc(self.observed_at, "observed_at")
        received_at = require_aware_utc(self.received_at, "received_at")
        normalized_values = {}
        for metric, value in self.values.items():
            decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
            if not decimal_value.is_finite():
                raise ValueError(f"{metric.value} must be finite")
            normalized_values[metric] = decimal_value
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "received_at", received_at)
        object.__setattr__(self, "values", MappingProxyType(normalized_values))
        object.__setattr__(self, "raw_payload", MappingProxyType(dict(self.raw_payload)))


@dataclass(frozen=True, slots=True)
class SamplePoint:
    metric: Metric
    value: Decimal
    observed_at: datetime
    received_at: datetime
    source: str
    gap: bool
    out_of_order: bool
