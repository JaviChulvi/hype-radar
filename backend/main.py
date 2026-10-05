import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from app.application.agent import ChatAgentService
from app.application.alerts import AlertService, AlertSpec
from app.application.chat import ChatMessage, ChatNotConfigured, ChatProviderError, OpenRouterTranscriptionClient
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
AUDIO_FORMATS = {
    "audio/aac": "aac",
    "audio/flac": "flac",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/webm": "webm",
    "audio/x-wav": "wav",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    markets = MarketDataService(HyperliquidClient(settings.hyperliquid_network))
    transcription = OpenRouterTranscriptionClient(settings)
    app.state.markets = markets
    app.state.transcription = transcription
    app.state.evaluator_status = "starting"
    evaluator = None
    task = None
    database = create_engine(settings)
    app.state.alert_sessions = create_session_factory(database)
    try:
        evaluator = EvaluationWorker(markets)
        await evaluator.start()
        app.state.evaluator = evaluator
        alerts = AlertService(app.state.alert_sessions, markets, evaluator)
        app.state.alerts = alerts
        app.state.chat = ChatAgentService(settings, markets, alerts)
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
        await transcription.close()
        if hasattr(app.state, "chat"):
            await app.state.chat.close()
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


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message content must not be blank")
        return value.strip()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    request_id: UUID = Field(default_factory=uuid4)
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=20)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def validate_context_size(self):
        if sum(len(item.content) for item in self.history) + len(self.message) > 20_000:
            raise ValueError("Conversation context is too large")
        return self

    def provider_messages(self) -> tuple[ChatMessage, ...]:
        history = tuple(ChatMessage(item.role, item.content) for item in self.history)
        return (*history, ChatMessage("user", self.message))


@app.post("/api/chat")
async def chat(request: ChatRequest):
    provider: ChatAgentService = app.state.chat
    try:
        reply = await provider.open_stream(
            request.provider_messages(),
            request_id=request.request_id,
        )
    except ChatNotConfigured as error:
        raise HTTPException(503, "OpenRouter is not configured") from error
    except ChatProviderError as error:
        logger.warning("Chat request failed: %s", error)
        raise HTTPException(502, "The chat provider is unavailable") from error

    return StreamingResponse(
        reply,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/chat/transcribe")
async def transcribe_chat_audio(request: Request):
    provider: OpenRouterTranscriptionClient = app.state.transcription
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    audio_format = AUDIO_FORMATS.get(content_type)
    if audio_format is None:
        raise HTTPException(415, "Unsupported audio format")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > provider.max_audio_bytes:
                raise HTTPException(413, "Audio recording is too large")
        except ValueError as error:
            raise HTTPException(400, "Invalid Content-Length header") from error
    audio = await request.body()
    if not audio:
        raise HTTPException(400, "Audio recording is empty")
    if len(audio) > provider.max_audio_bytes:
        raise HTTPException(413, "Audio recording is too large")
    try:
        text = await provider.transcribe(audio, audio_format)
    except ChatNotConfigured as error:
        raise HTTPException(503, "OpenRouter is not configured") from error
    except ChatProviderError as error:
        logger.warning("Audio transcription failed: %s", error)
        raise HTTPException(502, "The transcription provider is unavailable") from error
    return {"text": text}


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
    status: Literal["active", "paused"] = "active",
    offset: Annotated[int, Query(ge=0)] = 0,
):
    visible_markets = [markets.resolve(value) for value in UI_MARKETS]
    if symbol is not None:
        market = resolve(markets, symbol)
        if market not in visible_markets:
            raise HTTPException(404, "Unknown market")
        visible_markets = [market]
    async with request.app.state.alert_sessions() as session:
        rows = await AlertReadRepository(session).list_rows(
            view, visible_markets, limit + 1, offset=offset, status=status
        )
    if view == "rules":
        for row in rows:
            row["monitoring"] = (
                request.app.state.evaluator.monitoring(row["alert_id"]) if status == "active" else "inactive"
            )
    return {"items": rows[:limit], "has_more": len(rows) > limit}


class CreateAlertRequest(BaseModel):
    spec: AlertSpec
    request_id: UUID


class AlertStatusRequest(BaseModel):
    status: Literal["active", "paused"]


@app.post("/api/alerts")
async def create_alert(body: CreateAlertRequest, request: Request):
    service: AlertService = request.app.state.alerts
    preview = await service.preview(body.spec, body.request_id)
    return await service.create(UUID(preview["preview_id"]))


@app.patch("/api/alerts/{alert_id}")
async def set_alert_status(alert_id: UUID, body: AlertStatusRequest, request: Request):
    try:
        return await request.app.state.alerts.set_status(alert_id, body.status)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error


@app.get("/api/alerts/events/{event_id}")
async def alert_event(event_id: UUID, request: Request):
    try:
        return await request.app.state.alerts.get_event(event_id)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error


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
    evaluator_status = app.state.evaluator_status
    if evaluator_status in {"running", "idle"}:
        evaluator_status = "running" if app.state.evaluator.rules else "idle"
    state["evaluator"] = {"status": evaluator_status, "active_rules": len(app.state.evaluator.rules)}
    state["chat"] = {
        "status": "configured" if app.state.chat.configured else "unconfigured",
        "provider": "openrouter",
        "model": app.state.chat.model,
        "transcription_model": app.state.transcription.transcription_model,
    }
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
