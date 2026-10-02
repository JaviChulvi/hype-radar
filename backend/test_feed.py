import asyncio
import json
import unittest
from itertools import product
from threading import Event
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from app.application.market_data import MarketDataService, SlowConsumer
from app.domain.market_data import HISTORY_SIZE, INTERVAL_MS, Book
from app.domain.markets import MARKETS, UI_MARKETS, resolve_market
from app.ingestion.client import HyperliquidClient
from feed import CandleFeed, SharedFeed, TradeFeed
from main import app

DAY = 86_400_000


def candle(time, trades, close="100"):
    return {"t": time, "s": "BTC", "i": "5m", "o": "100", "h": "130", "l": "80", "c": close, "n": trades}


async def idle(*args):
    await asyncio.Event().wait()


class FeedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = HyperliquidClient()
        self.markets = MarketDataService(self.client)

    async def asyncTearDown(self):
        await self.markets.close()

    async def live(self, updates):
        async with asyncio.timeout(5):
            async for update in updates:
                if update.status == "live":
                    return update

    async def test_each_market_and_interval_use_matching_history_and_subscription(self):
        for (symbol, coin), interval in product(MARKETS.items(), INTERVAL_MS):
            with self.subTest(symbol=symbol, interval=interval):
                socket = AsyncMock()
                socket.recv.side_effect = idle

                async def connection(*args, socket=socket, **kwargs):
                    yield socket

                data = {**candle(DAY, 2), "s": coin, "i": interval}
                with (
                    patch("app.ingestion.client.connect", connection),
                    patch.object(self.client, "info", AsyncMock(return_value=[data])) as info,
                ):
                    async with self.markets.subscribe(symbol, ("candles",), interval=interval) as updates:
                        snapshot = await self.live(updates)
                        self.assertEqual(snapshot.market.exchange_coin, coin)
                        self.assertEqual(snapshot.data[0].interval, interval)
                        subscription = json.loads(socket.send.call_args.args[0])["subscription"]
                        self.assertEqual(subscription, {"type": "candle", "coin": coin, "interval": interval})
                        request = info.call_args.args[0]["req"]
                        self.assertEqual(request["coin"], coin)
                        self.assertEqual(request["interval"], interval)
                        self.assertEqual(
                            request["endTime"] - request["startTime"], HISTORY_SIZE * INTERVAL_MS[interval]
                        )
                        feed = self.markets._feeds[f"{symbol}:{interval}"]
                        with self.assertRaises(ValueError):
                            feed.parse_candle({**data, "i": "1m" if interval != "1m" else "1M"})
                        with self.assertRaises(ValueError):
                            feed.parse_candle({**data, "s": "ETH" if coin == "BTC" else "BTC"})

    async def test_retry_http_503_handshake(self):
        attempts = 0

        async def handshake(connection, request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return Response(503, "Service Unavailable", Headers(), b"")

        async def stream(socket):
            await socket.recv()
            await socket.send(json.dumps({"channel": "candle", "data": candle(DAY, 2)}))
            await socket.wait_closed()

        async with serve(stream, "127.0.0.1", 0, process_request=handshake) as server:
            port = server.sockets[0].getsockname()[1]

            def local_connect(*args, **kwargs):
                return connect(f"ws://127.0.0.1:{port}", **kwargs)

            with (
                patch("app.ingestion.client.connect", local_connect),
                patch.object(self.client, "info", AsyncMock(return_value=[candle(DAY, 1)])),
            ):
                async with self.markets.subscribe("BTC", ("candles",)) as updates:
                    await self.live(updates)
                    self.assertEqual(attempts, 2)
                    self.assertFalse(self.markets._tasks["BTC:5m"].done())

    async def test_reconcile_bootstrap_and_live_candles(self):
        for trades, expected in [(10, "115"), (20, "115"), (30, "125")]:
            messages = asyncio.Queue()
            messages.put_nowait(json.dumps({"channel": "candle", "data": candle(DAY, trades, "125")}))
            buffered = asyncio.Event()

            class Socket:
                async def send(self, message):
                    pass

                async def recv(self, messages=messages, buffered=buffered):
                    if messages.empty():
                        buffered.set()
                    return await messages.get()

            async def connection(*args, **kwargs):
                yield Socket()

            async def history(*args, buffered=buffered):
                await buffered.wait()
                return [candle(DAY, 20, "115"), candle(2 * DAY, 5)]

            # A separate service prevents prior subtest observations entering reconciliation.
            async with MarketDataService(HyperliquidClient()) as markets:
                with (
                    patch("app.ingestion.client.connect", connection),
                    patch.object(markets.client, "candles", history),
                ):
                    async with markets.subscribe("BTC", ("candles",)) as updates:
                        snapshot = await self.live(updates)
                        self.assertEqual(str(snapshot.data[0].close), expected)
                        self.assertEqual(set(snapshot.data[0].model_dump()), {"time", "open", "high", "low", "close"})
                        for count, close in [(4, "99"), (6, "105")]:
                            messages.put_nowait(
                                json.dumps({"channel": "candle", "data": candle(2 * DAY, count, close)})
                            )
                        update = await asyncio.wait_for(anext(updates), 1)
                        self.assertEqual(str(update.data.close), "105")

    async def test_snapshots_replace_depth_reject_older_data_and_recover(self):
        messages = asyncio.Queue()
        subscriptions = []

        class Socket:
            async def send(self, message):
                subscriptions.append(json.loads(message)["subscription"])

            async def recv(self):
                item = await messages.get()
                if isinstance(item, Exception):
                    raise item
                return json.dumps({"channel": "l2Book", "data": item})

        async def connection(*args, **kwargs):
            yield Socket()

        def book(timestamp, levels):
            return {"coin": "xyz:SP500", "time": timestamp, "levels": levels}

        with patch("app.ingestion.client.connect", connection):
            async with self.markets.subscribe("SP500", ("book",), precision=4, fast=True) as updates:
                self.assertIsNone((await anext(updates)).data)
                await messages.put(book(10, [[{"px": "100", "sz": "1"}], [{"px": "101", "sz": "3"}]]))
                first = await self.live(updates)
                self.assertEqual(first.data.time, 10)
                self.assertEqual(subscriptions[0], {"type": "l2Book", "coin": "xyz:SP500", "nSigFigs": 4, "fast": True})
                await messages.put(book(9, [[], []]))
                await messages.put(book(11, [[{"px": "98", "sz": "4"}], []]))
                updated = await anext(updates)
                self.assertEqual(updated.data.time, 11)
                self.assertEqual(updated.data.model_dump(mode="json")["levels"], [[{"px": "98", "sz": "4"}], []])
                await messages.put(OSError("lost connection"))
                reset = await anext(updates)
                self.assertEqual(reset.status, "reconnecting")
                self.assertIsNone(reset.data)
                self.assertTrue(reset.observation.gap)
                await messages.put(book(12, [[], []]))
                recovered = await self.live(updates)
                self.assertEqual(recovered.data.time, 12)
                self.assertTrue(recovered.observation.gap)
                self.assertEqual(len(subscriptions), 2)

    async def test_trade_deduplication_order_bounds_and_market_validation(self):
        feed = TradeFeed(self.client, resolve_market("BTC"))
        queue = asyncio.Queue(maxsize=32)
        feed.clients.add(queue)
        trades = [
            {
                "coin": "BTC",
                "px": "100",
                "sz": "0.1",
                "side": "B",
                "time": i // 2,
                "tid": i,
                "users": ["buyer", "seller"],
            }
            for i in range(50)
        ]
        feed.apply(trades)
        result = queue.get_nowait().data
        self.assertEqual([trade.tid for trade in result], list(range(49, 9, -1)))
        self.assertNotIn("users", result[0].model_dump())
        feed.apply(trades)
        self.assertTrue(queue.empty())
        feed.apply([{**trades[-1], "time": 100}])
        self.assertEqual(queue.get_nowait().data[0].time, 100)
        for override in ({"coin": "ETH"}, {"side": "X"}, {"sz": "NaN"}):
            with self.assertRaises(ValueError):
                feed.apply([{**trades[-1], **override}])
        self.assertEqual(len(feed.trades), 40)
        feed.reset()
        self.assertEqual(feed.data, ())

    async def test_invalid_levels_and_slow_clients(self):
        for bids, asks in [
            ([{"px": "NaN", "sz": "1"}], []),
            ([{"px": "1", "sz": "Infinity"}], []),
            ([{"px": "1", "sz": "-1"}], []),
            ([{"px": "2", "sz": "1"}], [{"px": "1", "sz": "1"}]),
            ([{"px": "1", "sz": "1"}, {"px": "2", "sz": "1"}], []),
        ]:
            with self.assertRaises(ValueError):
                Book.model_validate({"coin": "BTC", "time": 1, "levels": [bids, asks]})
        with patch.object(SharedFeed, "run", idle):
            async with self.markets.subscribe("BTC", ("book",)) as updates:
                feed = self.markets._feeds["book:BTC:full:normal"]
                for _ in range(32):
                    feed.broadcast()
                with self.assertRaises(SlowConsumer):
                    await anext(updates)
            self.assertEqual(self.markets._tasks, {})


class RoutingTests(unittest.TestCase):
    def test_feeds_are_shared_and_last_viewer_cleanup(self):
        with patch.object(SharedFeed, "run", idle), TestClient(app) as client:
            for path, key, count in (("candles", "BTC:5m", 1), ("book", "book:BTC:5:fast", 2)):
                with client.websocket_connect(f"/ws/{path}") as first:
                    self.assertEqual(first.receive_json()["symbol"], "BTC")
                    with client.websocket_connect(f"/ws/{path}") as second:
                        self.assertEqual(second.receive_json()["symbol"], "BTC")
                        self.assertEqual(len(app.state.markets._tasks), count)
                        self.assertEqual(app.state.markets._owners[key], 2)
                        second.close()
                    self.assertEqual(client.get("/health").json()["subscriptions"][key]["clients"], 1)
                    first.close()
                    client.get("/health")
                self.assertEqual(client.get("/health").json()["subscriptions"], {})
            self.assertEqual(app.state.markets._tasks, {})

    def test_switching_intervals_stops_unused_feeds_and_reopens_them(self):
        stopped = Event()

        async def run(feed):
            try:
                await idle()
            finally:
                stopped.set()

        with patch.object(CandleFeed, "run", run), TestClient(app) as client:
            for interval in [*INTERVAL_MS, "5m"]:
                stopped.clear()
                with client.websocket_connect(f"/ws/candles?interval={interval}") as socket:
                    self.assertEqual(socket.receive_json()["interval"], interval)
                    task = app.state.markets._tasks[f"BTC:{interval}"]
                    socket.close()
                    client.get("/health")
                self.assertTrue(stopped.wait(2))
                self.assertTrue(task.cancelled())
                self.assertEqual(client.get("/health").json()["markets"], {})

    def test_unsupported_options_are_rejected_without_a_feed(self):
        with TestClient(app) as client:
            for path in ("candles?symbol=DOGE", "candles?interval=1D", "candles?interval=", "book?precision=6"):
                with self.assertRaises(WebSocketDisconnect) as error:
                    with client.websocket_connect(f"/ws/{path}"):
                        pass
                self.assertEqual(error.exception.code, 1008)
                self.assertEqual(app.state.markets._tasks, {})

    def test_market_mapping_missing_prices_cache_and_outage_recovery(self):
        failing = False

        async def contexts(dex):
            if failing and dex == "xyz":
                raise httpx.ConnectError("Unavailable")
            names = ["ETH", "OTHER", "BTC"] if not dex else ["xyz:XYZ100", "xyz:BRENTOIL", "xyz:SP500"]
            marks = ["90", "200", "110"] if not dex else ["100", "NaN", "150"]
            previous = ["100"] * 3 if not dex else ["100", "100", "0"]
            return {
                name: {"markPx": mark, "prevDayPx": prev}
                for name, mark, prev in zip(names, marks, previous, strict=True)
            }

        with TestClient(app) as client:
            with patch.object(app.state.markets.client, "contexts", side_effect=contexts) as upstream:
                changes = client.get("/api/markets").json()
                self.assertEqual(set(changes), set(UI_MARKETS))
                self.assertAlmostEqual(changes["BTC"], 10)
                self.assertAlmostEqual(changes["ETH"], -10)
                self.assertEqual(changes["XYZ100"], 0)
                self.assertIsNone(changes["SP500"])
                self.assertIsNone(changes["BRENTOIL"])
                self.assertEqual(client.get("/api/markets").json(), changes)
                self.assertEqual(upstream.call_count, 2)
                failing = True
                app.state.markets._contexts_at.clear()
                partial = client.get("/api/markets").json()
                self.assertIsNone(partial["XYZ100"])
                self.assertAlmostEqual(partial["BTC"], 10)
                failing = False
                app.state.markets._contexts_at.clear()
                self.assertEqual(client.get("/api/markets").json(), changes)


if __name__ == "__main__":
    unittest.main()
