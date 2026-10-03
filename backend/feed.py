"""Channel state and reconciliation; transport and lifecycle belong to the service."""

import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime

from app.domain.market_data import (
    HISTORY_SIZE,
    INTERVAL_MS,
    Book,
    Candle,
    Channel,
    MarketUpdate,
    Trade,
)
from app.domain.markets import MarketIdentity
from app.domain.observations import MarketObservation
from app.ingestion.client import HyperliquidClient
from app.ingestion.hyperliquid import HyperliquidNormalizer


class SharedFeed:
    def __init__(self, client: HyperliquidClient, market: MarketIdentity, channel: Channel):
        self.client = client
        self.market = market
        self.coin = market.exchange_coin
        self.channel = channel
        self.clients: set[asyncio.Queue[MarketUpdate | None]] = set()
        self.status = "connecting"
        self.received_at: datetime | None = None
        self.gap = False
        self.observation: MarketObservation | None = None
        self.subscription: dict = {}

    def snapshot(self, initial: bool = False) -> MarketUpdate:
        return MarketUpdate(self.market, self.channel, self.status, self.data, self.observation, initial)

    def broadcast(self, message: MarketUpdate | None = None) -> None:
        message = message or self.snapshot()
        for queue in tuple(self.clients):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # Retain ownership until its context exits. Overflow is terminal, never silent.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
                self.clients.discard(queue)

    def reset(self) -> None:
        self.gap = True
        self.status = "reconnecting"
        now = datetime.now(UTC)
        self.observation = MarketObservation(
            market=self.market,
            source="hyperliquid",
            channel=self.subscription["type"],
            observed_at=now,
            received_at=now,
            values={},
            gap=True,
        )
        self.broadcast()

    def changed(self, received_at: datetime | None = None) -> None:
        self.received_at = received_at or datetime.now(UTC)
        self.status = "live"
        self.broadcast()
        self.gap = False

    async def run(self) -> None:
        await self.client.stream(self.subscription, self.consume, self.reset)


class CandleFeed(SharedFeed):
    def __init__(self, client: HyperliquidClient, market: MarketIdentity, interval: str = "5m"):
        super().__init__(client, market, "candles")
        self.interval = interval
        self.candles: dict[int, Candle] = {}
        self.subscription = {"type": "candle", "coin": self.coin, "interval": interval}

    @property
    def data(self) -> tuple[Candle, ...]:
        return tuple(self.candles.values())

    def parse_candle(self, data: dict) -> Candle:
        candle = Candle.model_validate(data)
        if candle.coin != self.coin or candle.interval != self.interval:
            raise ValueError("Candle identity does not match subscription")
        return candle

    async def history(self) -> dict[int, Candle]:
        end = int(time.time() * 1000)
        raw = await self.client.candles(self.coin, self.interval, end - HISTORY_SIZE * INTERVAL_MS[self.interval], end)
        candles = {c.time: c for c in map(self.parse_candle, raw)}
        if not candles:
            raise ValueError(f"No {self.coin} candle history")
        return dict(sorted(candles.items())[-HISTORY_SIZE:])

    async def consume(self, socket) -> None:
        pending: dict[int, Candle] = {}
        ready = False

        async def read():
            while True:
                message = await self.client.receive(socket)
                if message.get("channel") != "candle":
                    continue
                candle = self.parse_candle(message["data"])
                target = self.candles if ready else pending
                if ready and target and candle.time < max(target):
                    continue
                previous = target.get(candle.time)
                if previous is not None and candle.trades < previous.trades:
                    continue
                target[candle.time] = candle
                if len(target) > HISTORY_SIZE:
                    del target[min(target)]
                if ready:
                    self.received_at = datetime.now(UTC)
                    self.status = "live"
                    self.broadcast(MarketUpdate(self.market, "candles", "live", candle))

        reader = asyncio.create_task(read())
        try:
            candles = await self.history()
            # REST wins equal trade counts; a newer buffered candle wins otherwise.
            for candle in pending.values():
                previous = candles.get(candle.time)
                if previous is None or candle.trades > previous.trades:
                    candles[candle.time] = candle
            self.candles = dict(sorted(candles.items())[-HISTORY_SIZE:])
            ready = True
            self.changed()
            await reader
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


