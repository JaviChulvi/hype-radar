# hype-radar

**Market alerts with evidence, liquidity context, and a devil's advocate.**

Hype Radar is a market-monitoring platform designed for Hyperliquid perpetuals and HIP-3 markets. It brings price, open interest, funding, and order-book conditions into one alert workflow, with the context needed to assess each event.

## Product vision

Turn a market hypothesis into a precise rule, follow it in real time, and receive an alert that explains what happened and how reliable the supporting data are. Hype Radar will pair every signal with its observations, liquidity conditions, and relevant session context, so users can inspect the event behind the notification.

The **devil's advocate** is central to the experience. When a condition is met, the system will also examine stale observations, thin liquidity, conflicting signals, and uncertain oracle context. Users will be able to see both the evidence supporting an alert and the reasons to treat it with caution.

AI assistance will make rules easier to express and events easier to understand. A deterministic engine will evaluate the confirmed conditions, while explanations stay grounded in the recorded market data. Users retain control over their rules and trading decisions.

## Core capabilities

- **Custom market conditions.** Combine price, open interest (OI), and funding predicates with explicit windows, thresholds, persistence requirements, and cooldowns.
- **Visual and natural-language rule creation.** Build conditions in a structured editor or describe them in plain language, then review and confirm the exact rule before activation.
- **Liquidity-aware alerts.** Evaluate spread, order-book depth, activity, and data freshness alongside the primary condition, with configurable warning and blocking policies.
- **HIP-3 market context.** Surface session transitions, reference-price divergence, and verified oracle-regime observations for the selected deployer.
- **Web and Telegram notifications.** Receive event summaries with supporting evidence, quality warnings, and a link to the full breakdown.
- **An auditable event timeline.** Inspect the rule version, observations, predicate results, quality decisions, and delivery history behind each event.
- **Live market and feed health.** Track monitored instruments, observation freshness, connection gaps, and recovery status from the web app.

### Target workflow

> Alert me when HYPE moves above my price threshold, open interest rises over the last 15 minutes, and the spread stays below my limit.

The user reviews the resulting conditions and activates the rule. When they are met, the alert includes the observed values, timestamps, and quality checks. If the data are incomplete or liquidity is weak, the configured policy determines whether to warn or block the notification, with the reason visible in the event history.

The first release will focus on a small basket of markets, potentially HYPE and selected instruments from one HIP-3 deployer. Delivery starts with the deterministic engine and visual editor, followed by natural-language assistance. The roadmap prioritizes reliable detection, useful context, and a complete evidence trail.

## Run locally

The chart streams Hyperliquid perpetual candles for BTC, ETH, SP500, XYZ100, and BRENTOIL through FastAPI. Use the shadcn/ui market and time interval selectors in the header to switch markets and candle durations. Supported intervals are `1m`, `3m`, `5m` (default), `15m`, `30m`, `1h`, `2h`, `4h`, `8h`, `12h`, `1d`, `3d`, `1w`, and `1M` (one month), matching the [Hyperliquid candle subscription API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions). SP500, XYZ100, and BRENTOIL use the trade[XYZ] markets (`xyz:SP500`, `xyz:XYZ100`, and `xyz:BRENTOIL`). One shared ingestion task per requested market/interval pair serves every browser; the latest 200 candles per pair stay in memory.

The live order book follows the selected market alongside the chart (below it on mobile), with cumulative bid/ask depth, spread, exchange-side price grouping, and base/USD size units. It uses the [Hyperliquid `l2Book` snapshot stream](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions) with `fast: true` through `/ws/book?symbol=BTC&precision=5`. Grouping supports 5 (default), 4, 3, or 2 significant digits. Each market/grouping feed is shared across viewers and released when its last viewer leaves. The fast stream provides up to 5 levels per side, with updates measured around 0.5 seconds apart (exchange-controlled cadence). The panel shows five levels per side. A recent-trades tape fills the column below it with the latest 40 executions, newest first, showing buy/sell side, price, size in the selected base/USD unit, and local time. The `trades` subscription shares the book’s upstream connection; duplicate executions are removed and both views reset when reconnecting or changing markets. Missing or disconnected books are cleared while reconnecting.

