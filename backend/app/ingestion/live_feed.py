import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.domain.markets import MarketIdentity
from app.domain.observations import MarketObservation

from .hyperliquid import HyperliquidNormalizer

logger = logging.getLogger(__name__)


class HyperliquidObservationFeed:
    def __init__(
        self,
        market: MarketIdentity,
        publish: Callable[[MarketObservation], Awaitable[None]],
        normalizer: HyperliquidNormalizer | None = None,
    ):
        self.market = market
        self.coin = f"{market.dex}:{market.coin}" if market.dex else market.coin
        self._publish = publish
        self._normalizer = normalizer or HyperliquidNormalizer()

    async def run(self) -> None:
        delay = 1
        gap_pending = False
        while True:
            try:
                async for socket in connect("wss://api.hyperliquid.xyz/ws", open_timeout=10):
                    for subscription in (
                        {"type": "activeAssetCtx", "coin": self.coin},
                        {"type": "l2Book", "coin": self.coin},
                    ):
                        await socket.send(json.dumps({"method": "subscribe", "subscription": subscription}))
                    logger.info("Subscribed to normalized %s observations", self.coin)
                    while True:
                        try:
                            raw = await asyncio.wait_for(socket.recv(), timeout=25)
                        except TimeoutError:
                            await socket.send(json.dumps({"method": "ping"}))
                            continue
                        received_at = datetime.now(UTC)
                        observation = self.decode(json.loads(raw), received_at, gap_pending)
                        if observation is None:
                            continue
                        await self._publish(observation)
                        gap_pending = False
                        delay = 1
            except (OSError, ConnectionClosed, TimeoutError, ValueError, KeyError) as error:
                gap_pending = True
                logger.warning(
                    "%s normalized feed interrupted; retrying in %ss: %s",
                    self.coin,
                    delay,
                    error,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    def decode(
        self,
        message: dict[str, Any],
        received_at: datetime,
        gap: bool = False,
    ) -> MarketObservation | None:
        channel = message.get("channel")
        if channel in {"subscriptionResponse", "pong"}:
            return None
        if channel == "error":
            raise ValueError(message.get("data", "Hyperliquid subscription error"))
        data = message.get("data")
        if not isinstance(data, dict) or data.get("coin") != self.coin:
            if channel in {"activeAssetCtx", "l2Book"}:
                raise ValueError(f"Expected {self.coin} {channel} payload")
            return None
        if channel == "activeAssetCtx":
            # This channel has no source timestamp, so reception time is explicit evidence.
            return self._normalizer.asset_context(
                self.market,
                data["ctx"],
                observed_at=received_at,
                received_at=received_at,
                gap=gap,
            )
        if channel == "l2Book":
            observed_at = datetime.fromtimestamp(data["time"] / 1000, UTC)
            return self._normalizer.order_book(
                self.market,
                data["levels"],
                observed_at=observed_at,
                received_at=received_at,
                gap=gap,
            )
        return None