class OrderBookFeed(SharedFeed):
    def __init__(self, client: HyperliquidClient, market: MarketIdentity, precision: int | None, fast: bool):
        super().__init__(client, market, "book")
        self.precision = precision
        self.fast = fast
        self.book: Book | None = None
        self.normalizer = HyperliquidNormalizer()
        self.subscription = {"type": "l2Book", "coin": self.coin, "fast": fast}
        if precision is not None:
            self.subscription["nSigFigs"] = precision

    @property
    def data(self) -> Book | None:
        return self.book

    def reset(self) -> None:
        self.book = None
        super().reset()

    def apply(self, raw: dict, received_at: datetime) -> None:
        book = Book.model_validate(raw)
        if book.coin != self.coin:
            raise ValueError("Book identity does not match subscription")
        if self.fast:
            book = book.model_copy(update={"levels": tuple(side[:5] for side in book.levels)})
        observation = self.normalizer.order_book(
            self.market,
            [[level.model_dump() for level in side] for side in book.levels],
            datetime.fromtimestamp(book.time / 1000, UTC),
            received_at,
            gap=self.gap,
        )
        if self.book is not None and book.time < self.book.time:
            # Preserve integrity evidence without replacing or refreshing current state.
            self.broadcast(replace(self.snapshot(), observation=replace(observation, out_of_order=True)))
            return
        self.book = book
        self.observation = observation
        self.changed(received_at)

    async def consume(self, socket) -> None:
        deadline = time.monotonic() + 15
        while True:
            message = await self.client.receive(socket, max(0, deadline - time.monotonic()), heartbeat=False)
            if message.get("channel") == "l2Book":
                self.apply(message["data"], datetime.now(UTC))
                deadline = time.monotonic() + 15


class TradeFeed(SharedFeed):
    def __init__(self, client: HyperliquidClient, market: MarketIdentity):
        super().__init__(client, market, "trades")
        self.trades: dict[tuple[int, int], Trade] = {}
        self.subscription = {"type": "trades", "coin": self.coin}

    @property
    def data(self) -> tuple[Trade, ...]:
        return tuple(self.trades.values())

    def reset(self) -> None:
        self.trades.clear()
        super().reset()

    def apply(self, data: list[dict]) -> None:
        trades = dict(self.trades)
        for item in data:
            trade = Trade.model_validate(item)
            if trade.coin != self.coin:
                raise ValueError("Trade identity does not match subscription")
            trades[(trade.time, trade.tid)] = trade
        trades = dict(sorted(trades.items(), reverse=True)[:40])
        if trades != self.trades or self.status != "live":
            self.trades = trades
            self.changed()
        else:
            self.received_at = datetime.now(UTC)

    async def consume(self, socket) -> None:
        while True:
            message = await self.client.receive(socket)
            if message.get("channel") == "trades":
                self.apply(message["data"])


class ContextFeed(SharedFeed):
    def __init__(self, client: HyperliquidClient, market: MarketIdentity):
        super().__init__(client, market, "context")
        self.normalizer = HyperliquidNormalizer()
        self.subscription = {"type": "activeAssetCtx", "coin": self.coin}

    @property
    def data(self) -> MarketObservation | None:
        return self.observation

    def apply(self, payload: dict, received_at: datetime, channel: str = "activeAssetCtx") -> None:
        self.observation = self.normalizer.asset_context(
            self.market,
            payload,
            received_at,
            received_at,
            gap=self.gap,
            channel=channel,
        )
        self.changed(received_at)

    async def consume(self, socket) -> None:
        while True:
            message = await self.client.receive(socket)
            if message.get("channel") == "activeAssetCtx":
                data = message["data"]
                if data["coin"] != self.coin:
                    raise ValueError("Context identity does not match subscription")
                self.apply(data["ctx"], datetime.now(UTC))
