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

from feed import HISTORY_SIZE, INTERVAL_MS, MARKETS, Book, CandleFeed, OrderBookFeed
from main import app

DAY = 86_400_000


def candle(time, trades, close="100"):
    return {
        "t": time,
        "s": "BTC",
        "i": "5m",
        "o": "100",
        "h": "130",
        "l": "80",
        "c": close,
        "n": trades,
    }


class FeedTests(unittest.IsolatedAsyncioTestCase):
    async def snapshot(self, queue):
        while True:
            message = await asyncio.wait_for(queue.get(), 15)
            if message["type"] == "snapshot" and message["status"] == "live":
                return message

    async def test_each_market_and_interval_use_matching_history_and_subscription(self):
        for (symbol, coin), interval in product(MARKETS.items(), INTERVAL_MS):
            with self.subTest(symbol=symbol, interval=interval):
                socket = AsyncMock()

                async def receive():
                    await asyncio.Event().wait()

                socket.recv.side_effect = receive

                async def connection(*args, **kwargs):
                    yield socket

                data = {**candle(DAY, 2), "s": coin, "i": interval}
                http = AsyncMock()
                http.__aenter__.return_value = http
                http.post.return_value = httpx.Response(
                    200, json=[data], request=httpx.Request("POST", "https://example.test/info")
                )
                with patch("feed.connect", connection), patch("feed.httpx.AsyncClient", return_value=http):
                    feed = CandleFeed(symbol, interval)
                    queue = feed.subscribe()
                    task = asyncio.create_task(feed.run())
                    try:
                        snapshot = await self.snapshot(queue)
                        self.assertEqual(snapshot["symbol"], symbol)
                        self.assertEqual(snapshot["interval"], interval)
                        self.assertEqual(json.loads(socket.send.call_args.args[0])["subscription"]["interval"], interval)
                        request = http.post.call_args.kwargs["json"]["req"]
                        self.assertEqual(request["interval"], interval)
                        self.assertEqual(request["endTime"] - request["startTime"], HISTORY_SIZE * INTERVAL_MS[interval])
                        with self.assertRaises(ValueError):
                            feed.parse_candle({**data, "i": "1m" if interval != "1m" else "1M"})
                        self.assertEqual(json.loads(socket.send.call_args.args[0])["subscription"]["coin"], coin)
                        self.assertEqual(http.post.call_args.kwargs["json"]["req"]["coin"], coin)
                        with self.assertRaises(ValueError):
                            feed.parse_candle({**data, "s": "ETH" if coin == "BTC" else "BTC"})
                    finally:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

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

            response = httpx.Response(
                200,
                json=[candle(DAY, 1)],
                request=httpx.Request("POST", "https://example.test/info"),
            )
            http = AsyncMock()
            http.__aenter__.return_value = http
            http.post.return_value = response
            with (
                patch("feed.connect", local_connect),
                patch("feed.httpx.AsyncClient", return_value=http),
            ):
                feed = CandleFeed()
                queue = feed.subscribe()
                task = asyncio.create_task(feed.run())
                try:
                    await self.snapshot(queue)
                    self.assertEqual(attempts, 2)
                    self.assertFalse(task.done())
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_reconcile_bootstrap_and_live_candles(self):
        for trades, expected in [(10, "115"), (20, "115"), (30, "125")]:
            with self.subTest(buffered_trades=trades):
                messages = asyncio.Queue()
                messages.put_nowait(
                    json.dumps(
                        {"channel": "candle", "data": candle(DAY, trades, "125")}
                    )
                )
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

                async def history(*args, buffered=buffered, **kwargs):
                    await buffered.wait()
                    return httpx.Response(
                        200,
                        json=[candle(DAY, 20, "115"), candle(2 * DAY, 5)],
                        request=httpx.Request("POST", "https://example.test/info"),
                    )

                http = AsyncMock()
                http.__aenter__.return_value = http
                http.post.side_effect = history
                with (
                    patch("feed.connect", connection),
                    patch("feed.httpx.AsyncClient", return_value=http),
                ):
                    feed = CandleFeed()
                    queue = feed.subscribe()
                    task = asyncio.create_task(feed.run())
                    try:
                        snapshot = await self.snapshot(queue)
                        self.assertEqual(snapshot["candles"][0]["close"], expected)
                        self.assertEqual(
                            set(snapshot["candles"][0]),
                            {"time", "open", "high", "low", "close"},
                        )
                        # A delayed current-candle observation must not undo the REST snapshot.
                        for count, close in [(4, "99"), (6, "105")]:
                            messages.put_nowait(
                                json.dumps(
                                    {
                                        "channel": "candle",
                                        "data": candle(2 * DAY, count, close),
                                    }
                                )
                            )
                        update = await asyncio.wait_for(queue.get(), 1)
                        self.assertEqual(update["candle"]["close"], "105")
                        self.assertEqual(str(feed.candles[DAY].close), expected)
                    finally:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)


