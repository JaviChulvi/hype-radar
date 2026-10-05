# hype-radar

**Market alerts with evidence, liquidity context, and a devil's advocate.**

Hype Radar is a market-monitoring platform designed for Hyperliquid perpetuals and HIP-3 markets. It brings price, open interest, funding, and order-book conditions into one alert workflow, with the context needed to assess each event.

## Product vision

Turn a market hypothesis into a precise rule, follow it in real time, and receive an alert that explains what happened and how reliable the supporting data are. Hype Radar will pair every signal with its observations, liquidity conditions, and relevant session context, so users can inspect the event behind the notification.

The **devil's advocate** is central to the experience. When a condition is met, the system will also examine stale observations, thin liquidity, conflicting signals, and uncertain oracle context. Users will be able to see both the evidence supporting an alert and the reasons to treat it with caution.

AI assistance will make rules easier to express and events easier to understand. A deterministic engine will evaluate the confirmed conditions, while explanations stay grounded in the recorded market data. Users retain control over their rules and trading decisions.

## Core capabilities

- **Custom market conditions.** Combine price, open interest (OI), and funding predicates with explicit windows, thresholds, persistence requirements, and cooldowns.
- **Agent-driven rule creation.** Describe conditions in plain language, then ask the agent to create the rule; ask for a preview when you only want a draft.
- **Liquidity-aware alerts.** Evaluate spread, order-book depth, activity, and data freshness alongside the primary condition, with configurable warning and blocking policies.
- **HIP-3 market context.** Surface session transitions, reference-price divergence, and verified oracle-regime observations for the selected deployer.
- **Web and Telegram notifications.** Receive event summaries with supporting evidence, quality warnings, and a link to the full breakdown.
- **An auditable event timeline.** Inspect the rule version, observations, predicate results, quality decisions, and delivery history behind each event.
- **Live market and feed health.** Track monitored instruments, observation freshness, connection gaps, and recovery status from the web app.

### Target workflow

> Alert me when HYPE moves above my price threshold, open interest rises over the last 15 minutes, and the spread stays below my limit.

The agent creates and activates the requested rule, then summarizes its exact conditions. When they are met, the alert includes the observed values, timestamps, and quality checks. If the data are incomplete or liquidity is weak, the configured policy determines whether to warn or block the notification, with the reason visible in the event history.

The first release will focus on a small basket of markets, potentially HYPE and selected instruments from one HIP-3 deployer. The MVP pairs the deterministic engine with agent-driven rule creation. The roadmap prioritizes reliable detection, useful context, and a complete evidence trail.

## Run locally

The chart streams Hyperliquid perpetual candles for BTC, ETH, SP500, XYZ100, and BRENTOIL through FastAPI. Use the shadcn/ui market and time interval selectors in the header to switch markets and candle durations. Supported intervals are `1m`, `3m`, `5m` (default), `15m`, `30m`, `1h`, `2h`, `4h`, `8h`, `12h`, `1d`, `3d`, `1w`, and `1M` (one month), matching the [Hyperliquid candle subscription API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions). SP500, XYZ100, and BRENTOIL use the trade[XYZ] markets (`xyz:SP500`, `xyz:XYZ100`, and `xyz:BRENTOIL`). One shared ingestion task per requested market/interval pair serves every browser; the latest 200 candles per pair stay in memory.

The live order book follows the selected market alongside the chart (below it on mobile), with cumulative bid/ask depth, spread, exchange-side price grouping, and base/USD size units. It uses the [Hyperliquid `l2Book` snapshot stream](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions) with `fast: true` through `/ws/book?symbol=BTC&precision=5`. Grouping supports 5 (default), 4, 3, or 2 significant digits. Each market/grouping feed is shared across viewers and released when its last viewer leaves. The fast stream provides up to 5 levels per side, with updates measured around 0.5 seconds apart (exchange-controlled cadence). The panel shows five levels per side. A recent-trades tape fills the column below it with the latest 40 executions, newest first, showing buy/sell side, price, size in the selected base/USD unit, and local time. The `trades` subscription is shared per market across all book precisions; duplicate executions are removed. Books and trades have independent upstream connections and clear their own data when reconnecting. Missing or disconnected books are cleared while reconnecting.

