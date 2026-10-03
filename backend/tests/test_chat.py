import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.application.chat import ChatMessage, ChatNotConfigured, ChatProviderError, OpenRouterChatClient
from app.config import Settings
from main import app


class OpenRouterChatClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_text_and_sends_history_with_attribution(self):
        captured = {}

        async def upstream(request: httpx.Request) -> httpx.Response:
            captured["headers"] = request.headers
            captured["payload"] = json.loads(request.content)
            stream = (
                'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, text=stream, headers={"Content-Type": "text/event-stream"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
            client = OpenRouterChatClient(
                Settings(
                    openrouter_api_key="test-key",
                    openrouter_model="test/model",
                    openrouter_site_url="https://example.test",
                    openrouter_app_name="Test Radar",
                ),
                http,
            )
            stream = await client.open_stream(
                (ChatMessage("user", "First question"), ChatMessage("assistant", "First answer"))
            )
            result = "".join([token async for token in stream])

        self.assertEqual(result, "Hello world")
        self.assertEqual(captured["headers"]["authorization"], "Bearer test-key")
        self.assertEqual(captured["headers"]["http-referer"], "https://example.test")
        self.assertEqual(captured["headers"]["x-openrouter-title"], "Test Radar")
        self.assertEqual(captured["payload"]["model"], "test/model")
        self.assertTrue(captured["payload"]["stream"])
        self.assertEqual(captured["payload"]["messages"][1]["content"], "First question")
        self.assertEqual(captured["payload"]["messages"][2]["content"], "First answer")

    async def test_missing_configuration_and_provider_errors_are_explicit(self):
        client = OpenRouterChatClient(Settings(openrouter_api_key=None))
        with self.assertRaises(ChatNotConfigured):
            await client.open_stream((ChatMessage("user", "Hello"),))
        await client.close()

        async def rejected(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(rejected)) as http:
            client = OpenRouterChatClient(Settings(openrouter_api_key="test-key"), http)
            with self.assertRaisesRegex(ChatProviderError, "HTTP 429"):
                await client.open_stream((ChatMessage("user", "Hello"),))

    async def test_streaming_error_closes_the_upstream_response(self):
        async def failed_stream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text='data: {"error":{"message":"provider disconnected"},"choices":[]}\n\n',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(failed_stream)) as http:
            client = OpenRouterChatClient(Settings(openrouter_api_key="test-key"), http)
            stream = await client.open_stream((ChatMessage("user", "Hello"),))
            with self.assertRaisesRegex(ChatProviderError, "streaming error"):
                _ = [token async for token in stream]

    async def test_transcribes_audio_with_the_dedicated_model(self):
        captured = {}

        async def upstream(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["payload"] = json.loads(request.content)
            return httpx.Response(200, json={"text": "  alerta de bitcoin  "})

        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
            client = OpenRouterChatClient(
                Settings(
                    openrouter_api_key="test-key",
                    openrouter_transcription_model="test/transcriber",
                    openrouter_transcription_language="es",
                ),
                http,
            )
            result = await client.transcribe(b"abc", "webm")

        self.assertEqual(result, "alerta de bitcoin")
        self.assertTrue(captured["url"].endswith("/audio/transcriptions"))
        self.assertEqual(captured["payload"]["model"], "test/transcriber")
        self.assertEqual(captured["payload"]["input_audio"], {"data": "YWJj", "format": "webm"})
        self.assertEqual(captured["payload"]["language"], "es")


class ChatHTTPTests(unittest.TestCase):
    def setUp(self):
        self.load_rules = self.enterContext(
            patch("main.EvaluationWorker._load_rules_and_warm_up", new=AsyncMock(return_value=[]))
        )

    def test_chat_keeps_endpoint_and_streams_provider_response(self):
        async def tokens():
            yield "Real "
            yield "response"

        with TestClient(app) as client, patch.object(
            app.state.chat,
            "open_stream",
            new=AsyncMock(return_value=tokens()),
        ) as provider:
            response = client.post(
                "/api/chat",
                json={
                    "message": "  Follow up  ",
                    "history": [
                        {"role": "user", "content": "First question"},
                        {"role": "assistant", "content": "First answer"},
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "Real response")
        messages = provider.await_args.args[0]
        self.assertEqual([message.role for message in messages], ["user", "assistant", "user"])
        self.assertEqual(messages[-1].content, "Follow up")

    def test_chat_validates_input_and_reports_missing_configuration(self):
        with TestClient(app) as client, patch.object(app.state.chat, "_api_key", None):
            self.assertEqual(client.post("/api/chat", json={"message": "   "}).status_code, 422)
            response = client.post("/api/chat", json={"message": "Hello"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "OpenRouter is not configured")

    def test_audio_endpoint_validates_and_transcribes_browser_recordings(self):
        with TestClient(app) as client, patch.object(
            app.state.chat,
            "transcribe",
            new=AsyncMock(return_value="alerta de bitcoin"),
        ) as provider:
            response = client.post(
                "/api/chat/transcribe",
                content=b"webm-audio",
                headers={"Content-Type": "audio/webm;codecs=opus"},
            )
            unsupported = client.post(
                "/api/chat/transcribe",
                content=b"audio",
                headers={"Content-Type": "application/octet-stream"},
            )
            empty = client.post(
                "/api/chat/transcribe",
                content=b"",
                headers={"Content-Type": "audio/webm"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"text": "alerta de bitcoin"})
        provider.assert_awaited_once_with(b"webm-audio", "webm")
        self.assertEqual(unsupported.status_code, 415)
        self.assertEqual(empty.status_code, 400)


if __name__ == "__main__":
    unittest.main()
