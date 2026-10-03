"""Typed market data returned to API and agent consumers."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .markets import MarketIdentity
from .observations import MarketObservation

Interval = Literal["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "3d", "1w", "1M"]
# A 31-day lookback per monthly candle covers calendar months of every length.
INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
    "1M": 2_678_400_000,
}

HISTORY_SIZE = 200
BookPrecision = Literal["5", "4", "3", "2"]


class Candle(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, frozen=True)

    time: int = Field(validation_alias="t", ge=0)
    open: Decimal = Field(validation_alias="o", gt=0)
    high: Decimal = Field(validation_alias="h", gt=0)
    low: Decimal = Field(validation_alias="l", gt=0)
    close: Decimal = Field(validation_alias="c", gt=0)
    trades: int = Field(validation_alias="n", ge=0, exclude=True)
    coin: str = Field(validation_alias="s", exclude=True)
    interval: Interval = Field(validation_alias="i", exclude=True)

    @model_validator(mode="after")
    def validate_range(self):
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("Inconsistent candle prices")
        return self


class BookLevel(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, frozen=True)

    px: Decimal = Field(gt=0)
    sz: Decimal = Field(gt=0)


class Trade(BookLevel):
    coin: str = Field(exclude=True)
    side: Literal["B", "A"]
    time: int = Field(ge=0)
    tid: int = Field(ge=0)


class Book(BaseModel):
    model_config = ConfigDict(frozen=True)

    coin: str
    time: int = Field(ge=0)
    levels: tuple[tuple[BookLevel, ...], tuple[BookLevel, ...]]

    @model_validator(mode="after")
    def validate_levels(self):
        bids, asks = self.levels
        for levels, reverse in ((bids, True), (asks, False)):
            prices = [level.px for level in levels]
            if prices != sorted(set(prices), reverse=reverse):
                raise ValueError("Unordered or duplicate book levels")
        if bids and asks and bids[0].px >= asks[0].px:
            raise ValueError("Crossed order book")
        return self


class MarketField(BaseModel):
    value: Decimal | None = None
    source: str = "hyperliquid"
    channel: str | None = None
    source_at: datetime | None = None
    received_at: datetime | None = None
    timestamp_basis: Literal["exchange", "reception"] | None = None
    status: Literal["fresh", "stale", "missing"] = "missing"


class BookDepth(BaseModel):
    precision: int | None
    fast: bool
    bid_levels: int
    ask_levels: int
    unit: str = "quote_notional"


class MarketSnapshot(BaseModel):
    market: MarketIdentity
    assembled_at: datetime
    fields: dict[str, MarketField]
    depth: BookDepth | None = None


Channel = Literal["context", "book", "trades", "candles"]
FeedStatus = Literal["connecting", "live", "reconnecting"]


@dataclass(frozen=True, slots=True)
class MarketUpdate:
    market: MarketIdentity
    channel: Channel
    status: FeedStatus
    data: MarketObservation | Book | Candle | tuple[Candle, ...] | tuple[Trade, ...] | None
    observation: MarketObservation | None = None
    initial: bool = False