The right-side chat panel next to the order book uses OpenRouter through the backend. `POST /api/chat`
accepts a nonblank `message` of up to 4,000 characters plus up to 20 optional `user`/`assistant`
history messages, then runs the LangChain market agent and streams SSE events for text, tools and alert changes. The API key never reaches the browser.
Conversation state remains in the current browser session and is sent with each request; it is not
stored by Hype Radar. Enter sends; Shift+Enter adds a line. Replies can be stopped or retried after a
failure, and New chat clears the conversation. The panel respects reduced-motion settings and stacks
below the order book on mobile. The microphone button records up to 60 seconds, sends the audio to
`POST /api/chat/transcribe`, and inserts the returned transcript into the composer for review without
sending it automatically. Prompts, responses, and recordings are processed by OpenRouter and its
selected model provider, so do not submit secrets or personal information.

The bottom-left alerts panel spans the chart and order book, with **Active**, **Paused**, and **History** views. On mobile it sits directly below the chart. It refreshes every five seconds and pages through 25 rows at a time, with a current-market filter. Pagination appears only when more than one page is available. Alerts are created only through the assistant; the panel provides pause/resume controls and event evidence. Tables scroll independently, and failed refreshes label retained rows as last-loaded data.

Active alerts are configured active rule versions. A separate monitoring column reports **Monitoring**, **Warming up**, **Stale / interrupted**, or **Evaluator unavailable**, using the deterministic engine's current observations. Agent-created rules and pause/resume actions from the panel or agent apply immediately; other database changes are reconciled within five seconds. History retains each event's original rule name/version and distinguishes event status, condition truth, and quality. **Evidence** opens recorded values, thresholds, observation/evaluation times, OI baselines, and quality reasons. This remains an instance-wide app behind the existing deployment access boundary; owner IDs and delivery recipients are excluded. Notifications remain web-history-only.

The panel reuses the agent's alert service through these endpoints:

- `GET /api/alerts?view=rules|history&status=active|paused&symbol=BTC&limit=25&offset=0`: rows and `has_more`; status filters rules only.
- `PATCH /api/alerts/{alert_id}`: `{ "status": "active" }` or `{ "status": "paused" }`.
- `GET /api/alerts/events/{event_id}`: immutable rule definition and recorded event evidence.

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node.js 22.12+.

Start PostgreSQL, migrate the database, and start the backend from the repository root:

```sh
docker compose up -d postgres
cd backend
cp .env.example .env  # first setup only
uv sync
uv run alembic upgrade head
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

In a second terminal, start the frontend from the repository root:

```sh
cd frontend
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. The frontend proxies `/ws/candles?symbol=BTC&interval=5m` and `/health` to the backend. The `symbol` parameter accepts `BTC` (default), `ETH`, `SP500`, `XYZ100`, or `BRENTOIL`; the `interval` parameter accepts the intervals above. Unsupported symbols or intervals are rejected. Run one backend worker to retain one exchange connection per market/interval pair. A market/interval feed starts when first subscribed and stops when its last consumer disconnects, releasing the upstream connection. Bounded cached history remains available for reads; resubscribing reconciles it with fresh exchange history. Browsers viewing the same pair continue to share one feed. History is loaded on first request and refreshed after upstream reconnection; the chart preserves its last data while reconnecting and clears it when switching markets or intervals. `/health` reports feed status per requested market/interval pair, keyed as `BTC:5m`, for example.

To check the frontend's types and production build, run `npm run build` in `frontend/`. No API keys are required.

The header's market strip shows each picker's instrument and its 24-hour perpetual mark-price change, calculated as `(markPx / prevDayPx - 1) * 100` from Hyperliquid's `metaAndAssetCtxs` (native and `xyz` markets). This is independent of the candle interval and is not an underlying stock's official daily return. Clicking an instrument selects its chart. The frontend refreshes `/api/markets` every 15 seconds after each response; the backend shares results for 10 seconds. Missing or failed market data appears as `—`, with automatic retry.

