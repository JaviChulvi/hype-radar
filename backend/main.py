import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from feed import CandleFeed, Interval, Symbol


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.feeds = {}
    app.state.feed_tasks = {}
    try:
        yield
    finally:
        for task in app.state.feed_tasks.values():
            task.cancel()
        await asyncio.gather(*app.state.feed_tasks.values(), return_exceptions=True)


app = FastAPI(title="Hype Radar", lifespan=lifespan)


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
