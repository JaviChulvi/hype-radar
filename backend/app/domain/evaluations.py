from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class ConditionState(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class CheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"
    UNKNOWN = "unknown"


class QualityStatus(StrEnum):
    VALID = "valid"
    WARNED = "warned"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class PredicateResult:
    predicate_id: str
    state: ConditionState
    reason_code: str | None
    evidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class QualityCheckResult:
    check: str
    status: CheckStatus
    reason_code: str
    evidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    evaluated_at: datetime
    condition: ConditionState
    quality: QualityStatus
    predicates: tuple[PredicateResult, ...]
    checks: tuple[QualityCheckResult, ...]
