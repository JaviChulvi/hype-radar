import asyncio
import math
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from feed import MARKETS, CandleFeed, Interval, Symbol


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.feeds = {}
    app.state.feed_tasks = {}
    app.state.market_changes = {}
    app.state.market_changes_at = 0
    app.state.market_lock = asyncio.Lock()
    try:
        yield
    finally:
        for task in app.state.feed_tasks.values():
            task.cancel()
        await asyncio.gather(*app.state.feed_tasks.values(), return_exceptions=True)


app = FastAPI(title="Hype Radar", lifespan=lifespan)


@app.get("/api/markets")
async def market_changes():
    async with app.state.market_lock:
        if time.monotonic() - app.state.market_changes_at < 10:
            return app.state.market_changes
        changes = dict.fromkeys(MARKETS)
        async with httpx.AsyncClient(timeout=10) as http:
            async def load(dex):
                try:
                    response = await http.post(
                        "https://api.hyperliquid.xyz/info",
                        json={"type": "metaAndAssetCtxs", "dex": dex},
                    )
                    response.raise_for_status()
                    meta, contexts = response.json()
                    by_coin = {
                        asset["name"]: ctx
                        for asset, ctx in zip(meta["universe"], contexts, strict=True)
                    }
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    return
                for symbol, coin in MARKETS.items():
                    if coin not in by_coin:
                        continue
                    try:
                        mark = float(by_coin[coin]["markPx"])
                        previous = float(by_coin[coin]["prevDayPx"])
                        if mark > 0 and previous > 0 and math.isfinite(mark) and math.isfinite(previous):
                            change = (mark / previous - 1) * 100
                            if math.isfinite(change):
                                changes[symbol] = change
                    except (ValueError, KeyError, TypeError):
                        continue

            dexes = {coin.split(":")[0] if ":" in coin else "" for coin in MARKETS.values()}
            await asyncio.gather(*(load(dex) for dex in dexes))
        app.state.market_changes = changes
        app.state.market_changes_at = time.monotonic()
        return changes


@app.get("/health")
async def health():
    return {
        "markets": {
            key: {
                "status": feed.status,
                "candles": len(feed.candles),
                "clients": len(feed.clients),
                "last_received_at": feed.last_received_at,
            }
            for key, feed in app.state.feeds.items()
        }
    }


@app.websocket("/ws/candles")
async def candles(websocket: WebSocket, symbol: Symbol = "BTC", interval: Interval = "5m"):
    await websocket.accept()
    key = f"{symbol}:{interval}"
    if key not in app.state.feeds:
        app.state.feeds[key] = CandleFeed(symbol, interval)
        app.state.feed_tasks[key] = asyncio.create_task(app.state.feeds[key].run())
    feed = app.state.feeds[key]
    queue = feed.subscribe()

    async def send():
        while True:
            message = await queue.get()
            if message is None:
                await websocket.close(code=1013)
                return
            await asyncio.wait_for(websocket.send_json(message), timeout=5)

    async def receive():
        async for _ in websocket.iter_text():
            pass

    tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except TimeoutError:
        await websocket.close(code=1013)
    finally:
        feed.clients.discard(queue)
        if not feed.clients and app.state.feeds.get(key) is feed:
            del app.state.feeds[key]
            tasks.append(app.state.feed_tasks.pop(key))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
