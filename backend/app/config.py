from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(default="postgresql+psycopg://hype_radar:hype_radar@127.0.0.1:5432/hype_radar")
    database_echo: bool = False
    hyperliquid_network: Literal["mainnet", "testnet"] = "mainnet"
    openrouter_api_key: SecretStr | None = None
    openrouter_model: str = "openrouter/auto"
    openrouter_api_url: str = "https://openrouter.ai/api/v1/chat/completions"
    openrouter_transcription_model: str = "openai/whisper-1"
    openrouter_transcription_api_url: str = "https://openrouter.ai/api/v1/audio/transcriptions"
    openrouter_transcription_language: str | None = None
    openrouter_site_url: str | None = "http://127.0.0.1:5173"
    openrouter_app_name: str = "Hype Radar"
    openrouter_timeout_seconds: float = Field(default=60, gt=0, le=300)
    openrouter_max_completion_tokens: int = Field(default=800, ge=1, le=16_384)
    openrouter_max_audio_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)


@lru_cache
def get_settings() -> Settings:
    return Settings()
