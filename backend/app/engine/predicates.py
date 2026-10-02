from datetime import datetime, timedelta
from decimal import Decimal

from app.domain.evaluations import ConditionState, PredicateResult
from app.domain.observations import Metric
from app.domain.rules import (
    ChangeMode,
    ComparisonOperator,
    OpenInterestChangePredicate,
    Predicate,
    RuleVersion,
    ThresholdPredicate,
)

from .windows import ObservationWindowStore


def compare(left: Decimal, operator: ComparisonOperator, right: Decimal) -> bool:
    match operator:
        case ComparisonOperator.GREATER_THAN:
            return left > right
        case ComparisonOperator.GREATER_THAN_OR_EQUAL:
            return left >= right
        case ComparisonOperator.LESS_THAN:
            return left < right
        case ComparisonOperator.LESS_THAN_OR_EQUAL:
            return left <= right
    raise ValueError(f"Unsupported comparison operator: {operator}")


class PredicateEvaluator:
    def __init__(self, windows: ObservationWindowStore):
        self._windows = windows

    def evaluate(self, rule: RuleVersion, predicate: Predicate, evaluated_at: datetime) -> PredicateResult:
        if isinstance(predicate, ThresholdPredicate):
            return self._evaluate_threshold(rule, predicate, evaluated_at)
        if isinstance(predicate, OpenInterestChangePredicate):
            return self._evaluate_oi_change(rule, predicate, evaluated_at)
        raise TypeError(f"Unsupported predicate type: {type(predicate).__name__}")

    def _evaluate_threshold(
        self, rule: RuleVersion, predicate: ThresholdPredicate, evaluated_at: datetime
    ) -> PredicateResult:
        point = self._windows.latest(rule.market, predicate.metric, evaluated_at)
        if point is None:
            return PredicateResult(
                predicate.id,
                ConditionState.UNKNOWN,
                f"MISSING_{predicate.metric.value.upper()}",
                {"metric": predicate.metric.value},
            )
        age = evaluated_at - point.observed_at
        evidence = {
            "metric": predicate.metric.value,
            "value": str(point.value),
            "threshold": str(predicate.threshold),
            "operator": predicate.operator.value,
            "observed_at": point.observed_at.isoformat(),
            "age_seconds": age.total_seconds(),
            "max_age_seconds": predicate.max_age.total_seconds(),
        }
        if age < timedelta(0) or age > predicate.max_age:
            return PredicateResult(
                predicate.id,
                ConditionState.UNKNOWN,
                f"STALE_{predicate.metric.value.upper()}",
                evidence,
            )
        state = (
            ConditionState.TRUE
            if compare(point.value, predicate.operator, predicate.threshold)
            else ConditionState.FALSE
        )
        return PredicateResult(predicate.id, state, None, evidence)

    def _evaluate_oi_change(
        self,
        rule: RuleVersion,
        predicate: OpenInterestChangePredicate,
        evaluated_at: datetime,
    ) -> PredicateResult:
        current = self._windows.latest(rule.market, Metric.OPEN_INTEREST, evaluated_at)
        if current is None:
            return PredicateResult(
                predicate.id,
                ConditionState.UNKNOWN,
                "OI_UNCONFIRMED",
                {"metric": Metric.OPEN_INTEREST.value},
            )
        current_age = evaluated_at - current.observed_at
        cutoff = evaluated_at - predicate.window
        baseline = self._windows.at_or_before(rule.market, Metric.OPEN_INTEREST, cutoff)
        evidence = {
            "metric": Metric.OPEN_INTEREST.value,
            "current": str(current.value),
            "current_observed_at": current.observed_at.isoformat(),
            "window_seconds": predicate.window.total_seconds(),
            "mode": predicate.mode.value,
            "operator": predicate.operator.value,
            "threshold": str(predicate.threshold),
        }
        if current_age < timedelta(0) or current_age > predicate.max_age:
            return PredicateResult(predicate.id, ConditionState.UNKNOWN, "STALE_OI", evidence)
        if baseline is None or cutoff - baseline.observed_at > predicate.baseline_tolerance:
            return PredicateResult(predicate.id, ConditionState.UNKNOWN, "OI_UNCONFIRMED", evidence)
        evidence = {
            **evidence,
            "baseline": str(baseline.value),
            "baseline_observed_at": baseline.observed_at.isoformat(),
        }
        if predicate.mode is ChangeMode.PERCENT:
            if baseline.value == 0:
                return PredicateResult(predicate.id, ConditionState.UNKNOWN, "OI_ZERO_BASELINE", evidence)
            change = (current.value / baseline.value - Decimal(1)) * Decimal(100)
        else:
            change = current.value - baseline.value
        evidence = {**evidence, "change": str(change)}
        state = (
            ConditionState.TRUE if compare(change, predicate.operator, predicate.threshold) else ConditionState.FALSE
        )
        return PredicateResult(predicate.id, state, None, evidence)
