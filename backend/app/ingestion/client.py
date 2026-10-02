"""The only owner of Hyperliquid network I/O."""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class HyperliquidClient:
    def __init__(self, network: str = "mainnet"):
        if network not in ("mainnet", "testnet"):
            raise ValueError("Unsupported Hyperliquid network")
        self.network = network
        host = "api.hyperliquid.xyz" if network == "mainnet" else "api.hyperliquid-testnet.xyz"
        self.http = httpx.AsyncClient(base_url=f"https://{host}", timeout=10)
        self.ws_url = f"wss://{host}/ws"

    async def close(self) -> None:
        await self.http.aclose()

    async def info(self, payload: dict) -> Any:
        response = await self.http.post("/info", json=payload)
        response.raise_for_status()
        return response.json()

    async def contexts(self, dex: str) -> dict[str, dict]:
        meta, contexts = await self.info({"type": "metaAndAssetCtxs", "dex": dex})
        return {asset["name"]: context for asset, context in zip(meta["universe"], contexts, strict=True)}

    async def candles(self, coin: str, interval: str, start: int, end: int) -> list[dict]:
        return await self.info(
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": interval,
                    "startTime": start,
                    "endTime": end,
                },
            }
        )

    async def book(self, coin: str, precision: int | None) -> dict:
        request = {"type": "l2Book", "coin": coin}
        if precision is not None:
            request["nSigFigs"] = precision
        return await self.info(request)

    async def receive(self, socket: ClientConnection, timeout: float = 25, heartbeat: bool = True) -> dict:
        try:
            raw = await asyncio.wait_for(socket.recv(), timeout)
        except TimeoutError:
            if not heartbeat:
                raise
            await socket.send(json.dumps({"method": "ping"}))
            raw = await asyncio.wait_for(socket.recv(), 10)
        message = json.loads(raw)
        if message.get("channel") == "error":
            raise ValueError(message.get("data", "Hyperliquid subscription error"))
        return message

    async def stream(
        self,
        subscription: dict,
        consume: Callable[[ClientConnection], Awaitable[None]],
        interrupted: Callable[[], None],
    ) -> None:
        delay = 1
        while True:
            connected_at = None
            try:
                # websockets retries transient handshakes, including HTTP 503.
                async for socket in connect(self.ws_url, open_timeout=10):
                    await socket.send(json.dumps({"method": "subscribe", "subscription": subscription}))
                    connected_at = time.monotonic()
                    await consume(socket)
            except (OSError, ConnectionClosed, httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as error:
                interrupted()
                if connected_at is not None and time.monotonic() - connected_at >= 30:
                    delay = 1
                logger.warning("%s interrupted; retry in %ss: %s", subscription, delay, error)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
