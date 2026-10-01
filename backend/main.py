import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from feed import CandleFeed, Symbol


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.feeds = {}
    app.state.feed_tasks = []
    try:
        yield
    finally:
        for task in app.state.feed_tasks:
            task.cancel()
        await asyncio.gather(*app.state.feed_tasks, return_exceptions=True)


app = FastAPI(title="Hype Radar", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "markets": {
            symbol: {
                "status": feed.status,
                "candles": len(feed.candles),
                "clients": len(feed.clients),
                "last_received_at": feed.last_received_at,
            }
            for symbol, feed in app.state.feeds.items()
        }
    }


@app.websocket("/ws/candles")
async def candles(websocket: WebSocket, symbol: Symbol = "BTC"):
    await websocket.accept()
    if symbol not in app.state.feeds:
        app.state.feeds[symbol] = CandleFeed(symbol)
        app.state.feed_tasks.append(asyncio.create_task(app.state.feeds[symbol].run()))
    feed = app.state.feeds[symbol]
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
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
