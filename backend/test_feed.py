import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from feed import BitcoinFeed

DAY = 86_400_000


def candle(time, trades, close="100"):
    return {
        "t": time,
        "s": "BTC",
        "i": "1d",
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
                feed = BitcoinFeed()
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
                    feed = BitcoinFeed()
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


if __name__ == "__main__":
    unittest.main()