The right-side chat panel next to the order book is a session-only demo: `POST /api/chat` accepts a nonblank `message` of up to 4,000 characters and streams a dummy text reply after a short simulated thinking delay. No AI provider, API key, market analysis, alert creation, or conversation storage is involved. Enter sends; Shift+Enter adds a line. Replies can be stopped or retried after a failure, and New chat clears the conversation. The panel respects reduced-motion settings and stacks below the order book on mobile.

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node.js 22.12+.

Start the backend from the repository root:

```sh
cd backend
uv sync
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

In a second terminal, start the frontend from the repository root:

```sh
cd frontend
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. The frontend proxies `/ws/candles?symbol=BTC&interval=5m` and `/health` to the backend. The `symbol` parameter accepts `BTC` (default), `ETH`, `SP500`, `XYZ100`, or `BRENTOIL`; the `interval` parameter accepts the intervals above. Unsupported symbols or intervals are rejected. Run one backend worker to retain one exchange connection per market/interval pair. A market/interval feed starts when first requested and stops when its last browser client disconnects, releasing the upstream connection and cached history. Browsers viewing the same pair continue to share one feed. History is loaded on first request and refreshed after upstream reconnection; the chart preserves its last data while reconnecting and clears it when switching markets or intervals. `/health` reports feed status per requested market/interval pair, keyed as `BTC:5m`, for example.

To check the frontend's types and production build, run `npm run build` in `frontend/`. No API keys are required.

The header's market strip shows each picker's instrument and its 24-hour perpetual mark-price change, calculated as `(markPx / prevDayPx - 1) * 100` from Hyperliquid's `metaAndAssetCtxs` (native and `xyz` markets). This is independent of the candle interval and is not an underlying stock's official daily return. Clicking an instrument selects its chart. The frontend refreshes `/api/markets` every 15 seconds after each response; the backend shares results for 10 seconds. Missing or failed market data appears as `—`, with automatic retry.

