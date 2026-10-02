from datetime import UTC, datetime
from typing import Protocol

from app.domain.observations import require_aware_utc


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    def __init__(self, current: datetime):
        self._current = require_aware_utc(current, "current")

    def now(self) -> datetime:
        return self._current

    def set(self, current: datetime) -> None:
        self._current = require_aware_utc(current, "current")
