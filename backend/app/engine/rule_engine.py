import hashlib
from dataclasses import dataclass
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from app.domain.evaluations import ConditionState, EvaluationResult, QualityStatus
from app.domain.events import AlertEventDraft, EventStatus
from app.domain.rules import Combinator, RuleVersion

from .clock import Clock, SystemClock
from .predicates import PredicateEvaluator
from .quality import QualityEvaluator
from .windows import ObservationWindowStore


@dataclass(frozen=True, slots=True)
class RuleRuntimeState:
    rule_version_id: UUID
    condition: ConditionState = ConditionState.UNKNOWN
    true_since: datetime | None = None
    last_triggered_at: datetime | None = None
    trigger_sequence: int = 0
    episode_triggered: bool = False
    blocked_event_recorded: bool = False
    last_processed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    result: EvaluationResult
    runtime: RuleRuntimeState
    event: AlertEventDraft | None


class RuleEngine:
    def __init__(
        self,
        windows: ObservationWindowStore,
        clock: Clock | None = None,
    ):
        self._windows = windows
        self._clock = clock or SystemClock()
        self._predicates = PredicateEvaluator(windows)
        self._quality = QualityEvaluator(windows)

    def evaluate(
        self,
        rule: RuleVersion,
        runtime: RuleRuntimeState | None = None,
        evaluated_at: datetime | None = None,
    ) -> EvaluationOutcome:
        now = evaluated_at or self._clock.now()
        runtime = runtime or RuleRuntimeState(rule.id)
        if runtime.rule_version_id != rule.id:
            raise ValueError("Runtime state belongs to a different rule version")
        if runtime.last_processed_at is not None and now < runtime.last_processed_at:
            raise ValueError("Evaluation time must not move backwards")

        predicate_results = tuple(self._predicates.evaluate(rule, predicate, now) for predicate in rule.predicates)
        condition = self._combine(rule.combinator, predicate_results)
        quality, checks = self._quality.evaluate(rule, now)
        result = EvaluationResult(now, condition, quality, predicate_results, checks)
        updated_runtime, event = self._transition(rule, runtime, result)
        return EvaluationOutcome(result, updated_runtime, event)

    @staticmethod
    def _combine(combinator: Combinator, results) -> ConditionState:
        states = [result.state for result in results]
        if combinator is Combinator.ALL:
            if ConditionState.FALSE in states:
                return ConditionState.FALSE
            if ConditionState.UNKNOWN in states:
                return ConditionState.UNKNOWN
            return ConditionState.TRUE
        if ConditionState.TRUE in states:
            return ConditionState.TRUE
        if ConditionState.UNKNOWN in states:
            return ConditionState.UNKNOWN
        return ConditionState.FALSE

    def _transition(
        self,
        rule: RuleVersion,
        runtime: RuleRuntimeState,
        result: EvaluationResult,
    ) -> tuple[RuleRuntimeState, AlertEventDraft | None]:
        now = result.evaluated_at
        event = None
        true_since = runtime.true_since
        episode_triggered = runtime.episode_triggered
        blocked_event_recorded = runtime.blocked_event_recorded
        last_triggered_at = runtime.last_triggered_at
        trigger_sequence = runtime.trigger_sequence

        if result.condition is ConditionState.TRUE:
            if runtime.condition is not ConditionState.TRUE or true_since is None:
                true_since = now
                episode_triggered = False
                blocked_event_recorded = False
            persistence_met = now - true_since >= rule.persistence
            cooldown_met = last_triggered_at is None or now - last_triggered_at >= rule.cooldown
            if persistence_met and cooldown_met and not episode_triggered:
                if result.quality is QualityStatus.BLOCKED:
                    if not blocked_event_recorded:
                        trigger_sequence += 1
                        event = self._build_event(
                            rule,
                            runtime.condition,
                            result,
                            true_since,
                            trigger_sequence,
                        )
                        blocked_event_recorded = True
                else:
                    trigger_sequence += 1
                    event = self._build_event(
                        rule,
                        runtime.condition,
                        result,
                        true_since,
                        trigger_sequence,
                    )
                    episode_triggered = True
                    last_triggered_at = now
        else:
            true_since = None
            episode_triggered = False
            blocked_event_recorded = False
            if result.condition is ConditionState.UNKNOWN and runtime.condition is not ConditionState.UNKNOWN:
                trigger_sequence += 1
                event = self._build_unknown_event(rule, runtime.condition, result, trigger_sequence)

        updated_runtime = RuleRuntimeState(
            rule_version_id=rule.id,
            condition=result.condition,
            true_since=true_since,
            last_triggered_at=last_triggered_at,
            trigger_sequence=trigger_sequence,
            episode_triggered=episode_triggered,
            blocked_event_recorded=blocked_event_recorded,
            last_processed_at=now,
        )
        return updated_runtime, event

    def _build_event(
        self,
        rule: RuleVersion,
        previous: ConditionState,
        result: EvaluationResult,
        true_since: datetime,
        sequence: int,
    ) -> AlertEventDraft:
        status = {
            QualityStatus.VALID: EventStatus.CONFIRMED,
            QualityStatus.WARNED: EventStatus.WARN,
            QualityStatus.BLOCKED: EventStatus.BLOCKED,
        }[result.quality]
        fingerprint = self._fingerprint(rule.id, true_since, sequence, status)
        return AlertEventDraft(
            id=uuid5(NAMESPACE_URL, fingerprint),
            rule_version_id=rule.id,
            market=rule.market,
            evaluated_at=result.evaluated_at,
            status=status,
            condition=result.condition,
            quality=result.quality,
            transition=f"{previous.value}->{result.condition.value}",
            fingerprint=fingerprint,
            evidence=self._evidence(rule, result, true_since),
            deliveries=() if status is EventStatus.BLOCKED else rule.deliveries,
        )

    def _build_unknown_event(
        self,
        rule: RuleVersion,
        previous: ConditionState,
        result: EvaluationResult,
        sequence: int,
    ) -> AlertEventDraft:
        fingerprint = self._fingerprint(rule.id, result.evaluated_at, sequence, EventStatus.DATA_UNKNOWN)
        return AlertEventDraft(
            id=uuid5(NAMESPACE_URL, fingerprint),
            rule_version_id=rule.id,
            market=rule.market,
            evaluated_at=result.evaluated_at,
            status=EventStatus.DATA_UNKNOWN,
            condition=result.condition,
            quality=result.quality,
            transition=f"{previous.value}->{result.condition.value}",
            fingerprint=fingerprint,
            evidence=self._evidence(rule, result, None),
            deliveries=(),
        )

    @staticmethod
    def _fingerprint(
        rule_version_id: UUID,
        episode_at: datetime,
        sequence: int,
        status: EventStatus,
    ) -> str:
        value = f"{rule_version_id}:{episode_at.isoformat()}:{sequence}:{status.value}"
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _evidence(
        rule: RuleVersion,
        result: EvaluationResult,
        true_since: datetime | None,
    ) -> dict:
        return {
            "schema_version": 1,
            "rule_version_id": str(rule.id),
            "rule_version": rule.version,
            "market": rule.market.key,
            "evaluated_at": result.evaluated_at.isoformat(),
            "true_since": None if true_since is None else true_since.isoformat(),
            "condition": result.condition.value,
            "quality": result.quality.value,
            "predicates": [
                {
                    "id": item.predicate_id,
                    "state": item.state.value,
                    "reason_code": item.reason_code,
                    "evidence": dict(item.evidence),
                }
                for item in result.predicates
            ],
            "checks": [
                {
                    "check": item.check,
                    "status": item.status.value,
                    "reason_code": item.reason_code,
                    "evidence": dict(item.evidence),
                }
                for item in result.checks
            ],
        }
