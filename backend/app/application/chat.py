import base64
import logging
from dataclasses import dataclass

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str


class ChatProviderError(RuntimeError):
    """The configured chat provider could not produce a valid response."""


class ChatNotConfigured(ChatProviderError):
    """The chat provider is missing required configuration."""


class OpenRouterTranscriptionClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None):
        self._api_key = settings.openrouter_api_key.get_secret_value() if settings.openrouter_api_key else None
        self.transcription_model = settings.openrouter_transcription_model
        self._transcription_api_url = settings.openrouter_transcription_api_url
        self._transcription_language = (
            settings.openrouter_transcription_language.strip()
            if settings.openrouter_transcription_language and settings.openrouter_transcription_language.strip()
            else None
        )
        self.max_audio_bytes = settings.openrouter_max_audio_bytes
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.openrouter_timeout_seconds, connect=10),
        )
        self._headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        if settings.openrouter_site_url:
            self._headers["HTTP-Referer"] = settings.openrouter_site_url
        if settings.openrouter_app_name:
            self._headers["X-OpenRouter-Title"] = settings.openrouter_app_name

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def transcribe(self, audio: bytes, audio_format: str) -> str:
        if not self._api_key:
            raise ChatNotConfigured("OPENROUTER_API_KEY is not configured")
        payload = {
            "model": self.transcription_model,
            "input_audio": {
                "data": base64.b64encode(audio).decode("ascii"),
                "format": audio_format,
            },
            "response_format": "json",
        }
        if self._transcription_language:
            payload["language"] = self._transcription_language
        try:
            response = await self._http.post(
                self._transcription_api_url,
                headers={**self._headers, "Accept": "application/json", "Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
        except httpx.HTTPError as error:
            raise ChatProviderError("OpenRouter transcription request failed") from error
        await self._ensure_success(response, "transcription")
        try:
            result = response.json()
        except ValueError as error:
            raise ChatProviderError("OpenRouter returned invalid transcription JSON") from error
        text = result.get("text") if isinstance(result, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ChatProviderError("OpenRouter returned an empty transcription")
        return text.strip()

    @staticmethod
    async def _ensure_success(response: httpx.Response, operation: str) -> None:
        if not response.is_error:
            return
        try:
            await response.aread()
        finally:
            await response.aclose()
        logger.warning("OpenRouter rejected %s request status=%s", operation, response.status_code)
        raise ChatProviderError(f"OpenRouter returned HTTP {response.status_code}")