class RoutingTests(unittest.TestCase):
    def test_feeds_are_shared_per_pair_and_isolated_between_markets_and_intervals(self):
        async def idle(feed):
            await asyncio.Event().wait()

        with patch("main.CandleFeed.run", idle), TestClient(app) as client:
            with client.websocket_connect("/ws/candles") as first:
                snapshot = first.receive_json()
                self.assertEqual(snapshot["symbol"], "BTC")
                self.assertEqual(snapshot["interval"], "5m")
                with client.websocket_connect("/ws/candles?symbol=BTC") as second:
                    self.assertEqual(second.receive_json()["symbol"], "BTC")
                    self.assertEqual(len(app.state.feed_tasks), 1)
                    self.assertEqual(len(app.state.feeds["BTC:5m"].clients), 2)
                    for symbol in ("ETH", "SP500", "XYZ100", "BRENTOIL"):
                        with client.websocket_connect(f"/ws/candles?symbol={symbol}") as socket:
                            self.assertEqual(socket.receive_json()["symbol"], symbol)
                            self.assertIsNot(app.state.feeds[f"{symbol}:5m"], app.state.feeds["BTC:5m"])
                            socket.close()
                            self.assertNotIn(f"{symbol}:5m", client.get("/health").json()["markets"])
                    with client.websocket_connect("/ws/candles?symbol=BTC&interval=1M") as monthly:
                        self.assertEqual(monthly.receive_json()["interval"], "1M")
                        self.assertEqual(len(app.state.feeds["BTC:5m"].clients), 2)
                        self.assertEqual(len(app.state.feeds["BTC:1M"].clients), 1)
                        monthly.close()
                        self.assertNotIn("BTC:1M", client.get("/health").json()["markets"])
                    self.assertEqual(len(app.state.feed_tasks), 1)
                    second.close()
                    self.assertEqual(client.get("/health").json()["markets"]["BTC:5m"]["clients"], 1)
                first.close()
                self.assertEqual(client.get("/health").json()["markets"], {})
            self.assertEqual(app.state.feed_tasks, {})

    def test_switching_intervals_stops_unused_feeds_and_reopens_them(self):
        stopped = Event()

        async def idle(feed):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        with patch("main.CandleFeed.run", idle), TestClient(app) as client:
            for interval in [*INTERVAL_MS, "5m"]:
                stopped.clear()
                with client.websocket_connect(f"/ws/candles?interval={interval}") as socket:
                    self.assertEqual(socket.receive_json()["interval"], interval)
                    self.assertEqual(len(app.state.feeds), 1)
                    task = app.state.feed_tasks[f"BTC:{interval}"]
                    socket.close()
                    self.assertTrue(stopped.wait(2), "Unused ingestion was not stopped")
                    self.assertEqual(client.get("/health").json()["markets"], {})
                    self.assertTrue(task.cancelled())
                    self.assertEqual(app.state.feed_tasks, {})

    def test_unsupported_interval_is_rejected_without_starting_a_feed(self):
        with TestClient(app) as client:
            for interval in ("6h", "1s", "", "1D"):
                with self.subTest(interval=interval), self.assertRaises(WebSocketDisconnect) as error:
                    with client.websocket_connect(f"/ws/candles?interval={interval}"):
                        pass
                self.assertEqual(error.exception.code, 1008)
                self.assertEqual(app.state.feeds, {})

    def test_unsupported_market_is_rejected_without_starting_a_feed(self):
        with TestClient(app) as client:
            with self.assertRaises(WebSocketDisconnect) as error:
                with client.websocket_connect("/ws/candles?symbol=DOGE"):
                    pass
            self.assertEqual(error.exception.code, 1008)
            self.assertEqual(app.state.feeds, {})


