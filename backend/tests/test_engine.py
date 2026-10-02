import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.domain.evaluations import CheckStatus, ConditionState, QualityStatus
from app.domain.events import EventStatus
from app.domain.markets import MarketIdentity
from app.domain.observations import MarketObservation, Metric, OracleRegime
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
from app.engine import ObservationWindowStore, RuleEngine
from app.persistence.serialization import rule_from_dict, rule_to_dict

MARKET = MarketIdentity("mainnet", "", "HYPE")
OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")
RULE_ID = UUID("00000000-0000-0000-0000-000000000002")
VERSION_ID = UUID("00000000-0000-0000-0000-000000000003")
START = datetime(2026, 10, 2, 10, tzinfo=UTC)


def observation(
    at: datetime,
    values: dict[Metric, Decimal | str],
    *,
    received_at: datetime | None = None,
    gap: bool = False,
    out_of_order: bool = False,
    regime: OracleRegime = OracleRegime.UNVERIFIED,
) -> MarketObservation:
    return MarketObservation(
        market=MARKET,
        source="fixture",
        channel="replay",
        observed_at=at,
        received_at=received_at or at,
        values={metric: Decimal(value) for metric, value in values.items()},
        gap=gap,
        out_of_order=out_of_order,
        oracle_regime=regime,
    )


def rule(
    *predicates,
    combinator: Combinator = Combinator.ALL,
    persistence: timedelta = timedelta(0),
    cooldown: timedelta = timedelta(0),
    quality: QualitySettings | None = None,
    policy: QualityPolicy = QualityPolicy.WARN,
) -> RuleVersion:
    return RuleVersion(
        id=VERSION_ID,
        rule_id=RULE_ID,
        owner_id=OWNER_ID,
        version=1,
        name="HYPE deterministic rule",
        market=MARKET,
        predicates=tuple(predicates),
        combinator=combinator,
        persistence=persistence,
        cooldown=cooldown,
        quality=quality or QualitySettings(),
        quality_policy=policy,
        deliveries=(DeliveryTarget("web", str(OWNER_ID)),),
    )


def mark_above(threshold: str = "100", max_age: int = 30) -> ThresholdPredicate:
    return ThresholdPredicate(
        id="mark-above",
        metric=Metric.MARK_PRICE,
        operator=ComparisonOperator.GREATER_THAN_OR_EQUAL,
        threshold=Decimal(threshold),
        max_age=timedelta(seconds=max_age),
    )


class PredicateEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.windows = ObservationWindowStore()
        self.engine = RuleEngine(self.windows)

    def test_exact_threshold_and_stale_data(self):
        alert_rule = rule(mark_above())
        self.windows.add(observation(START, {Metric.MARK_PRICE: "100"}))

        exact = self.engine.evaluate(alert_rule, evaluated_at=START)
        stale = self.engine.evaluate(alert_rule, exact.runtime, evaluated_at=START + timedelta(seconds=31))

        self.assertEqual(exact.result.condition, ConditionState.TRUE)
        self.assertEqual(exact.event.status, EventStatus.CONFIRMED)
        self.assertEqual(stale.result.condition, ConditionState.UNKNOWN)
        self.assertEqual(stale.result.predicates[0].reason_code, "STALE_MARK_PRICE")
        self.assertEqual(stale.event.status, EventStatus.DATA_UNKNOWN)

    def test_open_interest_change_requires_compatible_window_samples(self):
        predicate = OpenInterestChangePredicate(
            id="oi-rise",
            operator=ComparisonOperator.GREATER_THAN,
            threshold=Decimal("10"),
            window=timedelta(minutes=15),
            max_age=timedelta(seconds=30),
            baseline_tolerance=timedelta(minutes=1),
            mode=ChangeMode.PERCENT,
        )
        alert_rule = rule(predicate)
        self.windows.add(observation(START, {Metric.OPEN_INTEREST: "100"}))
        missing = self.engine.evaluate(alert_rule, evaluated_at=START + timedelta(minutes=14))
        self.windows.add(observation(START + timedelta(minutes=15), {Metric.OPEN_INTEREST: "111"}))
        confirmed = self.engine.evaluate(alert_rule, missing.runtime, evaluated_at=START + timedelta(minutes=15))

        self.assertEqual(missing.result.condition, ConditionState.UNKNOWN)
        self.assertEqual(missing.result.predicates[0].reason_code, "STALE_OI")
        self.assertEqual(confirmed.result.condition, ConditionState.TRUE)
        self.assertEqual(confirmed.result.predicates[0].evidence["change"], "11.00")

    def test_all_and_any_use_three_valued_logic(self):
        price = mark_above()
        funding = ThresholdPredicate(
            id="funding-low",
            metric=Metric.FUNDING_RATE,
            operator=ComparisonOperator.LESS_THAN,
            threshold=Decimal("0.01"),
            max_age=timedelta(minutes=1),
        )
        self.windows.add(observation(START, {Metric.MARK_PRICE: "101"}))

        all_result = self.engine.evaluate(rule(price, funding), evaluated_at=START)
        any_result = self.engine.evaluate(rule(price, funding, combinator=Combinator.ANY), evaluated_at=START)

        self.assertEqual(all_result.result.condition, ConditionState.UNKNOWN)
        self.assertEqual(any_result.result.condition, ConditionState.TRUE)


class RuntimeTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.windows = ObservationWindowStore()
        self.engine = RuleEngine(self.windows)

    def test_persistence_episode_and_cooldown(self):
        alert_rule = rule(
            mark_above(),
            persistence=timedelta(seconds=10),
            cooldown=timedelta(seconds=30),
        )
        self.windows.add(observation(START, {Metric.MARK_PRICE: "101"}))
        first = self.engine.evaluate(alert_rule, evaluated_at=START)
        waiting = self.engine.evaluate(alert_rule, first.runtime, evaluated_at=START + timedelta(seconds=9))
        triggered = self.engine.evaluate(alert_rule, waiting.runtime, evaluated_at=START + timedelta(seconds=10))
        duplicate = self.engine.evaluate(alert_rule, triggered.runtime, evaluated_at=START + timedelta(seconds=20))

        self.assertIsNone(first.event)
        self.assertIsNone(waiting.event)
        self.assertEqual(triggered.event.status, EventStatus.CONFIRMED)
        self.assertIsNone(duplicate.event)

        self.windows.add(observation(START + timedelta(seconds=21), {Metric.MARK_PRICE: "99"}))
        reset = self.engine.evaluate(alert_rule, duplicate.runtime, evaluated_at=START + timedelta(seconds=21))
        self.windows.add(observation(START + timedelta(seconds=22), {Metric.MARK_PRICE: "102"}))
        second_episode = self.engine.evaluate(alert_rule, reset.runtime, evaluated_at=START + timedelta(seconds=22))
        cooldown_wait = self.engine.evaluate(
            alert_rule, second_episode.runtime, evaluated_at=START + timedelta(seconds=32)
        )
        after_cooldown = self.engine.evaluate(
            alert_rule, cooldown_wait.runtime, evaluated_at=START + timedelta(seconds=40)
        )

        self.assertIsNone(second_episode.event)
        self.assertIsNone(cooldown_wait.event)
        self.assertIsNotNone(after_cooldown.event)
        self.assertNotEqual(triggered.event.fingerprint, after_cooldown.event.fingerprint)

    def test_unknown_transition_creates_one_data_event(self):
        alert_rule = rule(mark_above(max_age=10))
        self.windows.add(observation(START, {Metric.MARK_PRICE: "99"}))
        false_result = self.engine.evaluate(alert_rule, evaluated_at=START)
        unknown = self.engine.evaluate(alert_rule, false_result.runtime, evaluated_at=START + timedelta(seconds=11))
        repeated = self.engine.evaluate(alert_rule, unknown.runtime, evaluated_at=START + timedelta(seconds=12))

        self.assertEqual(false_result.result.condition, ConditionState.FALSE)
        self.assertEqual(unknown.event.status, EventStatus.DATA_UNKNOWN)
        self.assertIsNone(repeated.event)


class QualityEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.windows = ObservationWindowStore()
        self.engine = RuleEngine(self.windows)

    def test_wide_spread_warns_but_gap_blocks(self):
        settings = QualitySettings(
            stale_after={Metric.MARK_PRICE: timedelta(seconds=30)},
            max_spread_bps=Decimal("10"),
        )
        alert_rule = rule(mark_above(), quality=settings)
        self.windows.add(
            observation(
                START,
                {
                    Metric.MARK_PRICE: "101",
                    Metric.BID_PRICE: "100",
                    Metric.ASK_PRICE: "101",
                },
            )
        )
        warned = self.engine.evaluate(alert_rule, evaluated_at=START)
        self.windows.add(
            observation(
                START + timedelta(seconds=1),
                {Metric.MARK_PRICE: "102"},
                gap=True,
            )
        )
        blocked = self.engine.evaluate(alert_rule, warned.runtime, evaluated_at=START + timedelta(seconds=1))

        self.assertEqual(warned.result.quality, QualityStatus.WARNED)
        self.assertEqual(warned.event.status, EventStatus.WARN)
        self.assertEqual(blocked.result.quality, QualityStatus.BLOCKED)
        self.assertTrue(
            any(check.reason_code == "GAP" and check.status is CheckStatus.BLOCK for check in blocked.result.checks)
        )

    def test_verified_oracle_passes(self):
        settings = QualitySettings(require_verified_oracle=True)
        alert_rule = rule(mark_above(), quality=settings, policy=QualityPolicy.BLOCK)
        self.windows.add(
            observation(
                START,
                {Metric.MARK_PRICE: "101", Metric.ORACLE_PRICE: "100"},
                regime=OracleRegime.EXTERNAL,
            )
        )
        outcome = self.engine.evaluate(alert_rule, evaluated_at=START)

        self.assertEqual(outcome.result.quality, QualityStatus.VALID)
        self.assertEqual(outcome.result.checks[0].reason_code, "ORACLE_MODE_VERIFIED")

    def test_clock_skew_is_reported_as_a_warning(self):
        settings = QualitySettings(max_clock_skew=timedelta(seconds=2))
        alert_rule = rule(mark_above(), quality=settings)
        self.windows.add(
            observation(
                START,
                {Metric.MARK_PRICE: "101"},
                received_at=START + timedelta(seconds=3),
            )
        )

        outcome = self.engine.evaluate(alert_rule, evaluated_at=START + timedelta(seconds=3))

        self.assertEqual(outcome.result.quality, QualityStatus.WARNED)
        self.assertEqual(outcome.result.checks[0].reason_code, "CLOCK_SKEW")

    def test_blocked_episode_can_confirm_after_quality_recovers(self):
        settings = QualitySettings(max_spread_bps=Decimal("10"))
        alert_rule = rule(mark_above(), quality=settings, policy=QualityPolicy.BLOCK)
        self.windows.add(
            observation(
                START,
                {
                    Metric.MARK_PRICE: "101",
                    Metric.BID_PRICE: "100",
                    Metric.ASK_PRICE: "102",
                },
            )
        )
        blocked = self.engine.evaluate(alert_rule, evaluated_at=START)
        self.windows.add(
            observation(
                START + timedelta(seconds=1),
                {
                    Metric.MARK_PRICE: "101",
                    Metric.BID_PRICE: "100.99",
                    Metric.ASK_PRICE: "101.01",
                },
            )
        )
        recovered = self.engine.evaluate(alert_rule, blocked.runtime, evaluated_at=START + timedelta(seconds=1))

        self.assertEqual(blocked.event.status, EventStatus.BLOCKED)
        self.assertFalse(blocked.runtime.episode_triggered)
        self.assertTrue(blocked.runtime.blocked_event_recorded)
        self.assertEqual(recovered.event.status, EventStatus.CONFIRMED)
        self.assertTrue(recovered.runtime.episode_triggered)
        self.assertNotEqual(blocked.event.fingerprint, recovered.event.fingerprint)


class SerializationTests(unittest.TestCase):
    def test_rule_round_trip_preserves_typed_definition(self):
        original = rule(
            mark_above(),
            OpenInterestChangePredicate(
                id="oi-rise",
                operator=ComparisonOperator.GREATER_THAN,
                threshold=Decimal("5"),
                window=timedelta(minutes=15),
                max_age=timedelta(minutes=1),
                baseline_tolerance=timedelta(minutes=2),
            ),
            persistence=timedelta(seconds=20),
            cooldown=timedelta(minutes=5),
            quality=QualitySettings(max_spread_bps=Decimal("12.5")),
        )

        restored = rule_from_dict(rule_to_dict(original))

        self.assertEqual(restored, original)


if __name__ == "__main__":
    unittest.main()
