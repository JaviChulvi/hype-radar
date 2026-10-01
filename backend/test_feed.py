import asyncio
import json
import unittest
from itertools import product
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from feed import CandleFeed, HISTORY_SIZE, INTERVAL_MS, MARKETS
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
                            self.assertEqual(client.get("/health").json()["markets"][f"{symbol}:5m"]["clients"], 0)
                    with client.websocket_connect("/ws/candles?symbol=BTC&interval=1M") as monthly:
                        self.assertEqual(monthly.receive_json()["interval"], "1M")
                        self.assertEqual(len(app.state.feeds["BTC:5m"].clients), 2)
                        self.assertEqual(len(app.state.feeds["BTC:1M"].clients), 1)
                        monthly.close()
                        self.assertEqual(client.get("/health").json()["markets"]["BTC:1M"]["clients"], 0)
                    self.assertEqual(len(app.state.feed_tasks), 6)
                    second.close()
                    self.assertEqual(client.get("/health").json()["markets"]["BTC:5m"]["clients"], 1)
                first.close()
                self.assertEqual(client.get("/health").json()["markets"]["BTC:5m"]["clients"], 0)
            self.assertEqual(set(client.get("/health").json()["markets"]), {f"{symbol}:5m" for symbol in MARKETS} | {"BTC:1M"})
        self.assertTrue(all(task.done() for task in app.state.feed_tasks))

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


if __name__ == "__main__":
    unittest.main()
