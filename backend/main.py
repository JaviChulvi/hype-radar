import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from feed import BitcoinFeed


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.feed = BitcoinFeed()
    task = asyncio.create_task(app.state.feed.run())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


app = FastAPI(title="Hype Radar", lifespan=lifespan)


@app.get("/health")
async def health():
    feed = app.state.feed
    return {
        "status": feed.status,
        "candles": len(feed.candles),
        "clients": len(feed.clients),
        "last_received_at": feed.last_received_at,
    }


@app.websocket("/ws/btc")
async def bitcoin(websocket: WebSocket):
    await websocket.accept()
    feed = app.state.feed
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
