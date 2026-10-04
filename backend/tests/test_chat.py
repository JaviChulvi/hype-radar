import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.application.chat import OpenRouterTranscriptionClient
from app.config import Settings
from main import app


class OpenRouterTranscriptionClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcribes_audio_with_the_dedicated_model(self):
        captured = {}

        async def upstream(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["payload"] = json.loads(request.content)
            return httpx.Response(200, json={"text": "  alerta de bitcoin  "})

        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
            client = OpenRouterTranscriptionClient(
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
            yield 'data: {"type":"text","text":"Real response"}\n\n'
            yield 'data: {"type":"done"}\n\n'

        with (
            TestClient(app) as client,
            patch.object(
                app.state.chat,
                "open_stream",
                new=AsyncMock(return_value=tokens()),
            ) as provider,
        ):
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
        self.assertIn('"text":"Real response"', response.text)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        messages = provider.await_args.args[0]
        self.assertEqual([message.role for message in messages], ["user", "assistant", "user"])
        self.assertEqual(messages[-1].content, "Follow up")

    def test_chat_validates_input_and_reports_missing_configuration(self):
        with TestClient(app) as client, patch.object(app.state.chat.settings, "openrouter_api_key", None):
            self.assertEqual(client.post("/api/chat", json={"message": "   "}).status_code, 422)
            response = client.post("/api/chat", json={"message": "Hello"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "OpenRouter is not configured")

    def test_audio_endpoint_validates_and_transcribes_browser_recordings(self):
        with (
            TestClient(app) as client,
            patch.object(
                app.state.transcription,
                "transcribe",
                new=AsyncMock(return_value="alerta de bitcoin"),
            ) as provider,
        ):
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
