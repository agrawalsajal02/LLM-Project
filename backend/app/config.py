from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration, read from environment variables or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://chat:chat@localhost:5432/chat"

    # "gemini" calls the real API; "fake" streams a canned reply (no key needed, used in tests).
    llm_provider: Literal["gemini", "fake"] = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    system_prompt: str = "You are a helpful, concise assistant. Format answers in Markdown."
    # How many previous messages are sent to the model as context.
    history_max_messages: int = 20

    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
