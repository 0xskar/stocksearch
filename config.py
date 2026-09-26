"""Loads .env and exposes a single Settings object used across the app."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    ollama_host: str
    ollama_model: str
    ollama_keep_alive: str
    ollama_timeout_seconds: float
    finnhub_api_key: str | None
    reddit_client_id: str | None
    reddit_client_secret: str | None
    reddit_user_agent: str
    reddit_subreddits: list[str] = field(default_factory=list)
    db_path: str = "./data/stocksearch.db"
    ticker_pause_seconds: float = 2.0
    log_path: str = "./logs/stocksearch.log"
    log_level: str = "INFO"

    @property
    def reddit_configured(self) -> bool:
        return bool(self.reddit_client_id and self.reddit_client_secret)

    @property
    def finnhub_configured(self) -> bool:
        return bool(self.finnhub_api_key)


def load_settings() -> Settings:
    return Settings(
        ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct"),
        ollama_keep_alive=os.environ.get("OLLAMA_KEEP_ALIVE", "10m"),
        ollama_timeout_seconds=float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "120")),
        finnhub_api_key=os.environ.get("FINNHUB_API_KEY") or None,
        reddit_client_id=os.environ.get("REDDIT_CLIENT_ID") or None,
        reddit_client_secret=os.environ.get("REDDIT_CLIENT_SECRET") or None,
        reddit_user_agent=os.environ.get("REDDIT_USER_AGENT", "stocksearch/0.1"),
        reddit_subreddits=_split_csv(
            os.environ.get("REDDIT_SUBREDDITS", "wallstreetbets,stocks,investing")
        ),
        db_path=os.environ.get("STOCKSEARCH_DB_PATH", "./data/stocksearch.db"),
        ticker_pause_seconds=float(os.environ.get("TICKER_PAUSE_SECONDS", "2")),
        log_path=os.environ.get("STOCKSEARCH_LOG_PATH", "./logs/stocksearch.log"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
