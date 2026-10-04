import asyncio
import json
import os
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import ValidationError
from sqlalchemy import delete, select

from app.application.agent import ChatAgentService
from app.application.alerts import AlertService, AlertSpec
from app.application.chat import ChatMessage, ChatNotConfigured
from app.application.market_data import MarketDataService, MarketUnavailable
from app.config import Settings
from app.domain.observations import MarketObservation, Metric
from app.ingestion.client import HyperliquidClient
from app.persistence import create_engine, create_session_factory
from app.persistence.models import AlertEventModel, AlertRuleModel, AlertRuleVersionModel, RuleRuntimeModel
from app.workers.evaluator import EvaluationWorker
from feed import ContextFeed, SharedFeed


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def spec():
    return AlertSpec(
        name="BTC level",
        market="BTC",
        predicates=[{"type": "threshold", "metric": "mark_price", "operator": "gt", "threshold": "100"}],
    )


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.markets = MarketDataService(HyperliquidClient())

    async def asyncTearDown(self):
        await self.markets.close()

    async def test_real_langchain_loop_executes_tool_and_emits_text(self):
        model = ToolModel(
            responses=[
                AIMessage(content="", tool_calls=[{"name": "list_markets", "args": {}, "id": "call_1"}]),
                AIMessage(content="BTC is supported."),
            ]
        )
        agent = ChatAgentService(Settings(openrouter_api_key=None), self.markets, None, model=model)
        events = [
            json.loads(frame.removeprefix("data: "))
            async for frame in await agent.open_stream([ChatMessage("user", "What can I monitor?")])
        ]
        self.assertTrue(any(event["type"] == "tool_start" and event["name"] == "list_markets" for event in events))
        self.assertEqual("".join(event["text"] for event in events if event["type"] == "text"), "BTC is supported.")
        self.assertEqual(events[-1], {"type": "done"})

    async def test_missing_configuration_and_tool_validation(self):
        agent = ChatAgentService(Settings(openrouter_api_key=None, _env_file=None), self.markets, None)
        with self.assertRaises(ChatNotConfigured):
            await agent.open_stream([ChatMessage("user", "Hello")])
        tools = {tool.name: tool for tool in agent.tools(uuid4())}
        self.assertEqual(len(tools), 10)
        with self.assertRaises(ValidationError):
            await tools["get_candles"].ainvoke({"market": "HYPE"})
        with self.assertRaises(ValidationError):
            await tools["get_candles"].ainvoke({"market": "BTC", "limit": 10000})
        with self.assertRaises(ValidationError):
            await tools["list_alerts"].ainvoke({"offset": -1})
        with self.assertRaises(ValidationError):
            AlertSpec(
                name="Invalid",
                market="BTC",
                predicates=[{"type": "threshold", "metric": "mark_price", "operator": "gt", "threshold": "NaN"}],
            )

    async def test_model_tool_loop_is_bounded(self):
        model = ToolModel(
            responses=[AIMessage(content="", tool_calls=[{"name": "list_markets", "args": {}, "id": "loop"}])]
        )
        agent = ChatAgentService(Settings(openrouter_api_key=None), self.markets, None, model=model)
        with self.assertLogs("app.application.agent", level="ERROR"):
            frames = [
                json.loads(frame.removeprefix("data: "))
                async for frame in await agent.open_stream([ChatMessage("user", "List markets")])
            ]
        self.assertEqual(frames[-1]["type"], "error")
        self.assertLessEqual(sum(frame["type"] == "tool_start" for frame in frames), 6)

    async def test_provider_failure_is_an_error_event_not_success(self):
        agent = ChatAgentService(
            Settings(openrouter_api_key=None),
            self.markets,
            None,
            model=ToolModel(responses=[AIMessage(content="Unused")]),
        )

        class FailedGraph:
            async def astream(self, *args, **kwargs):
                raise RuntimeError("Disconnected")
                yield

        with self.assertLogs("app.application.agent", level="ERROR"):
            events = [
                json.loads(frame.removeprefix("data: "))
                async for frame in agent._events(FailedGraph(), [ChatMessage("user", "Hello")])
            ]
        self.assertEqual([event["type"] for event in events], ["error"])


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class AgentAlertTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = create_engine(Settings(database_url=os.environ["TEST_DATABASE_URL"]))
        self.sessions = create_session_factory(self.database)
        self.markets = MarketDataService(HyperliquidClient())
        with patch("app.workers.evaluator.create_engine", return_value=self.database):
            self.worker = EvaluationWorker(self.markets, tick_interval=0.01)
        self.alerts = AlertService(self.sessions, self.markets, self.worker)
        self.rule_ids = []

    async def asyncTearDown(self):
        await self.markets.close()
        async with self.sessions() as session, session.begin():
            versions = select(AlertRuleVersionModel.id).where(AlertRuleVersionModel.rule_id.in_(self.rule_ids))
            await session.execute(delete(AlertEventModel).where(AlertEventModel.rule_version_id.in_(versions)))
            await session.execute(delete(AlertRuleModel).where(AlertRuleModel.id.in_(self.rule_ids)))
        await self.database.dispose()

    async def preview(self, request_id=None):
        preview = await self.alerts.preview(spec(), request_id or uuid4())
        self.rule_ids.append(UUID(preview["definition"]["rule_id"]))
        return preview

    async def test_creation_idempotency_expiry_and_pause_guard(self):
        request_id = uuid4()
        preview = await self.preview(request_id)
        preview_id = UUID(preview["preview_id"])
        rule_id = self.rule_ids[-1]
        self.assertEqual((await self.preview(request_id))["preview_id"], preview["preview_id"])
        with self.assertRaisesRegex(ValueError, "Draft alerts"):
            await self.alerts.set_status(rule_id, "active")
        async with self.sessions() as session:
            self.assertIsNone((await session.get(AlertRuleVersionModel, preview_id)).confirmed_at)
            self.assertEqual((await session.get(AlertRuleModel, rule_id)).status, "draft")
        first = await self.alerts.create(preview_id)
        self.assertEqual(first["status"], "active")
        results = await asyncio.gather(*(self.alerts.create(preview_id) for _ in range(3)))
        self.assertTrue(all(result == first for result in results))
        await self.preview(request_id)
        await self.alerts.set_status(rule_id, "paused")
        self.assertEqual((await self.alerts.create(preview_id))["status"], "paused")
        self.assertEqual((await self.alerts.list_alerts(status="paused"))["items"][0]["alert_id"], str(rule_id))
        expired = UUID((await self.preview())["preview_id"])
        async with self.sessions() as session, session.begin():
            (await session.get(AlertRuleVersionModel, expired)).created_at = datetime.now(UTC) - timedelta(hours=2)
        with self.assertRaisesRegex(ValueError, "expired"):
            await self.alerts.create(expired)

    async def test_rule_created_after_start_is_evaluated_and_pause_releases_feed(self):
        await self.worker.start()

        async def feed_run(feed):
            if isinstance(feed, ContextFeed):
                feed.apply({"markPx": "101", "openInterest": "1000"}, datetime.now(UTC))
            await asyncio.Event().wait()

        with patch.object(SharedFeed, "run", feed_run):
            task = asyncio.create_task(self.worker.run())
            try:
                await asyncio.sleep(0)
                preview_id = UUID((await self.preview())["preview_id"])
                rule_id = self.rule_ids[-1]
                result = await self.alerts.create(preview_id)
                self.assertEqual(result["monitoring"], "monitoring")
                async with asyncio.timeout(3):
                    while True:
                        async with self.sessions() as session:
                            event = await session.scalar(
                                select(AlertEventModel).where(
                                    AlertEventModel.rule_version_id == preview_id, AlertEventModel.status == "confirmed"
                                )
                            )
                            if event:
                                break
                        await asyncio.sleep(0.01)
                history = await self.alerts.list_alerts(view="history")
                self.assertIn(str(event.id), [item["event_id"] for item in history["items"]])
                past_history = await self.alerts.list_alerts(view="history", offset=len(history["items"]))
                self.assertEqual(past_history["items"], [])
                self.assertIsNone(past_history["next_offset"])
                agent = ChatAgentService(Settings(openrouter_api_key=None), self.markets, self.alerts)
                history_tool = next(tool for tool in agent.tools(uuid4()) if tool.name == "get_metric_history")
                observations = await history_tool.ainvoke({"market": "BTC", "metric": "mark_price"})
                self.assertTrue(observations["samples"])
                self.assertEqual(observations["coverage"], "observed_samples_only")
                evidence = await self.alerts.get_event(event.id)
                self.assertEqual(Decimal(evidence["evidence"]["predicates"][0]["evidence"]["value"]), Decimal("101"))
                paused = await self.alerts.set_status(rule_id, "paused")
                self.assertEqual(paused["monitoring"], "inactive")
                self.assertEqual(self.worker._feeds, {})
                self.assertEqual(self.markets._owners, {})
                await self.alerts.set_status(rule_id, "active")
                self.assertEqual(self.worker.monitoring(rule_id), "monitoring")
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_disconnected_activation_is_reconciled(self):
        await self.worker.start()

        async def feed_run(feed):
            await asyncio.Event().wait()

        with patch.object(SharedFeed, "run", feed_run):
            task = asyncio.create_task(self.worker.run())
            try:
                await asyncio.sleep(0)
                preview_id = UUID((await self.preview())["preview_id"])
                rule_id = self.rule_ids[-1]
                with patch.object(self.worker, "reload_rules", new=AsyncMock(side_effect=asyncio.CancelledError)):
                    with self.assertRaises(asyncio.CancelledError):
                        await self.alerts.create(preview_id)
                self.assertEqual(self.worker.monitoring(rule_id), "inactive")
                async with asyncio.timeout(7):
                    while self.worker.monitoring(rule_id) != "monitoring":
                        await asyncio.sleep(0.02)
                self.assertEqual([rule.rule_id for rule in self.worker.rules], [rule_id])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_resume_restarts_persistence_and_preserves_cooldown_and_sequence(self):
        for persistence, previous_trigger_age, wait_seconds in ((60, 600, 60), (0, 10, 290)):
            with self.subTest(persistence=persistence):
                alert_spec = spec().model_copy(update={"persistence_seconds": persistence, "cooldown_seconds": 300})
                preview = await self.alerts.preview(alert_spec, uuid4())
                rule_id = UUID(preview["definition"]["rule_id"])
                version_id = UUID(preview["preview_id"])
                self.rule_ids.append(rule_id)
                await self.alerts.create(version_id)
                now = datetime.now(UTC)
                before_pause = now - timedelta(seconds=120)
                last_triggered = now - timedelta(seconds=previous_trigger_age)
                async with self.sessions() as session, session.begin():
                    session.add(
                        RuleRuntimeModel(
                            rule_version_id=version_id,
                            condition_state="true",
                            true_since=before_pause,
                            last_triggered_at=last_triggered,
                            trigger_sequence=7,
                            episode_triggered=persistence == 0,
                            blocked_event_recorded=True,
                            last_processed_at=before_pause,
                            updated_at=before_pause,
                        )
                    )
                await self.alerts.set_status(rule_id, "paused")
                await self.alerts.set_status(rule_id, "active")
                rule = next(rule for rule in self.worker.rules if rule.id == version_id)

                async def evaluate(at, price, rule=rule):
                    self.worker._windows.add(
                        MarketObservation(
                            market=rule.market,
                            source="hyperliquid",
                            channel="activeAssetCtx",
                            observed_at=at,
                            received_at=at,
                            values={Metric.MARK_PRICE: Decimal(price)},
                        )
                    )
                    return await self.worker._service.evaluate(rule, evaluated_at=at)

                # The price went below the threshold while paused, then returned above it on resume.
                self.worker._windows.add(
                    MarketObservation(
                        market=rule.market,
                        source="hyperliquid",
                        channel="activeAssetCtx",
                        observed_at=now - timedelta(seconds=1),
                        received_at=now - timedelta(seconds=1),
                        values={Metric.MARK_PRICE: Decimal("90")},
                    )
                )
                resumed = await evaluate(now, "101")
                self.assertIsNone(resumed.event)
                self.assertEqual(resumed.runtime.true_since, now)
                self.assertFalse(resumed.runtime.episode_triggered)
                self.assertFalse(resumed.runtime.blocked_event_recorded)
                self.assertEqual(resumed.runtime.last_triggered_at, last_triggered)
                self.assertEqual(resumed.runtime.trigger_sequence, 7)
                # Retrying an already-applied resume must not restart the new persistence period.
                await self.alerts.set_status(rule_id, "active")
                waiting = await evaluate(now + timedelta(seconds=wait_seconds - 1), "101")
                self.assertIsNone(waiting.event)
                self.assertEqual(waiting.runtime.true_since, now)
                fired = await evaluate(now + timedelta(seconds=wait_seconds), "101")
                self.assertEqual(fired.event.status.value, "confirmed")
                self.assertEqual(fired.runtime.trigger_sequence, 8)
                self.assertEqual(fired.event.evidence["true_since"], now.isoformat())

    async def test_creation_tool_activates_without_confirmation_and_retries_once(self):
        request_id = uuid4()
        original = spec().model_dump(mode="json")
        regenerated = spec().model_dump(mode="json")
        regenerated["name"] = "Bitcoin above 100"
        regenerated["predicates"][0]["threshold"] = "100.0"
        model = ToolModel(
            responses=[
                message
                for attempt, payload in enumerate((original, regenerated))
                for message in (
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "create_alert",
                                "args": {"spec": {**payload, "market": market}},
                                "id": f"c_{attempt}_{market}",
                            }
                            for market in (("BTC", "ETH") if attempt == 0 else ("ETH", "BTC"))
                        ],
                    ),
                    AIMessage(content="Alerts created."),
                )
            ]
        )
        attempts = []
        for _ in range(2):
            # Recreate services too: the retry must rely on storage, not a response cache.
            alerts = AlertService(self.sessions, self.markets, self.worker)
            agent = ChatAgentService(Settings(openrouter_api_key=None), self.markets, alerts, model=model)
            frames = [
                json.loads(frame.removeprefix("data: "))
                async for frame in await agent.open_stream(
                    [ChatMessage("user", "Create BTC and ETH alerts above 100")], request_id=request_id
                )
            ]
            changes = [frame for frame in frames if frame["type"] == "alert_changed"]
            self.rule_ids.extend(UUID(change["alert_id"]) for change in changes)
            self.assertEqual(len(changes), 2)
            attempts.append({change["alert_id"] for change in changes})
            self.assertEqual({change["definition"]["market"]["coin"] for change in changes}, {"BTC", "ETH"})
            for changed in changes:
                self.assertEqual(changed["status"], "active")
                self.assertEqual(changed["definition"]["name"], original["name"])
                self.assertEqual(changed["definition"]["predicates"][0]["threshold"], "100")
            self.assertFalse(any(frame["type"] == "alert_preview" for frame in frames))
            self.assertEqual(frames[-1]["type"], "done")
        self.assertEqual(len(attempts[0]), 2)
        self.assertEqual(attempts[0], attempts[1])
        async with self.sessions() as session:
            rows = (await session.scalars(select(AlertRuleModel).where(AlertRuleModel.id.in_(self.rule_ids)))).all()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row.status == "active" for row in rows))

    async def test_creation_retry_resumes_remaining_alerts_and_reuses_equivalent_conditions(self):
        request_id = uuid4()
        btc = spec().model_dump(mode="json")
        btc["predicates"].append(
            {"type": "threshold", "metric": "open_interest", "operator": "gt", "threshold": "1000"}
        )
        btc["quality"] = {"max_spread_bps": "5"}
        eth = {**btc, "market": "ETH"}
        higher_btc = {**btc, "predicates": [{**btc["predicates"][0], "threshold": "200"}]}

        def new_tool():
            alerts = AlertService(self.sessions, self.markets, self.worker)
            agent = ChatAgentService(Settings(openrouter_api_key=None), self.markets, alerts)
            return next(tool for tool in agent.tools(request_id) if tool.name == "create_alert")

        async def create(tool, payload):
            result = json.loads(await tool.ainvoke({"spec": payload}))
            self.rule_ids.append(UUID(result["alert_id"]))
            return result

        first = await create(new_tool(), btc)
        # BTC committed before the connection broke. The retry starts with the remaining alerts.
        retry_tool = new_tool()
        second = await create(retry_tool, eth)
        third = await create(retry_tool, higher_btc)
        self.assertEqual(second["definition"]["market"]["coin"], "ETH")
        self.assertEqual(third["definition"]["predicates"][0]["threshold"], "200")
        self.assertEqual(len(set(self.rule_ids)), 3)

        regenerated = {
            **btc,
            "name": "Renamed BTC alert",
            "predicates": [{**item, "threshold": item["threshold"] + ".0"} for item in reversed(btc["predicates"])],
            "quality": {"max_spread_bps": "5.00"},
        }
        await self.alerts.set_status(UUID(first["alert_id"]), "paused")
        repeated = await create(new_tool(), regenerated)
        self.assertEqual(repeated["alert_id"], first["alert_id"])
        self.assertEqual(repeated["status"], "paused")
        self.assertEqual(repeated["definition"], first["definition"])
        self.assertEqual((await create(retry_tool, eth))["alert_id"], second["alert_id"])

    async def test_list_tool_reaches_and_pauses_alert_beyond_first_page(self):
        with patch.object(self.worker, "reload_rules", new=AsyncMock()):
            for _ in range(31):
                await self.alerts.create(UUID((await self.preview())["preview_id"]))
            agent = ChatAgentService(Settings(openrouter_api_key=None), self.markets, self.alerts)
            tools = {tool.name: tool for tool in agent.tools(uuid4())}
            first = await tools["list_alerts"].ainvoke({"market": "BTC", "status": "active"})
            self.assertEqual(len(first["items"]), 30)
            self.assertTrue(first["has_more"])
            second = await tools["list_alerts"].ainvoke(
                {"market": "BTC", "status": "active", "offset": first["next_offset"]}
            )
            self.assertFalse(second["has_more"])
            self.assertIsNone(second["next_offset"])
            self.assertEqual([item["alert_id"] for item in second["items"]], [str(self.rule_ids[0])])
            await tools["set_alert_status"].ainvoke({"alert_id": str(self.rule_ids[0]), "status": "paused"})
            async with self.sessions() as session:
                self.assertEqual((await session.get(AlertRuleModel, self.rule_ids[0])).status, "paused")

    async def test_preview_tool_stays_inactive_without_confirmation_artifact(self):
        request_id = uuid4()
        preview = await self.preview(request_id)
        agent = ChatAgentService(Settings(openrouter_api_key=None), self.markets, self.alerts)
        preview_tool = next(tool for tool in agent.tools(request_id) if tool.name == "preview_alert")
        with patch.object(self.markets, "get_snapshot", new=AsyncMock(side_effect=MarketUnavailable("offline"))):
            result = await preview_tool.ainvoke({"spec": spec().model_dump(mode="json")})
        self.assertEqual(result["preview_id"], preview["preview_id"])
        self.assertNotIn("requires_confirmation", result)
        self.assertEqual(result["data_warning"], "offline")
        async with self.sessions() as session:
            self.assertEqual((await session.get(AlertRuleModel, self.rule_ids[-1])).status, "draft")
