import asyncio
import json
import logging
import time
from decimal import Decimal
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger("uvicorn.error")
HISTORY_SIZE = 200
Interval = Literal["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "3d", "1w", "1M"]
# A 31-day lookback per monthly candle covers calendar months of every length.
INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "8h": 28_800_000, "12h": 43_200_000,
    "1d": 86_400_000, "3d": 259_200_000, "1w": 604_800_000, "1M": 2_678_400_000,
}
Symbol = Literal["BTC", "ETH", "SP500", "XYZ100", "BRENTOIL"]
MARKETS = {
    "BTC": "BTC",
    "ETH": "ETH",
    "SP500": "xyz:SP500",
    "XYZ100": "xyz:XYZ100",
    "BRENTOIL": "xyz:BRENTOIL",
}


class Candle(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    time: int = Field(alias="t", ge=0)
    open: Decimal = Field(alias="o", gt=0)
    high: Decimal = Field(alias="h", gt=0)
    low: Decimal = Field(alias="l", gt=0)
    close: Decimal = Field(alias="c", gt=0)
    trades: int = Field(alias="n", ge=0, exclude=True)
    coin: str = Field(alias="s", exclude=True)
    interval: Interval = Field(alias="i", exclude=True)

    @model_validator(mode="after")
    def validate_range(self):
        if (
            not self.low
            <= min(self.open, self.close)
            <= max(self.open, self.close)
            <= self.high
        ):
            raise ValueError("Inconsistent candle prices")
        return self


class SharedFeed:
    def __init__(self):
        self.clients: set[asyncio.Queue] = set()
        self.status = "connecting"
        self.last_received_at: float | None = None

    def subscribe(self):
        queue = asyncio.Queue(maxsize=32)
        queue.put_nowait(self.snapshot())
        self.clients.add(queue)
        return queue

    def broadcast(self, message):
        for queue in tuple(self.clients):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # Disconnect lagging clients; their reconnect gets a fresh snapshot.
                self.clients.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)

    def set_status(self, status):
        self.status = status
        self.broadcast({"type": "status", "status": status})


class CandleFeed(SharedFeed):
    def __init__(self, symbol: Symbol = "BTC", interval: Interval = "5m"):
        super().__init__()
        self.symbol = symbol
        self.interval = interval
        self.coin = MARKETS[symbol]
        self.candles: dict[int, Candle] = {}

    def snapshot(self):
        return {
            "type": "snapshot",
            "symbol": self.symbol,
            "interval": self.interval,
            "status": self.status,
            "candles": [c.model_dump(mode="json") for c in self.candles.values()],
        }

    def parse_candle(self, data):
        candle = Candle.model_validate(data)
        if candle.coin != self.coin:
            raise ValueError(f"Expected {self.coin} candle, received {candle.coin}")
        if candle.interval != self.interval:
            raise ValueError(f"Expected {self.interval} candle, received {candle.interval}")
        return candle

    async def consume(self, socket, pending):
        while True:
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=25)
            except TimeoutError:
                await socket.send(json.dumps({"method": "ping"}))
                continue
            message = json.loads(raw)
            if message.get("channel") != "candle":
                continue
            candle = self.parse_candle(message["data"])
            self.last_received_at = time.time()
            candles = self.candles if self.status == "live" else pending
            if self.status == "live" and candles and candle.time < max(candles):
                continue
            previous = candles.get(candle.time)
            if previous is not None and candle.trades < previous.trades:
                continue
            candles[candle.time] = candle
            if self.status != "live":
                continue
            if len(self.candles) > HISTORY_SIZE:
                del self.candles[min(self.candles)]
            self.broadcast({"type": "candle", "candle": candle.model_dump(mode="json")})

    async def run(self):
        delay = 1
        async with httpx.AsyncClient(timeout=10) as http:
            while True:
                try:
                    async for socket in connect(
                        "wss://api.hyperliquid.xyz/ws", open_timeout=10
                    ):
                        await socket.send(
                            json.dumps(
                                {
                                    "method": "subscribe",
                                    "subscription": {
                                        "type": "candle",
                                        "coin": self.coin,
                                        "interval": self.interval,
                                    },
                                }
                            )
                        )
                        logger.info(
                            "Subscribed to the shared Hyperliquid %s %s candle feed",
                            self.coin, self.interval,
                        )
                        pending = {}
                        reader = asyncio.create_task(self.consume(socket, pending))
                        try:
                            end = int(time.time() * 1000)
                            response = await http.post(
                                "https://api.hyperliquid.xyz/info",
                                json={
                                    "type": "candleSnapshot",
                                    "req": {
                                        "coin": self.coin,
                                        "interval": self.interval,
                                        "startTime": end - HISTORY_SIZE * INTERVAL_MS[self.interval],
                                        "endTime": end,
                                    },
                                },
                            )
                            response.raise_for_status()
                            candles = {
                                c.time: c
                                for c in map(self.parse_candle, response.json())
                            }
                            # Trade counts order observations within the same candle.
                            # Keep REST on ties so an older buffer cannot undo history.
                            for candle in pending.values():
                                previous = candles.get(candle.time)
                                if previous is None or candle.trades > previous.trades:
                                    candles[candle.time] = candle
                            if not candles:
                                raise ValueError(f"No {self.coin} history returned")
                            self.candles = dict(sorted(candles.items())[-HISTORY_SIZE:])
                            self.status = "live"
                            self.broadcast(self.snapshot())
                            delay = 1
                            await reader
                        finally:
                            reader.cancel()
                            await asyncio.gather(reader, return_exceptions=True)
                except (
                    OSError,
                    ConnectionClosed,
                    httpx.HTTPError,
                    ValidationError,
                    ValueError,
                ) as error:
                    self.set_status("reconnecting")
                    logger.warning(
                        "%s feed interrupted; retrying in %ss: %s", self.coin, delay, error
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)


