from datetime import datetime, timedelta
from decimal import Decimal

from app.domain.evaluations import CheckStatus, QualityCheckResult, QualityStatus
from app.domain.observations import Metric, OracleRegime
from app.domain.rules import QualityPolicy, RuleVersion

from .windows import ObservationWindowStore


class QualityEvaluator:
    def __init__(self, windows: ObservationWindowStore):
        self._windows = windows

    def evaluate(
        self, rule: RuleVersion, evaluated_at: datetime
    ) -> tuple[QualityStatus, tuple[QualityCheckResult, ...]]:
        checks: list[QualityCheckResult] = []
        for metric, max_age in rule.quality.stale_after.items():
            point = self._windows.latest(rule.market, metric, evaluated_at)
            age = None if point is None else evaluated_at - point.observed_at
            if point is None or age is None or age < timedelta(0) or age > max_age:
                checks.append(
                    QualityCheckResult(
                        check="freshness",
                        status=CheckStatus.BLOCK,
                        reason_code=f"STALE_{metric.value.upper()}",
                        evidence={
                            "metric": metric.value,
                            "age_seconds": None if age is None else age.total_seconds(),
                            "max_age_seconds": max_age.total_seconds(),
                        },
                    )
                )
            else:
                checks.append(
                    QualityCheckResult(
                        check="freshness",
                        status=CheckStatus.PASS,
                        reason_code="FRESH_DATA",
                        evidence={"metric": metric.value, "age_seconds": age.total_seconds()},
                    )
                )

        horizon = self._integrity_horizon(rule)
        since = evaluated_at - horizon
        if self._windows.has_gap_since(rule.market, since, evaluated_at):
            checks.append(QualityCheckResult("integrity", CheckStatus.BLOCK, "GAP", {}))
        if self._windows.has_out_of_order_since(rule.market, since, evaluated_at):
            checks.append(QualityCheckResult("integrity", CheckStatus.WARN, "OUT_OF_ORDER", {}))
        if rule.quality.max_clock_skew is not None:
            checks.append(self._clock_skew_check(rule, evaluated_at))

        if rule.quality.max_spread_bps is not None:
            checks.append(self._spread_check(rule, evaluated_at))
        if rule.quality.min_bid_depth is not None:
            checks.append(
                self._depth_check(
                    rule,
                    evaluated_at,
                    Metric.BID_DEPTH,
                    rule.quality.min_bid_depth,
                )
            )
        if rule.quality.min_ask_depth is not None:
            checks.append(
                self._depth_check(
                    rule,
                    evaluated_at,
                    Metric.ASK_DEPTH,
                    rule.quality.min_ask_depth,
                )
            )
        if rule.quality.require_verified_oracle:
            checks.append(self._oracle_check(rule, evaluated_at))

        if any(check.status is CheckStatus.BLOCK for check in checks):
            quality = QualityStatus.BLOCKED
        elif any(check.status in {CheckStatus.UNKNOWN, CheckStatus.WARN} for check in checks):
            quality = QualityStatus.BLOCKED if rule.quality_policy is QualityPolicy.BLOCK else QualityStatus.WARNED
        else:
            quality = QualityStatus.VALID
        return quality, tuple(checks)

    def _spread_check(self, rule: RuleVersion, evaluated_at: datetime) -> QualityCheckResult:
        bid = self._windows.latest(rule.market, Metric.BID_PRICE, evaluated_at)
        ask = self._windows.latest(rule.market, Metric.ASK_PRICE, evaluated_at)
        if bid is None or ask is None or bid.value <= 0 or ask.value <= bid.value:
            return QualityCheckResult("spread", CheckStatus.UNKNOWN, "STALE_BOOK", {})
        mid = (bid.value + ask.value) / Decimal(2)
        spread_bps = Decimal(10_000) * (ask.value - bid.value) / mid
        status = CheckStatus.WARN if spread_bps > rule.quality.max_spread_bps else CheckStatus.PASS
        return QualityCheckResult(
            "spread",
            status,
            "WIDE_SPREAD" if status is CheckStatus.WARN else "SPREAD_WITHIN_LIMIT",
            {
                "bid": str(bid.value),
                "ask": str(ask.value),
                "spread_bps": str(spread_bps),
                "max_spread_bps": str(rule.quality.max_spread_bps),
            },
        )

    def _depth_check(
        self,
        rule: RuleVersion,
        evaluated_at: datetime,
        metric: Metric,
        minimum: Decimal,
    ) -> QualityCheckResult:
        point = self._windows.latest(rule.market, metric, evaluated_at)
        if point is None:
            return QualityCheckResult("depth", CheckStatus.UNKNOWN, "THIN_DEPTH", {"metric": metric.value})
        status = CheckStatus.WARN if point.value < minimum else CheckStatus.PASS
        return QualityCheckResult(
            "depth",
            status,
            "THIN_DEPTH" if status is CheckStatus.WARN else "DEPTH_SUFFICIENT",
            {"metric": metric.value, "value": str(point.value), "minimum": str(minimum)},
        )

    def _oracle_check(self, rule: RuleVersion, evaluated_at: datetime) -> QualityCheckResult:
        regime = self._windows.latest_oracle_regime(rule.market, evaluated_at)
        if regime is None or regime is OracleRegime.UNVERIFIED:
            return QualityCheckResult("oracle_regime", CheckStatus.UNKNOWN, "ORACLE_MODE_UNKNOWN", {})
        return QualityCheckResult(
            "oracle_regime",
            CheckStatus.PASS,
            "ORACLE_MODE_VERIFIED",
            {"oracle_regime": regime.value},
        )

    def _clock_skew_check(self, rule: RuleVersion, evaluated_at: datetime) -> QualityCheckResult:
        marker = self._windows.latest_integrity_marker(rule.market, evaluated_at)
        if marker is None:
            return QualityCheckResult("clock_skew", CheckStatus.UNKNOWN, "CLOCK_SKEW_UNKNOWN", {})
        skew = abs(marker.received_at - marker.observed_at)
        status = CheckStatus.WARN if skew > rule.quality.max_clock_skew else CheckStatus.PASS
        return QualityCheckResult(
            "clock_skew",
            status,
            "CLOCK_SKEW" if status is CheckStatus.WARN else "CLOCK_SKEW_WITHIN_LIMIT",
            {
                "skew_seconds": skew.total_seconds(),
                "max_skew_seconds": rule.quality.max_clock_skew.total_seconds(),
            },
        )

    @staticmethod
    def _integrity_horizon(rule: RuleVersion) -> timedelta:
        horizons = [timedelta(minutes=1), *rule.quality.stale_after.values()]
        horizons.extend(predicate.window for predicate in rule.predicates if hasattr(predicate, "window"))
        return max(horizons)
