from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_feed: str = "iex"

    database_url: str = "postgresql+psycopg2://trident:trident@localhost:5432/trident"

    risk_per_trade_pct: Decimal = Field(default=Decimal("1.0"))
    daily_loss_limit_pct: Decimal = Field(default=Decimal("2.0"))
    max_concurrent_positions: int = 3
    account_equity_override: Decimal | None = None

    log_level: str = "INFO"
    log_dir: Path = Path("./logs")
    environment: str = "development"

    @property
    def is_paper(self) -> bool:
        return "paper" in self.alpaca_base_url.lower()


@lru_cache
def get_settings() -> Settings:
    return Settings()
