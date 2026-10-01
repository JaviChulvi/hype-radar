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
    interval: Literal["1d"] = Field(alias="i", exclude=True)

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


class CandleFeed:
    def __init__(self, symbol: Symbol = "BTC"):
        self.symbol = symbol
        self.coin = MARKETS[symbol]
        self.candles: dict[int, Candle] = {}
        self.clients: set[asyncio.Queue] = set()
        self.status = "connecting"
        self.last_received_at: float | None = None

    def snapshot(self):
        return {
            "type": "snapshot",
            "symbol": self.symbol,
            "interval": "1d",
            "status": self.status,
            "candles": [c.model_dump(mode="json") for c in self.candles.values()],
        }

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

    def parse_candle(self, data):
        candle = Candle.model_validate(data)
        if candle.coin != self.coin:
            raise ValueError(f"Expected {self.coin} candle, received {candle.coin}")
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
                                        "interval": "1d",
                                    },
                                }
                            )
                        )
                        logger.info(
                            "Subscribed to the shared Hyperliquid %s daily candle feed",
                            self.coin,
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
                                        "interval": "1d",
                                        "startTime": end - HISTORY_SIZE * 86_400_000,
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
