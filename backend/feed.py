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


class Candle(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    time: int = Field(alias="t", ge=0)
    open: Decimal = Field(alias="o", gt=0)
    high: Decimal = Field(alias="h", gt=0)
    low: Decimal = Field(alias="l", gt=0)
    close: Decimal = Field(alias="c", gt=0)
    coin: Literal["BTC"] = Field(alias="s", exclude=True)
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


class BitcoinFeed:
    def __init__(self):
        self.candles: dict[int, Candle] = {}
        self.clients: set[asyncio.Queue] = set()
        self.status = "connecting"
        self.last_received_at: float | None = None

    def snapshot(self):
        return {
            "type": "snapshot",
            "symbol": "BTC",
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
            candle = Candle.model_validate(message["data"])
            self.last_received_at = time.time()
            if self.status != "live":
                pending[candle.time] = candle
                continue
            if self.candles and candle.time < max(self.candles):
                continue
            self.candles[candle.time] = candle
            if len(self.candles) > HISTORY_SIZE:
                del self.candles[min(self.candles)]
            self.broadcast({"type": "candle", "candle": candle.model_dump(mode="json")})

    async def run(self):
        delay = 1
        async with httpx.AsyncClient(timeout=10) as http:
            while True:
                try:
                    async with connect(
                        "wss://api.hyperliquid.xyz/ws", open_timeout=10
                    ) as socket:
                        await socket.send(
                            json.dumps(
                                {
                                    "method": "subscribe",
                                    "subscription": {
                                        "type": "candle",
                                        "coin": "BTC",
                                        "interval": "1d",
                                    },
                                }
                            )
                        )
                        logger.info(
                            "Subscribed to the shared Hyperliquid BTC daily candle feed"
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
                                        "coin": "BTC",
                                        "interval": "1d",
                                        "startTime": end - HISTORY_SIZE * 86_400_000,
                                        "endTime": end,
                                    },
                                },
                            )
                            response.raise_for_status()
                            candles = {
                                c.time: c
                                for c in map(Candle.model_validate, response.json())
                            }
                            candles.update(pending)
                            if not candles:
                                raise ValueError("No BTC history returned")
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
                        "BTC feed interrupted; retrying in %ss: %s", delay, error
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)