The Select component is adapted from [shadcn/ui](https://ui.shadcn.com/docs/components/radix/select) to the existing CSS. Market icons are bundled locally from Hyperdash's public `/icons/` assets: [BTC](https://hyperdash.com/icons/BTC.svg), [ETH](https://hyperdash.com/icons/ETH.svg), [SP500](https://hyperdash.com/icons/SP500.png), [XYZ100](https://hyperdash.com/icons/XYZ100.png), and [BRENTOIL](https://hyperdash.com/icons/BRENTOIL.png).

Run the backend regression tests with `uv run python -m unittest -v` in `backend/`.

## Production images

Build the backend and frontend production images from the repository root:

```sh
docker build -t hype-radar-backend ./backend
docker build -t hype-radar-frontend ./frontend
```

The backend image installs the locked runtime dependencies, runs as an unprivileged user, and starts
one Uvicorn worker on port 8000. The frontend image builds the Vite application and serves it with
Nginx on port 80. Nginx forwards `/api`, `/health`, and `/ws` to a container reachable as
`backend:8000`; API response buffering is disabled for chat streaming and WebSocket upgrades are
forwarded for live market data.

Database startup, migrations, secrets, and service ordering are deployment concerns and are not run
during image construction. Prepare the production environment from the provided template:

```sh
cp .env.production.example .env.production
chmod 600 .env.production
```

Replace every placeholder in `.env.production`. `POSTGRES_PASSWORD` must be URL-safe and must match
the password embedded in `DATABASE_URL`. Set `OPENROUTER_SITE_URL` to the public HTTPS URL used by
the deployment. The production environment file is ignored by Git.

Create the Basic Auth password file before starting the stack. The following command prompts for the
password instead of exposing it in shell history:

```sh
mkdir -p .secrets
docker run --rm --interactive --tty \
  --volume "$PWD/.secrets:/auth" \
  httpd:2.4-alpine \
  htpasswd -B -c /auth/htpasswd demo
chmod 644 .secrets/htpasswd
```

Use `-c` only when creating the file. Add another user without replacing existing users:

```sh
docker run --rm --interactive --tty \
  --volume "$PWD/.secrets:/auth" \
  httpd:2.4-alpine \
  htpasswd -B /auth/htpasswd teammate@example.com
```

An email address can be used as the username for clarity, but Basic Auth does not verify ownership of
that email account. The ignored password file stores bcrypt hashes rather than plaintext passwords.
The application must be accessed through HTTPS because Basic Auth credentials are otherwise only
base64-encoded, not encrypted.

Build and start the complete production stack:

```sh
docker compose --env-file .env.production -f compose.prod.yml up -d --build
docker compose --env-file .env.production -f compose.prod.yml ps --all
```

The stack starts PostgreSQL first, waits until it is healthy, runs `alembic upgrade head` once, then
starts the backend and finally Nginx. Only the configured frontend port is published on the host;
PostgreSQL and FastAPI remain reachable only through their Docker networks. Container logs are
rotated to prevent unbounded disk usage. Nginx protects the complete application with Basic Auth.
General API traffic is limited to 30 requests per second per source IP. Chat and transcription are
additionally limited to 6 requests per minute per Basic Auth credential, with a short burst allowance.

Inspect the stack or stop it without deleting PostgreSQL data:

```sh
docker compose --env-file .env.production -f compose.prod.yml logs --follow
docker compose --env-file .env.production -f compose.prod.yml down
```

Running `down --volumes` also deletes the PostgreSQL volume and all persisted application data.

## AWS demo infrastructure

The Terraform configuration in [`infra/terraform`](infra/terraform/README.md) provisions the AWS
demo environment: a Docker-ready Lightsail instance, attached static IP, firewall rules, a Lightsail
CDN distribution, and an account-wide USD 15 monthly budget with email alerts. The default instance
and CDN plans cost approximately USD 9.50 per month before taxes and overages. See the infrastructure
README for authentication, deployment, security, and teardown instructions.

## Shared market-data API

The same service serves browser WebSockets, read-only HTTP calls, and deterministic alerts. Agent
code can call it directly without an LLM framework or API key. Standalone market-data reads do not
need a database; the FastAPI backend always starts alert evaluation and requires PostgreSQL.

| Object | Responsibility |
| --- | --- |
| `HyperliquidClient` (`app/ingestion/client.py`) | Reusable async HTTP client, exchange requests, subscriptions, heartbeat and reconnect handling. |
| `MarketDataService` (`app/application/market_data.py`) | Registry lookup, shared subscriptions, cached reads, typed snapshots and lifecycle. |
| Channel feed objects (`feed.py`) | Candle reconciliation, book replacement, context observations and trade deduplication. |
| `HyperliquidNormalizer` | Convert validated exchange values to deterministic `MarketObservation` records. |
| `EvaluationWorker` | Consume the shared service and persist rule outcomes; it owns no exchange connections. |

`app/domain/markets.py` is the configured registry: BTC, ETH, HYPE, SP500, XYZ100, and BRENTOIL.
Display aliases (`SP500`) and exchange names (`xyz:SP500`) resolve to the same identity. The existing
UI still lists its original five markets. `HYPERLIQUID_NETWORK=mainnet` is the default; `testnet`
changes both transport URLs. The configured markets must exist on the selected network.

From `backend/`, a standalone read client looks like this:

```python
import asyncio
from app.application.market_data import MarketDataService
from app.ingestion.client import HyperliquidClient

async def main():
    async with MarketDataService(HyperliquidClient()) as markets:
        print(await markets.list_markets())
        snapshot = await markets.get_snapshot("BTC")
        print(snapshot.fields["mark_price"].value)
        print(snapshot.model_dump(mode="json"))
        candles = await markets.get_candles("BTC", interval="5m", limit=200)
        book = await markets.get_order_book("BTC", precision=5, fast=True)
        trades = await markets.get_recent_trades("BTC", limit=40)
        async with markets.subscribe("BTC", channels=("context", "book")) as updates:
            async for update in updates:
                print(update.channel, update.status, update.data)
                if update.status == "live":
                    break

asyncio.run(main())
```

Inside the backend, reuse `request.app.state.markets` or inject it into the consumer constructor;
do not construct a second service. A separately launched script owns independent connections.
Subscriptions are async context managers: exiting releases that consumer's ownership. Identical
subscription options share one upstream task. Trades are shared independently of book precision.
The default subscribed book is ungrouped, normal-depth; UI adapters explicitly request fast grouped
books. Bounded subscriber queues terminate slow consumers with `SlowConsumer`; an evaluator stops
rather than silently losing observations.

HTTP equivalents (all GET):

| Route | Parameters |
| --- | --- |
| `/api/market-data` | Configured identities, including HYPE. |
| `/api/market-data/{market}/snapshot` | Combined context and ungrouped normal-depth book. |
| `/api/market-data/{market}/candles` | `interval=5m`, `limit=200` (1–200). |
| `/api/market-data/{market}/book` | `precision=5` (2–5 or `full`), `fast=true`; `full` maps to Python `None`. |
| `/api/market-data/{market}/trades` | `limit=40` (1–40); returns up to that many observed trades. |

```sh
curl 'http://127.0.0.1:8000/api/market-data/xyz:SP500/snapshot'
curl 'http://127.0.0.1:8000/api/market-data/BTC/book?precision=full&fast=false'
```

Financial values are Decimal strings in JSON. Snapshot fields carry `source_at`, `received_at`,
`timestamp_basis`, and `fresh`/`stale`/`missing` status. Context freshness defaults to 30 seconds;
book freshness uses the exchange timestamp and a 15-second budget. Rule-specific budgets still
control evaluation. The snapshot's `assembled_at` is not a common exchange observation time.
Missing fields stay null; a book update never refreshes OI or funding. Spread and mid derive from
the same book. Depth sums only supplied levels and includes grouping, actual side counts and
`quote_notional` units; it is not a full-market liquidity estimate. Funding is the observed raw
rate, and volume/change refer to the perpetual market, not an underlying stock's official return.

Reads reuse fresh state or perform bounded REST requests (temporary streaming for trades), with
a total ten-second deadline. A snapshot may contain explicitly stale or missing fields; no usable
data returns 503. Other read endpoints return 503 when no sufficiently current result is available.
Unknown markets return 404 and invalid query options return 422. The legacy `/api/markets` numeric
percentage mapping and existing WebSocket routes remain compatible. `/health` retains chart/book
sections and adds all active subscriptions plus evaluator status; unhealthy active feeds return 503.

## Deterministic alert core

The alert evaluator is an always-running supervised task in the same process as FastAPI, using the same market-data service. It stores normalized market samples,
immutable rule versions, runtime checkpoints, alert events, evidence, and notification intent in
PostgreSQL. Event, evidence, runtime, and outbox changes are committed atomically. Economic values
use `Decimal` in Python and `NUMERIC(38, 18)` in PostgreSQL.

The first deterministic predicates support current-value thresholds and absolute or percentage open
interest changes over explicit windows. Rules combine predicates with `all` or `any`, using
three-valued `true / false / unknown` logic. Persistence, cooldown, freshness, spread, depth, feed-gap,
out-of-order, and verified-oracle checks are evaluated without an LLM. The exchange's
`activeAssetCtx` channel does not provide a source timestamp, so those observations explicitly use
the backend reception time; `l2Book` observations retain the exchange timestamp.

Start PostgreSQL from the repository root:

```sh
docker compose up -d postgres
```

Prepare and migrate the backend:

```sh
cd backend
cp .env.example .env
uv sync --locked
uv run alembic upgrade head
```

Run the versioned deterministic replay fixture:

```sh
uv run python -m app.workers.replay tests/fixtures/hype_breakout.json
```

Register a reviewed rule definition for BTC, ETH, SP500, XYZ100, or BRENTOIL on the
configured network. A running backend picks up changes within five seconds. Rule creation and new
versions reject unsupported markets before writing a rule. HIP-3 definitions must
use `dex: "xyz"` and the unprefixed coin (for example, `coin: "SP500"`). The HYPE
replay fixture above is synthetic test data and cannot be registered as an active rule.

```sh
uv run python -m app.workers.seed_rule path/to/your-rule.json
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

The evaluator holds shared context and ungrouped normal-depth book subscriptions for each active
market. Closing browser tabs cannot stop those subscriptions. It rebuilds required windows from
stored samples, evaluates observations and timers, and persists emitted events in PostgreSQL.
It loads rules at startup, applies agent changes immediately, and reconciles stored active versions every five seconds.
Alert evaluation starts automatically with the backend. With no active rules it stays idle.
Database/rule-loading failures prevent startup. A runtime evaluator failure stops evaluation and
makes `/health` return 503; restart after resolving the cause. The agent can pause or resume confirmed alerts.
The former standalone live-evaluator command has been removed; seed and replay commands remain. External
notification delivery, per-user authentication, and editing existing rule conditions remain later work;
outbox rows are durable but are not sent yet.

Run the isolated PostgreSQL integration test with:

```sh
docker compose --profile test up -d postgres-test
cd backend
DATABASE_URL=postgresql+psycopg://hype_radar:hype_radar@127.0.0.1:55432/hype_radar_test \
  uv run alembic upgrade head
TEST_DATABASE_URL=postgresql+psycopg://hype_radar:hype_radar@127.0.0.1:55432/hype_radar_test \
  uv run python -m unittest -v tests.test_persistence
```

## OpenRouter chat

OpenRouter remains the default. An optional [Qwen EC2 test setup](infra/qwen-ec2/README.md)
uses a private env overlay to try self-hosted vLLM without changing application defaults.

Create an API key in OpenRouter and add it to `backend/.env`; never commit the real value:

```dotenv
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=deepseek/deepseek-v4-flash
OPENROUTER_TRANSCRIPTION_MODEL=openai/whisper-1
```

Restart the backend after changing `.env`. `OPENROUTER_MODEL` accepts any model slug available to the
account with tool-calling support. The default is `deepseek/deepseek-v4-flash`; routing requires providers
to support the requested parameters. `OPENROUTER_SITE_URL` and `OPENROUTER_APP_NAME` configure optional application
attribution headers. `OPENROUTER_TIMEOUT_SECONDS` and `OPENROUTER_MAX_COMPLETION_TOKENS` bound each
provider request. Voice input uses OpenRouter's dedicated `/api/v1/audio/transcriptions` endpoint.
`OPENROUTER_TRANSCRIPTION_LANGUAGE` can contain an ISO-639-1 hint such as `es`; leave it empty for
automatic language detection. `OPENROUTER_MAX_AUDIO_BYTES` defaults to 10 MiB.

`backend/app/application/agent.py` uses `langchain.agents.create_agent` and `ChatOpenRouter` with ten typed tools:

- `list_markets`, `get_market_snapshot`, `get_candles`, `get_market_microstructure`, `get_metric_history`.
- `preview_alert`, `create_alert`, `list_alerts`, `set_alert_status`, `get_alert_event`.

Market tools share the dashboard's `MarketDataService`. They report sources, timestamps, units and
coverage limitations. Candle results contain OHLC prices (no volume); metric history is limited to
200 observations from at most 24 hours and may be incomplete if the market was not monitored.
News search, technical-indicator alert predicates, and external notification delivery remain future work.

An explicit request to create an alert invokes `create_alert` with its typed conditions and activates it
in the same turn. There is no confirmation card, extra button or confirmation token. The agent reports the
saved definition and actual monitoring state. Ambiguous requests still require the missing conditions.
`preview_alert` is available for explicit preview-only requests; it saves an inactive draft and explains it
in the conversation without an activation component. Internally creation validates an immutable version
before activation. `request_id` and normalized conditions identify a saved rule across retries, independent
of tool-call order, generated names, decimal formatting and predicate order. Equivalent conditions reuse the
saved definition and do not reactivate a subsequently paused rule; different markets or conditions identify
separate rules. This deduplicates equivalent tool calls, not arbitrary changes to model-generated conditions.
Draft activation expires after one hour. `list_alerts` returns up to
30 rules or events per page; pass its `next_offset` with the same filters to retrieve older entries.
Stopping a response does not undo a committed rule. Check the alerts list after an interrupted write.
The evaluator also reconciles committed writes whose HTTP response was lost. Pausing releases evaluator-owned
subscriptions when no remaining rule uses that market; browser-owned subscriptions remain independent.
Resuming starts a new condition episode and persistence period, preserving the previous cooldown and event sequence.

This remains an unauthenticated, instance-wide app. New agent rules use a server-owned instance identity;
the model cannot choose owners or delivery recipients. Use the existing deployment access boundary.

`POST /api/chat` returns `text/event-stream` with JSON `data:` frames of types `text`, `tool_start`,
`tool_end`, `alert_changed`, `done`, and `error`. Each turn is limited to six model calls,
twelve tool calls, and 75 seconds. Missing configuration returns `503`; errors during the agent run
produce an explicit `error` event instead of a successful completion. Retries preserve their request ID.
Conversation text remains browser-local and bounded; the server persists alert previews, rules and evidence.
`/health` reports the configured model and evaluator state without exposing credentials.
Audio recordings are held in memory only for transcription and are not stored by Hype Radar.

## Proposed architecture

The current modular monolith runs ingestion, evaluation, and API tasks in one process. A future notification worker can consume the durable outbox independently. Run exactly one Uvicorn worker: in-memory subscriptions and state are shared within that process.

```mermaid
flowchart TD
    WS[Hyperliquid WebSocket] --> ING[Ingestion and normalization]
    REST[REST startup and reconciliation snapshots] --> ING
    ING --> STATE[Recent market state and window buffers]
    ING --> SAMPLES[(PostgreSQL market samples)]
    STATE --> EVAL[Deterministic evaluator and quality checks]
    EVAL --> EVENTS[(PostgreSQL events, evidence and outbox)]
    EVENTS --> NOTIFY[Notification worker]
    NOTIFY --> TG[Telegram]
    NOTIFY --> WEB[Web notifications]
    SAMPLES --> API[FastAPI]
    EVENTS --> API
    API --> UI[React web app via HTTP and WebSocket or SSE]
    UI --> API
    API --> AI[AI rule proposals and deferred explanations]
    AI --> API
```

Exchange feeds and external-provider content are data, not instructions. AI is outside the signal-evaluation path. Events, evidence, and notification intent must be durable before any external notification is sent.

### Proposed stack

| Component | Choice and responsibility |
| --- | --- |
| Frontend | Vite, React, TypeScript: rule management, event history, evidence, and feed health. Live updates come from our backend. |
| HTTP API | Python, FastAPI, Pydantic: authentication, per-user authorization, validated rules, event queries, live updates, and health endpoints. |
| Ingestion | Python `asyncio`: shared Hyperliquid WebSocket subscriptions and REST reconciliation. |
| Rule engine | Deterministic Python: in-memory windows, incremental evaluation, quality checks, and runtime checkpoints. |
| Storage | PostgreSQL, SQLAlchemy 2, Alembic: market observations, immutable rule versions, events, evidence, and delivery records. Each concurrent task owns its database session. |
| Notifications | Separate worker with a transactional outbox, retries with backoff, and idempotency per event/channel/recipient. |
| AI assistance | LangChain and Pydantic structured outputs behind a `ModelProvider` interface; provider/model selection follows evaluation. |
| Delivery | Docker, CI for lint/type checks/tests/migrations, TLS, managed secrets, verified database backups, and operational metrics. |

Redis, pgvector, object storage for large raw datasets, and a full OpenTelemetry setup are later options if scale or measured usage requires them.

## Market data and interpretation

The current service uses `activeAssetCtx`, `l2Book`, `trades`, and candles on demand, with REST `metaAndAssetCtxs` for context bootstrap and REST book/candle snapshots for cold reads. New markets and deployers still require validation of channel behavior, fields, cadences, and API limits.

| Data | Required interpretation |
| --- | --- |
| Price | Store mark, oracle, bid, ask, and mid separately. Mark price is not necessarily executable. A rule names the reference it compares. |
| OI | Preserve units and calculate changes from compatible, valid samples over a defined window. A price tick does not establish that OI was updated. |
| Funding | Preserve the rate, period, and observation time; distinguish observed rates, predictions, and settled payments. |
| Liquidity | Check spread in basis points, depth for a configurable reference notional, book freshness, and activity. A snapshot does not guarantee execution liquidity. |
| HIP-3 context | Use deployer-specific evidence and session metadata. Do not infer oracle mode from the clock, spread, or price divergence. |
| Underlying stocks | Do not label a perpetual's `prevDayPx` as the stock's official previous close. Licensed stock data and calendars are a later integration. |

### HIP-3 feasibility gate

The reference proposes investigating trade[XYZ] as a possible first deployer; this is not a final selection. Determine whether an explicit external/internal oracle state is programmatically accessible, timestamped, and stable.

Use `EXTERNO` or `INTERNO` only with direct, validated evidence. Otherwise expose `NO_VERIFICADO` consistently in the web app, API, and notifications. Observable session transitions, spread changes, and reference-price divergence can still be reported, but cannot establish an oracle-mode switch. Findings for one deployer must not be generalized to others.

## Rule and alert lifecycle

1. **Request and create.** An explicit user request lets the agent produce typed predicates and activate the immutable rule version directly. The backend validates markets, units, windows, and thresholds. Preview-only requests remain inactive; read-only analysis does not authorize mutations.
2. **Warm up.** Load active rules, index them by market and dependent metric, and build the required observation windows. Incomplete windows remain unknown.
3. **Ingest.** Stamp reception time, validate identity and schema, handle duplicates/out-of-order observations according to channel guarantees, and update recent state. Batch sample persistence outside the critical evaluation path.
4. **Evaluate.** Reevaluate affected rules on relevant metric changes and timers for persistence, stale data, and cooldown. Each field has its own freshness budget.
5. **Apply quality policy.** Evaluate the condition and devil's advocate checks separately. Missing mandatory observations produce `data_unknown`, never an automatic `false` result.
6. **Persist atomically.** Write the event, sufficient reproducible evidence, and outbox entry in one transaction. A unique event fingerprint based on rule version, trigger episode, and transition prevents duplicate events.
7. **Deliver and trace.** The worker sends asynchronously, records attempts/results, and links to the event detail. Later edits to a rule do not change historical evidence.
8. **Recover honestly.** On restart or disconnect, mark affected data unreliable, reconcile snapshots, and rebuild windows before dependent evaluation resumes. Do not present unobserved transitions as live alerts. Any recovery-detected event must expose the gap and delayed detection.

Delivery to external providers is **at least once**; retries and database uniqueness do not guarantee exactly-once Telegram delivery.

## Devil's advocate checks

Each check returns `PASS`, `WARN`, `BLOCK`, or `UNKNOWN`, together with a reason code and evidence. Thresholds must be calibrated per market, session, and reference notional.

| Check | Example reason codes |
| --- | --- |
| Per-field freshness | `STALE_PRICE`, `STALE_OI`, `STALE_BOOK` |
| Feed integrity and sufficient observations | `GAP`, `OUT_OF_ORDER`, `CLOCK_SKEW` |
| Spread and available depth | `WIDE_SPREAD`, `THIN_DEPTH` |
| Comparable mark/mid/oracle references | `MARK_ORACLE_DIVERGENCE`, `MID_ORACLE_DIVERGENCE` |
| Activity and temporal confirmation | `ISOLATED_TRADE`, `THIN_VOLUME` |
| Compatible OI samples and dated funding | `OI_UNCONFIRMED`, `FUNDING_UNDATED` |
| Session context and verified oracle observations | `SESSION_TRANSITION`, `ORACLE_MODE_UNKNOWN` |
| Contradictory supporting signals | `PRICE_UP_OI_DOWN`, `BREAKOUT_NO_BOOK_SUPPORT` |

Policies support notification, notification with warnings, or blocking for inadequate data. Missing indispensable data blocks confirmation; an unobservable contextual state stays unknown. The UI separates condition truth (`true / false / unknown`) from quality (`valid / warned / blocked`). Contradictory signals are facts to explain, not an invented probability of trading success.

## Data model and auditability

Instrument identity is `(network, dex, coin)`, never `coin` alone. Store economic values as PostgreSQL `NUMERIC` / Python `Decimal`, with units and schema versions. Preserve source timestamps when available, UTC reception/evaluation timestamps, and measurable latency or clock skew.

| Tables | Purpose |
| --- | --- |
| `markets` | Instrument identity, units, applicable timezone/session, and metadata version. |
| `market_samples` | Per-channel values, provenance, timestamps, available sequence/IDs, gap flags, and traceable payloads. |
| `regime_observations` | Source, observation time, verified/unknown state, confirmation evidence, and reason. |
| `alert_rules`, `alert_rule_versions` | Owner, lifecycle, policy, cooldown, delivery limits, and immutable typed definitions with an activation timestamp (`confirmed_at`). |
| `rule_runtime` | Last evaluation state, persistence duration, last trigger, cooldown, processed-data cursor, and restart checkpoints. |
| `alert_events`, `alert_evidence` | Rule version, transition, fingerprint, event status, observations, predicate results, quality decisions, and reason codes. |
| `notification_outbox`, `deliveries` | Notification intent, recipient/channel, attempts, provider results, and unique `(event_id, channel, recipient)`. |

Proposed event statuses are `candidate`, `warn`, `confirmed`, `blocked`, and `data_unknown`. Index samples by market/time and rules/events by owner/status; partition samples only when volume warrants it.

Initial retention proposals are 7–30 days for detailed samples and 6–12 months for aggregates, subject to measured volume and data rights. Rule versions and event evidence remain while the account is active plus a defined deletion period. Avoid indefinite storage of full order books. Support user export/deletion, separate personal data from public market data, and encrypt stored Telegram tokens.

## AI boundaries and security

- Evaluate providers on a fixed Spanish-language corpus covering ambiguous rules, units, HIP-3 references, rejection cases, and evidence-based explanations. Measure structured-output correctness, invented figures, latency, cost, and data policies.
- Start with an API provider if it passes the corpus. Consider self-hosted vLLM only when measured cost, privacy, or operational requirements justify it; retain the same interface and tests.
- Generate explanations from persisted evidence. Fall back to a deterministic template when AI output lacks support, disagrees with recorded values, or the provider is unavailable. Version prompts/providers as explanation metadata.
- Enforce ownership on every API query and live channel; validate inputs, rate-limit access, use HTTPS, minimize personal data, protect secrets, and verify backups. The MVP has no trading credentials or execution endpoints.

## Validation and performance targets

Use versioned sample fixtures and a simulated clock for deterministic replay. Acceptance checks cover one event per eligible transition, explanations matching recorded evidence, blocking when mandatory data is stale, reconnect/restart recovery, cooldowns, and competing workers. Include exact thresholds, missing OI samples, zero spread, isolated trades, and backwards timestamps.

Measure p50/p95/p99 across `received_at → evaluated_at → committed_at → delivered_at`, plus `source_at → received_at` only when source timestamps are comparable. Initial engineering targets, subject to a defined load and benchmarks:

- Evaluation p95 below **100 ms** from reception with rules preloaded.
- Persistence and enqueue p95 below **300 ms** under the agreed workload.
- Burst tests reporting p95/p99 latency and loss/duplication rates.

Telegram delivery time and exchange publication cadence are external factors. Keep SQL and AI calls out of per-tick evaluation without sacrificing durable event evidence or gap detection.

## Delivery roadmap

| Phase | Work | Exit criterion |
| --- | --- | --- |
| **0 — Feasibility** | Small ingestion prototype/dataset; verify market identity, fields, cadence, freshness, API limits, permissions, and oracle-state observability. | A per-market feasibility matrix and go/no-go decision, including explicit `NO_VERIFICADO` behavior where necessary. |
| **1 — Deterministic core** | Schema/migrations, subscriptions, samples, windowed evaluation, quality checks, replay, recovery, and auditable events. | Reproducible console alerts with recorded evidence, without AI. |
| **2 — Product beta** | FastAPI, authentication, React UI, agent-driven rule creation, event history, Telegram/outbox, live updates, and observability. | A beta for a small user group with traceable alert and delivery behavior. |
| **3 — AI assistance** | Natural-language rule proposals and controlled explanations; compare providers using the fixed corpus. | User-requested rules and evidence-grounded explanations passing the evaluation set. |

Progress depends on observed feed quality, reproducible false-alarm and outage tests, verified oracle context or an explicit unknown fallback, and demonstrated usefulness of caution-rich alerts compared with conventional alerts.

## Open decisions

- Initial network, HIP-3 deployer, and market basket.
- Reliable oracle-state source and access permissions; session calendars and timezone rules.
- Per-field freshness budgets, thresholds, default quality policy, and recovery behavior.
- Target number of users/rules/markets and expected burst load.
- Retention, hosting and notification costs, and eventual stock-data licensing.
- Production model selection, provider data policy, cost limits, rate limiting, and outage behavior.

## Reference documentation

These are the documentation links cited by the architecture PDF. Their current contracts and provider-specific behavior must be verified during Phase 0 and implementation.

- Hyperliquid: [WebSocket subscriptions](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions), [WebSocket lifecycle](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket), [perpetuals info endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals), and [API limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits).
- trade[XYZ]: [oracle-price mechanics](https://docs.trade.xyz/perpetuals/mechanics/oracle-price).
- AI: [OpenRouter quickstart](https://openrouter.ai/docs/quickstart), [OpenRouter streaming](https://openrouter.ai/docs/api/reference/streaming), [OpenRouter transcription](https://openrouter.ai/docs/guides/overview/multimodal/stt), [LangChain structured output](https://docs.langchain.com/oss/python/langchain/structured-output), and [vLLM structured outputs](https://docs.vllm.ai/en/stable/features/structured_outputs).
- Storage: [SQLAlchemy session basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html) and optional [pgvector](https://github.com/pgvector/pgvector).
