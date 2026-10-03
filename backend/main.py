import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.application.market_data import MarketDataService, MarketUnavailable, SlowConsumer
from app.config import get_settings
from app.domain.market_data import BookPrecision, Candle, Interval
from app.domain.markets import UI_MARKETS
from app.ingestion.client import HyperliquidClient
from app.persistence import create_engine, create_session_factory
from app.persistence.repositories import AlertReadRepository
from app.workers.evaluator import EvaluationWorker

logger = logging.getLogger(__name__)
Symbol = Literal["BTC", "ETH", "SP500", "XYZ100", "BRENTOIL"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    markets = MarketDataService(HyperliquidClient(settings.hyperliquid_network))
    app.state.markets = markets
    app.state.evaluator_status = "starting"
    evaluator = None
    task = None
    database = create_engine(settings)
    app.state.alert_sessions = create_session_factory(database)
    try:
        evaluator = EvaluationWorker(markets)
        await evaluator.start()
        app.state.evaluator_status = "running" if evaluator.rules else "idle"

        async def evaluate():
            try:
                await evaluator.run()
            except Exception:
                app.state.evaluator_status = "failed"
                logger.exception("Alert evaluator stopped; restart required")

        task = asyncio.create_task(evaluate())
        yield
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if evaluator is not None:
            await evaluator.close()
        await markets.close()
        await database.dispose()


app = FastAPI(title="Hype Radar", lifespan=lifespan)


def market_data(request: Request) -> MarketDataService:
    return request.app.state.markets


Markets = Annotated[MarketDataService, Depends(market_data)]


@app.exception_handler(MarketUnavailable)
async def unavailable(request: Request, error: MarketUnavailable):
    return JSONResponse({"detail": str(error)}, status_code=503)


def resolve(markets: MarketDataService, market: str):
    try:
        return markets.resolve(market)
    except KeyError as error:
        raise HTTPException(404, "Unknown market") from error


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must not be blank")
        return value.strip()


@app.post("/api/chat")
async def chat(request: ChatRequest):
    async def reply():
        # Simulate model latency and token delivery without an AI provider.
        await asyncio.sleep(0.9)
        excerpt = request.message[:120] + ("…" if len(request.message) > 120 else "")
        response = (
            f"I received your message: “{excerpt}”\n\n"
            "This is a demo reply from Hype Radar. Once an AI provider is connected, "
            "we can explore market moves, liquidity, and alert ideas here. "
            "For now, no market analysis has been performed and no alert has been created."
        )
        for word in response.split(" "):
            yield word + " "
            await asyncio.sleep(0.035)

    return StreamingResponse(
        reply(),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/markets")
async def market_changes(markets: Markets):
    changes = await markets.market_changes()
    return {symbol: changes[symbol] for symbol in UI_MARKETS}


@app.get("/api/alerts")
async def alerts(
    request: Request,
    markets: Markets,
    view: Literal["rules", "history"] = "rules",
    symbol: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
):
    visible_markets = [markets.resolve(value) for value in UI_MARKETS]
    if symbol is not None:
        market = resolve(markets, symbol)
        if market not in visible_markets:
            raise HTTPException(404, "Unknown market")
        visible_markets = [market]
    async with request.app.state.alert_sessions() as session:
        rows = await AlertReadRepository(session).list_rows(view, visible_markets, limit + 1)
    return {"items": rows[:limit], "has_more": len(rows) > limit}


@app.get("/api/market-data")
async def list_markets(markets: Markets):
    return await markets.list_markets()


@app.get("/api/market-data/{market}/snapshot")
async def market_snapshot(market: str, markets: Markets):
    return await markets.get_snapshot(resolve(markets, market))


@app.get("/api/market-data/{market}/candles")
async def market_candles(
    market: str, markets: Markets, interval: Interval = "5m", limit: Annotated[int, Query(ge=1, le=200)] = 200
):
    return await markets.get_candles(resolve(markets, market), interval, limit)


@app.get("/api/market-data/{market}/book")
async def market_book(
    market: str, markets: Markets, precision: BookPrecision | Literal["full"] = "5", fast: bool = True
):
    return await markets.get_order_book(resolve(markets, market), None if precision == "full" else int(precision), fast)


@app.get("/api/market-data/{market}/trades")
async def market_trades(market: str, markets: Markets, limit: Annotated[int, Query(ge=1, le=40)] = 40):
    return await markets.get_recent_trades(resolve(markets, market), limit)


@app.get("/health")
async def health(markets: Markets):
    state = markets.health()
    state["evaluator"] = {"status": app.state.evaluator_status}
    failed = app.state.evaluator_status == "failed" or any(
        item["status"] != "live" for item in state["subscriptions"].values()
    )
    return JSONResponse(state, status_code=503 if failed else 200)


@app.websocket("/ws/candles")
async def candles(websocket: WebSocket, symbol: Symbol = "BTC", interval: Interval = "5m"):
    await stream_feed(websocket, symbol, ("candles",), interval=interval)


@app.websocket("/ws/book")
async def order_book(websocket: WebSocket, symbol: Symbol = "BTC", precision: BookPrecision = "5"):
    await stream_feed(websocket, symbol, ("book", "trades"), precision=int(precision), fast=True)


async def stream_feed(websocket: WebSocket, symbol: str, channels: tuple, **options):
    await websocket.accept()
    markets = websocket.app.state.markets
    async with markets.subscribe(symbol, channels, **options) as updates:

        async def send():
            async for update in updates:
                if update.channel == "candles":
                    if isinstance(update.data, Candle):
                        message = {"type": "candle", "candle": update.data.model_dump(mode="json")}
                    elif update.initial or update.status == "live":
                        message = {
                            "type": "snapshot",
                            "symbol": symbol,
                            "interval": options["interval"],
                            "status": update.status,
                            "candles": [c.model_dump(mode="json") for c in update.data],
                        }
                    else:
                        message = {"type": "status", "status": update.status}
                elif update.channel == "book":
                    message = {
                        "type": "book",
                        "symbol": symbol,
                        "status": update.status,
                        "book": update.data.model_dump(mode="json") if update.data else None,
                    }
                    if update.initial:
                        message["trades"] = []
                else:
                    message = {"type": "trades", "trades": [t.model_dump(mode="json") for t in update.data]}
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
        except (TimeoutError, SlowConsumer):
            await websocket.close(code=1013)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
