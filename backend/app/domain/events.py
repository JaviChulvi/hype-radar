from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from .evaluations import ConditionState, QualityStatus
from .markets import MarketIdentity
from .rules import DeliveryTarget


class EventStatus(StrEnum):
    CANDIDATE = "candidate"
    WARN = "warn"
    CONFIRMED = "confirmed"
    BLOCKED = "blocked"
    DATA_UNKNOWN = "data_unknown"


@dataclass(frozen=True, slots=True)
class AlertEventDraft:
    id: UUID
    rule_version_id: UUID
    market: MarketIdentity
    evaluated_at: datetime
    status: EventStatus
    condition: ConditionState
    quality: QualityStatus
    transition: str
    fingerprint: str
    evidence: Mapping[str, Any]
    deliveries: tuple[DeliveryTarget, ...]
