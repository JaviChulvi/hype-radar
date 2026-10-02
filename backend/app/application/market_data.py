"""Shared, read-only market data for HTTP, WebSockets, and the evaluator."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Self

import httpx

from app.domain.market_data import (
    INTERVAL_MS,
    Book,
    BookDepth,
    Candle,
    Channel,
    MarketField,
    MarketSnapshot,
    MarketUpdate,
    Trade,
)
from app.domain.markets import MARKETS, MarketIdentity, market_symbol, resolve_market
from app.ingestion.client import HyperliquidClient
from feed import CandleFeed, ContextFeed, OrderBookFeed, SharedFeed, TradeFeed

logger = logging.getLogger(__name__)
READ_ERRORS = (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError)


class MarketUnavailable(Exception):
    """No usable upstream data before the read deadline."""


class SlowConsumer(MarketUnavailable):
    """A subscriber lost continuity and must stop or reconnect."""


class MarketDataService:
    def __init__(self, client: HyperliquidClient):
        self.client = client
        self._feeds: dict[str, SharedFeed] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._owners: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._contexts_at: dict[str, float] = {}
        self._closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args):
        await self.close()

    def resolve(self, market: str | MarketIdentity) -> MarketIdentity:
        return resolve_market(market, self.client.network)

    async def list_markets(self) -> list[MarketIdentity]:
        return [self.resolve(symbol) for symbol in MARKETS]

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        queues = {queue for feed in self._feeds.values() for queue in feed.clients}
        for queue in queues:
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(None)
        self._feeds.clear()
        await self.client.close()

    def _feed(self, market, channel, interval="5m", precision=None, fast=False) -> tuple[str, SharedFeed]:
        if self._closed:
            raise RuntimeError("Market data service is closed")
        identity = self.resolve(market)
        if interval not in INTERVAL_MS or precision not in (None, 2, 3, 4, 5):
            raise ValueError("Unsupported candle interval or book precision")
        symbol = market_symbol(identity)
        if channel == "candles":
            key = f"{symbol}:{interval}"
        elif channel == "book":
            key = f"book:{symbol}:{precision if precision is not None else 'full'}:{'fast' if fast else 'normal'}"
        elif channel in ("context", "trades"):
            key = f"{channel}:{symbol}"
        else:
            raise ValueError(f"Unsupported channel: {channel}")
        if key not in self._feeds:
            if channel == "candles":
                feed = CandleFeed(self.client, identity, interval)
            elif channel == "book":
                feed = OrderBookFeed(self.client, identity, precision, fast)
            elif channel == "trades":
                feed = TradeFeed(self.client, identity)
            else:
                feed = ContextFeed(self.client, identity)
            self._feeds[key] = feed
        return key, self._feeds[key]

    async def _run(self, feed: SharedFeed) -> None:
        bootstrap = None
        if isinstance(feed, ContextFeed):
            bootstrap = asyncio.create_task(self._refresh_contexts(feed.market.dex))
        try:
            await feed.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            feed.reset()
            logger.exception("Market feed failed: %s %s", feed.market.key, feed.channel)
            for queue in tuple(feed.clients):
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
        finally:
            if bootstrap is not None:
                bootstrap.cancel()
                await asyncio.gather(bootstrap, return_exceptions=True)

    @asynccontextmanager
    async def subscribe(
        self,
        market: str | MarketIdentity,
        channels: tuple[Channel, ...] = ("context", "book"),
        *,
        interval: str = "5m",
        precision: int | None = None,
        fast: bool = False,
    ) -> AsyncIterator[AsyncIterator[MarketUpdate]]:
        if not channels or len(set(channels)) != len(channels):
            raise ValueError("Choose distinct, nonempty channels")
        entries = [self._feed(market, channel, interval, precision, fast) for channel in channels]
        queue: asyncio.Queue[MarketUpdate | None] = asyncio.Queue(maxsize=32)
        for key, feed in entries:
            if key not in self._tasks and feed.received_at is not None:
                feed.reset()
            self._owners[key] = self._owners.get(key, 0) + 1
            feed.clients.add(queue)
            queue.put_nowait(feed.snapshot(initial=True))
            if key not in self._tasks:
                self._tasks[key] = asyncio.create_task(self._run(feed))

        async def updates():
            while True:
                update = await queue.get()
                if update is None:
                    raise SlowConsumer("Market subscription lost continuity")
                yield update

        try:
            yield updates()
        finally:
            stopped = []
            for key, feed in entries:
                feed.clients.discard(queue)
                self._owners[key] -= 1
                if not self._owners[key]:
                    del self._owners[key]
                    task = self._tasks.pop(key, None)
                    if task is not None:
                        task.cancel()
                        stopped.append(task)
            await asyncio.gather(*stopped, return_exceptions=True)

    @staticmethod
    def _fresh(feed: SharedFeed, seconds: float) -> bool:
        if feed.status != "live" or feed.received_at is None:
            return False
        at = feed.received_at
        if isinstance(feed, OrderBookFeed) and feed.book is not None:
            at = datetime.fromtimestamp(feed.book.time / 1000, UTC)
        return 0 <= (datetime.now(UTC) - at).total_seconds() <= seconds

    async def _refresh_contexts(self, dex: str) -> None:
        async with self._locks.setdefault(f"dex:{dex}", asyncio.Lock()):
            if time.monotonic() - self._contexts_at.get(dex, float("-inf")) < 10:
                return
            started = datetime.now(UTC)
            try:
                contexts = await self.client.contexts(dex)
            except READ_ERRORS:
                for feed in self._feeds.values():
                    if isinstance(feed, ContextFeed) and feed.market.dex == dex:
                        if feed.received_at is None or feed.received_at < started:
                            feed.status = "reconnecting"
                logger.warning("Context snapshot unavailable for dex=%s", dex, exc_info=True)
                return
            received = datetime.now(UTC)
            for identity in await self.list_markets():
                if identity.dex != dex:
                    continue
                _, feed = self._feed(identity, "context")
                # A context arriving live during the REST request takes precedence.
                if feed.received_at is not None and feed.received_at >= started:
                    continue
                try:
                    feed.apply(contexts[identity.exchange_coin], received, "metaAndAssetCtxs")
                except READ_ERRORS:
                    feed.status = "reconnecting"
            self._contexts_at[dex] = time.monotonic()

    async def _refresh_book(self, feed: OrderBookFeed, key: str) -> None:
        async with self._locks.setdefault(key, asyncio.Lock()):
            if self._fresh(feed, 15) and feed.book is not None:
                return
            try:
                raw = await self.client.book(feed.coin, feed.precision)
                feed.apply(raw, datetime.now(UTC))
            except READ_ERRORS:
                logger.warning("Book snapshot unavailable for %s", feed.coin, exc_info=True)

    async def get_snapshot(self, market: str | MarketIdentity) -> MarketSnapshot:
        identity = self.resolve(market)
        _, context = self._feed(identity, "context")
        book_key, book = self._feed(identity, "book")
        try:
            async with asyncio.timeout(10):
                work = [self._refresh_book(book, book_key)]
                if not self._fresh(context, 30):
                    work.append(self._refresh_contexts(identity.dex))
                await asyncio.gather(*work)
        except TimeoutError:
            pass
        result = self._snapshot(identity, context, book)
        if not any(field.value is not None for field in result.fields.values()):
            raise MarketUnavailable(f"No market data for {identity.exchange_coin}")
        return result

    def _snapshot(self, identity, context: ContextFeed, book: OrderBookFeed) -> MarketSnapshot:
        now = datetime.now(UTC)
        fields = {}
        context_fields = {
            "mark_price": "markPx",
            "oracle_price": "oraclePx",
            "open_interest": "openInterest",
            "funding_rate": "funding",
            "previous_day_price": "prevDayPx",
            "volume_24h": "dayNtlVlm",
        }
        observation = context.observation
        for name, raw_key in context_fields.items():
            field = MarketField()
            if observation is not None:
                try:
                    value = Decimal(str(observation.raw_payload.get(raw_key)))
                    if value.is_finite():
                        field = MarketField(
                            value=value,
                            channel=observation.channel,
                            received_at=observation.received_at,
                            timestamp_basis="reception",
                            status="fresh" if self._fresh(context, 30) else "stale",
                        )
                except InvalidOperation:
                    pass
            fields[name] = field
        mark, previous = fields["mark_price"], fields["previous_day_price"]
        fields["change_24h_percent"] = MarketField()
        if mark.value is not None and previous.value is not None and min(mark.value, previous.value) > 0:
            fields["change_24h_percent"] = mark.model_copy(
                update={
                    "value": (mark.value / previous.value - 1) * 100,
                    "status": "fresh" if mark.status == previous.status == "fresh" else "stale",
                }
            )
        for name in ("bid_price", "ask_price", "mid_price", "bid_depth", "ask_depth", "spread_bps"):
            fields[name] = MarketField()
        depth = None
        if book.book is not None and book.observation is not None:
            observation = book.observation
            for metric, value in observation.values.items():
                fields[metric.value] = MarketField(
                    value=value,
                    channel="l2Book",
                    source_at=observation.observed_at,
                    received_at=observation.received_at,
                    timestamp_basis="exchange",
                    status="fresh" if self._fresh(book, 15) else "stale",
                )
            bid, ask, mid = (fields[name] for name in ("bid_price", "ask_price", "mid_price"))
            if bid.value is not None and ask.value is not None and mid.value is not None:
                fields["spread_bps"] = mid.model_copy(update={"value": (ask.value - bid.value) / mid.value * 10000})
            depth = BookDepth(
                precision=book.precision,
                fast=book.fast,
                bid_levels=len(book.book.levels[0]),
                ask_levels=len(book.book.levels[1]),
            )
        return MarketSnapshot(market=identity, assembled_at=now, fields=fields, depth=depth)

    async def get_candles(
        self, market: str | MarketIdentity, interval: str = "5m", limit: int = 200
    ) -> tuple[Candle, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("Candle limit must be between 1 and 200")
        key, feed = self._feed(market, "candles", interval)
        try:
            async with asyncio.timeout(10), self._locks.setdefault(key, asyncio.Lock()):
                if not self._fresh(feed, 10):
                    candles = await feed.history()
                    # Preserve any newer live observations while REST was in flight.
                    for candle in feed.candles.values():
                        previous = candles.get(candle.time)
                        if previous is None or candle.trades > previous.trades:
                            candles[candle.time] = candle
                    feed.candles = dict(sorted(candles.items())[-200:])
                    feed.changed()
                return feed.data[-limit:]
        except READ_ERRORS as error:
            raise MarketUnavailable("Candle history unavailable") from error

    async def get_order_book(self, market: str | MarketIdentity, precision: int | None = 5, fast: bool = True) -> Book:
        key, feed = self._feed(market, "book", precision=precision, fast=fast)
        try:
            async with asyncio.timeout(10):
                await self._refresh_book(feed, key)
        except TimeoutError:
            pass
        if feed.book is None or not self._fresh(feed, 15):
            raise MarketUnavailable("Order book unavailable")
        return feed.book.model_copy(deep=True)

    async def get_recent_trades(self, market: str | MarketIdentity, limit: int = 40) -> tuple[Trade, ...]:
        if not 1 <= limit <= 40:
            raise ValueError("Trade limit must be between 1 and 40")
        _, feed = self._feed(market, "trades")
        if self._fresh(feed, 15):
            return feed.data[:limit]
        try:
            async with asyncio.timeout(10), self.subscribe(market, ("trades",)) as updates:
                async for update in updates:
                    if not update.initial and update.status == "live":
                        return update.data[:limit]
        except TimeoutError as error:
            raise MarketUnavailable("Recent trades unavailable") from error

    async def market_changes(self) -> dict[str, float | None]:
        try:
            async with asyncio.timeout(10):
                await asyncio.gather(
                    *(self._refresh_contexts(dex) for dex in {m.dex for m in await self.list_markets()})
                )
        except TimeoutError:
            pass
        changes = {}
        for identity in await self.list_markets():
            _, context = self._feed(identity, "context")
            _, book = self._feed(identity, "book")
            change = self._snapshot(identity, context, book).fields["change_24h_percent"]
            changes[market_symbol(identity)] = float(change.value) if change.status == "fresh" else None
        return changes

    def health(self) -> dict:
        result = {"markets": {}, "order_books": {}, "subscriptions": {}}
        for key, task in self._tasks.items():
            feed = self._feeds[key]
            status = "failed" if task.done() else feed.status
            if status == "live" and isinstance(feed, (ContextFeed, OrderBookFeed)):
                if not self._fresh(feed, 30 if isinstance(feed, ContextFeed) else 15):
                    status = "stale"
            state = {
                "status": status,
                "clients": self._owners[key],
                "last_received_at": feed.received_at.timestamp() if feed.received_at else None,
            }
            result["subscriptions"][key] = state
            if isinstance(feed, CandleFeed):
                result["markets"][key] = {**state, "candles": len(feed.candles)}
            elif isinstance(feed, OrderBookFeed):
                legacy_key = f"book:{market_symbol(feed.market)}:{feed.precision}" if feed.fast else key
                result["order_books"][legacy_key] = state
        return result