BookPrecision = Literal["5", "4", "3", "2"]


class BookLevel(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    px: Decimal = Field(gt=0)
    sz: Decimal = Field(gt=0)


class Book(BaseModel):
    coin: str
    time: int = Field(ge=0)
    levels: tuple[list[BookLevel], list[BookLevel]]

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


class OrderBookFeed(SharedFeed):
    def __init__(self, symbol: Symbol = "BTC", precision: BookPrecision = "5"):
        super().__init__()
        self.symbol = symbol
        self.coin = MARKETS[symbol]
        self.precision = precision
        self.book: Book | None = None

    def snapshot(self):
        return {
            "type": "book",
            "symbol": self.symbol,
            "status": self.status,
            "book": self.book.model_dump(mode="json") if self.book else None,
        }

    async def run(self):
        delay = 1
        while True:
            try:
                async for socket in connect(
                    "wss://api.hyperliquid.xyz/ws", open_timeout=10
                ):
                    await socket.send(json.dumps({
                        "method": "subscribe",
                        "subscription": {
                            "type": "l2Book", "coin": self.coin,
                            "nSigFigs": int(self.precision),
                            "fast": True,
                        },
                    }))
                    while True:
                        raw = await asyncio.wait_for(socket.recv(), timeout=15)
                        message = json.loads(raw)
                        if message.get("channel") == "error":
                            raise ValueError(message.get("data"))
                        if message.get("channel") != "l2Book":
                            continue
                        book = Book.model_validate(message["data"])
                        if book.coin != self.coin:
                            raise ValueError(f"Expected {self.coin} book")
                        if self.book is not None and book.time < self.book.time:
                            continue
                        self.book = book
                        self.last_received_at = time.time()
                        self.status = "live"
                        self.broadcast(self.snapshot())
                        delay = 1
            except (OSError, ConnectionClosed, TimeoutError, ValueError, KeyError) as error:
                # Each message is a full snapshot; never show old depth during recovery.
                self.book = None
                self.set_status("reconnecting")
                logger.warning("%s book interrupted; retrying in %ss: %s", self.coin, delay, error)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