class OrderBookTests(unittest.IsolatedAsyncioTestCase):
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

        with patch("feed.connect", connection):
            feed = OrderBookFeed("SP500", "4")
            queue = feed.subscribe()
            self.assertIsNone(queue.get_nowait()["book"])
            task = asyncio.create_task(feed.run())
            try:
                first = book(10, [[{"px": "100", "sz": "1"}, {"px": "99", "sz": "2"}], [{"px": "101", "sz": "3"}]])
                await messages.put(first)
                snapshot = await asyncio.wait_for(queue.get(), 2)
                self.assertEqual(snapshot["status"], "live")
                self.assertEqual(subscriptions[0], {"type": "l2Book", "coin": "xyz:SP500", "nSigFigs": 4, "fast": True})
                await messages.put(book(9, [[], []]))
                await messages.put(book(11, [[{"px": "98", "sz": "4"}], []]))
                updated = await asyncio.wait_for(queue.get(), 2)
                self.assertEqual(updated["book"]["time"], 11)
                self.assertEqual(updated["book"]["levels"], [[{"px": "98", "sz": "4"}], []])
                await messages.put(OSError("lost connection"))
                self.assertEqual((await asyncio.wait_for(queue.get(), 2))["status"], "reconnecting")
                self.assertIsNone(feed.subscribe().get_nowait()["book"])
                await messages.put(book(12, [[], []]))
                self.assertEqual((await asyncio.wait_for(queue.get(), 3))["book"]["levels"], [[], []])
                self.assertEqual(len(subscriptions), 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

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
        feed = OrderBookFeed()
        queue = feed.subscribe()
        for _ in range(32):
            feed.broadcast(feed.snapshot())
        self.assertIsNone(queue.get_nowait())
        self.assertNotIn(queue, feed.clients)


class BookRoutingTests(unittest.TestCase):
    def test_sharing_precision_isolation_and_last_viewer_cleanup(self):
        async def idle(feed):
            await asyncio.Event().wait()

        with patch("main.OrderBookFeed.run", idle), TestClient(app) as client:
            with client.websocket_connect("/ws/book") as first:
                self.assertEqual(first.receive_json()["symbol"], "BTC")
                with client.websocket_connect("/ws/book") as second:
                    second.receive_json()
                    self.assertEqual(len(app.state.feed_tasks), 1)
                    self.assertEqual(client.get("/health").json()["order_books"]["book:BTC:5"]["clients"], 2)
                    with client.websocket_connect("/ws/book?precision=4&symbol=ETH") as other:
                        self.assertEqual(other.receive_json()["symbol"], "ETH")
                        task = app.state.feed_tasks["book:ETH:4"]
                        other.close()
                        self.assertNotIn("book:ETH:4", client.get("/health").json()["order_books"])
                        self.assertTrue(task.cancelled())
                    second.close()
                    self.assertEqual(client.get("/health").json()["order_books"]["book:BTC:5"]["clients"], 1)
                first.close()
                self.assertEqual(client.get("/health").json()["order_books"], {})
                self.assertEqual(app.state.feed_tasks, {})
            for query in ("precision=6", "symbol=DOGE"):
                with self.assertRaises(WebSocketDisconnect), client.websocket_connect(f"/ws/book?{query}"):
                    pass
                self.assertEqual(app.state.feeds, {})


class MarketChangesTests(unittest.TestCase):
    def test_market_mapping_missing_prices_cache_and_outage_recovery(self):
        failing = False

        async def post(url, *, json):
            if failing and json["dex"] == "xyz":
                raise httpx.ConnectError("Unavailable")
            names = ["ETH", "OTHER", "BTC"] if not json["dex"] else ["xyz:XYZ100", "xyz:BRENTOIL", "xyz:SP500"]
            marks = ["90", "200", "110"] if not json["dex"] else ["100", "NaN", "150"]
            previous = ["100"] * 3 if not json["dex"] else ["100", "100", "0"]
            return httpx.Response(200, request=httpx.Request("POST", url), json=[
                {"universe": [{"name": name} for name in names]},
                [{"markPx": mark, "prevDayPx": prev} for mark, prev in zip(marks, previous)],
            ])

        with TestClient(app) as client:
            with patch("main.httpx.AsyncClient.post", side_effect=post) as upstream:
                changes = client.get("/api/markets").json()
                self.assertEqual(set(changes), set(MARKETS))
                self.assertAlmostEqual(changes["BTC"], 10)
                self.assertAlmostEqual(changes["ETH"], -10)
                self.assertEqual(changes["XYZ100"], 0)
                self.assertIsNone(changes["SP500"])
                self.assertIsNone(changes["BRENTOIL"])
                self.assertEqual(client.get("/api/markets").json(), changes)
                self.assertEqual(upstream.call_count, 2)
                failing = True
                app.state.market_changes_at = 0
                partial = client.get("/api/markets").json()
                self.assertIsNone(partial["XYZ100"])
                self.assertAlmostEqual(partial["BTC"], 10)
                failing = False
                app.state.market_changes_at = 0
                self.assertEqual(client.get("/api/markets").json(), changes)


if __name__ == "__main__":
    unittest.main()