The Select component is adapted from [shadcn/ui](https://ui.shadcn.com/docs/components/radix/select) to the existing CSS. Market icons are bundled locally from Hyperdash's public `/icons/` assets: [BTC](https://hyperdash.com/icons/BTC.svg), [ETH](https://hyperdash.com/icons/ETH.svg), [SP500](https://hyperdash.com/icons/SP500.png), [XYZ100](https://hyperdash.com/icons/XYZ100.png), and [BRENTOIL](https://hyperdash.com/icons/BRENTOIL.png).

Run the backend regression tests with `uv run python -m unittest -v` in `backend/`.

## Deterministic alert core

The alert core is a separate process from the demo HTTP API. It stores normalized market samples,
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

Register that example rule and start the live evaluation worker:

```sh
uv run python -m app.workers.seed_rule tests/fixtures/hype_breakout.json
uv run python -m app.workers.evaluator
```

The worker opens one normalized Hyperliquid connection per active market, subscribes to
`activeAssetCtx` and `l2Book`, rebuilds required windows from stored samples after restart, evaluates
on observations and timers, and writes emitted events to the console and PostgreSQL. It currently
loads active rules at startup, so restart it after activating a different rule version. Notification
delivery, rule-management HTTP endpoints, authentication, and the visual rule editor remain later
work; outbox rows are durable but are not sent yet.

Run the isolated PostgreSQL integration test with:

```sh
docker compose --profile test up -d postgres-test
cd backend
DATABASE_URL=postgresql+psycopg://hype_radar:hype_radar@127.0.0.1:55432/hype_radar_test \
  uv run alembic upgrade head
TEST_DATABASE_URL=postgresql+psycopg://hype_radar:hype_radar@127.0.0.1:55432/hype_radar_test \
  uv run python -m unittest -v tests.test_persistence
```

## Proposed architecture

Build a modular monolith with separate ingestion, evaluation, API, and notification processes. Share exchange subscriptions per market instead of opening a connection per user.

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
| Frontend | Vite, React, TypeScript: typed forms, rules, event history, evidence, and feed health. Live updates come from our backend. |
| HTTP API | Python, FastAPI, Pydantic: authentication, per-user authorization, validated rules, event queries, live updates, and health endpoints. |
| Ingestion | Python `asyncio`: shared Hyperliquid WebSocket subscriptions and REST reconciliation. |
| Rule engine | Deterministic Python: in-memory windows, incremental evaluation, quality checks, and runtime checkpoints. |
| Storage | PostgreSQL, SQLAlchemy 2, Alembic: market observations, immutable rule versions, events, evidence, and delivery records. Each concurrent task owns its database session. |
| Notifications | Separate worker with a transactional outbox, retries with backoff, and idempotency per event/channel/recipient. |
| AI assistance | LangChain and Pydantic structured outputs behind a `ModelProvider` interface; provider/model selection follows evaluation. |
| Delivery | Docker, CI for lint/type checks/tests/migrations, TLS, managed secrets, verified database backups, and operational metrics. |

Redis, pgvector, object storage for large raw datasets, and a full OpenTelemetry setup are later options if scale or measured usage requires them.

## Market data and interpretation

The initial ingestion plan uses `activeAssetCtx`, `bbo` or `l2Book`, and `trades` only when a check needs them. REST `metaAndAssetCtxs` is proposed for startup and reconciliation. Phase 0 must verify channel behavior, fields, cadences, `dex` handling, and API limits for every selected market.

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

1. **Draft and confirm.** The visual editor or optional AI parser produces typed predicates. The backend validates markets, units, windows, and thresholds. The user reviews and confirms an immutable rule version; AI cannot activate or modify it independently.
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
| `alert_rules`, `alert_rule_versions` | Owner, lifecycle, policy, cooldown, delivery limits, and immutable typed definitions with user confirmation. |
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
| **2 — Product beta** | FastAPI, authentication, React UI, visual editor, event history, Telegram/outbox, live updates, and observability. | A beta for a small user group with traceable alert and delivery behavior. |
| **3 — AI assistance** | Natural-language rule proposals and controlled explanations; compare providers using the fixed corpus. | User-confirmed rules and evidence-grounded explanations passing the evaluation set. |

Progress depends on observed feed quality, reproducible false-alarm and outage tests, verified oracle context or an explicit unknown fallback, and demonstrated usefulness of caution-rich alerts compared with conventional alerts.

## Open decisions

- Initial network, HIP-3 deployer, and market basket.
- Reliable oracle-state source and access permissions; session calendars and timezone rules.
- Per-field freshness budgets, thresholds, default quality policy, and recovery behavior.
- Target number of users/rules/markets and expected burst load.
- Retention, hosting and notification costs, and eventual stock-data licensing.
- AI provider/model, data policy, and behavior during provider outages.

## Reference documentation

These are the documentation links cited by the architecture PDF. Their current contracts and provider-specific behavior must be verified during Phase 0 and implementation.

- Hyperliquid: [WebSocket subscriptions](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions), [WebSocket lifecycle](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket), [perpetuals info endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals), and [API limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits).
- trade[XYZ]: [oracle-price mechanics](https://docs.trade.xyz/perpetuals/mechanics/oracle-price).
- AI: [LangChain structured output](https://docs.langchain.com/oss/python/langchain/structured-output) and [vLLM structured outputs](https://docs.vllm.ai/en/stable/features/structured_outputs).
- Storage: [SQLAlchemy session basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html) and optional [pgvector](https://github.com/pgvector/pgvector).
