from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.markets import MarketIdentity
from app.domain.observations import MarketObservation, Metric, OracleRegime, SamplePoint


@dataclass(frozen=True, slots=True)
class IntegrityMarker:
    observed_at: datetime
    received_at: datetime
    gap: bool
    out_of_order: bool
    oracle_regime: OracleRegime


@dataclass(frozen=True, slots=True)
class WindowUpdate:
    out_of_order: bool
    duplicate_points: int


class ObservationWindowStore:
    def __init__(self, retention: timedelta = timedelta(days=31)):
        if retention <= timedelta(0):
            raise ValueError("Window retention must be positive")
        self._retention = retention
        self._series: dict[tuple[MarketIdentity, Metric], list[SamplePoint]] = defaultdict(list)
        self._markers: dict[MarketIdentity, list[IntegrityMarker]] = defaultdict(list)

    def add(self, observation: MarketObservation) -> WindowUpdate:
        duplicate_points = 0
        detected_out_of_order = observation.out_of_order
        for metric, value in observation.values.items():
            key = (observation.market, metric)
            series = self._series[key]
            if series and observation.observed_at < series[-1].observed_at:
                detected_out_of_order = True
            point = SamplePoint(
                metric=metric,
                value=value,
                observed_at=observation.observed_at,
                received_at=observation.received_at,
                source=observation.source,
                gap=observation.gap,
                out_of_order=detected_out_of_order,
            )
            timestamps = [item.observed_at for item in series]
            index = bisect_right(timestamps, point.observed_at)
            replaced = False
            for candidate_index in range(index - 1, -1, -1):
                candidate = series[candidate_index]
                if candidate.observed_at != point.observed_at:
                    break
                if candidate.source == point.source:
                    series[candidate_index] = point
                    duplicate_points += 1
                    replaced = True
                    break
            if not replaced:
                series.insert(index, point)
            self._trim_series(series, observation.received_at - self._retention)

        markers = self._markers[observation.market]
        markers.append(
            IntegrityMarker(
                observed_at=observation.observed_at,
                received_at=observation.received_at,
                gap=observation.gap,
                out_of_order=detected_out_of_order,
                oracle_regime=observation.oracle_regime,
            )
        )
        cutoff = observation.received_at - self._retention
        self._markers[observation.market] = [marker for marker in markers if marker.received_at >= cutoff]
        return WindowUpdate(detected_out_of_order, duplicate_points)

    def latest(self, market: MarketIdentity, metric: Metric, at: datetime) -> SamplePoint | None:
        series = self._series.get((market, metric), ())
        index = bisect_right([item.observed_at for item in series], at)
        return series[index - 1] if index else None

    def at_or_before(self, market: MarketIdentity, metric: Metric, at: datetime) -> SamplePoint | None:
        return self.latest(market, metric, at)

    def has_gap_since(self, market: MarketIdentity, since: datetime, at: datetime) -> bool:
        return any(marker.gap and since <= marker.received_at <= at for marker in self._markers.get(market, ()))

    def has_out_of_order_since(self, market: MarketIdentity, since: datetime, at: datetime) -> bool:
        return any(
            marker.out_of_order and since <= marker.received_at <= at for marker in self._markers.get(market, ())
        )

    def latest_oracle_regime(self, market: MarketIdentity, at: datetime) -> OracleRegime | None:
        marker = self.latest_integrity_marker(market, at)
        return marker.oracle_regime if marker else None

    def latest_integrity_marker(self, market: MarketIdentity, at: datetime) -> IntegrityMarker | None:
        eligible = [marker for marker in self._markers.get(market, ()) if marker.received_at <= at]
        return eligible[-1] if eligible else None

    def _trim_series(self, series: list[SamplePoint], cutoff: datetime) -> None:
        series[:] = [point for point in series if point.received_at >= cutoff]
