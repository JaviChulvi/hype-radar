from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(default="postgresql+psycopg://hype_radar:hype_radar@127.0.0.1:5432/hype_radar")
    database_echo: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
