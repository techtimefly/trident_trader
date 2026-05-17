from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# settings.py lives at src/trident/settings.py, so the project root — where
# .env and .env.example sit — is two directories up. Anchoring the .env lookup
# here means config loads correctly no matter which directory a script runs from.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Precedence, highest first: real environment variables, then the .env file,
    # then the field defaults below. Exported env vars therefore override .env,
    # and .env is optional — when it is absent, env vars + defaults are used.
    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_feed: str = "iex"

    # Anthropic API key for the AI stock-suggestions feature (trident.suggest).
    # Optional — when blank, the suggest client degrades gracefully and returns
    # an empty result rather than calling the network or crashing.
    anthropic_api_key: str = ""

    database_url: str = "postgresql+psycopg2://trident:trident@localhost:5432/trident"

    risk_per_trade_pct: Decimal = Field(default=Decimal("1.0"))
    daily_loss_limit_pct: Decimal = Field(default=Decimal("2.0"))
    max_concurrent_positions: int = 3
    account_equity_override: Decimal | None = None

    # Honest-backtest cost model (scripts/backtest.py). Synthetic slippage in
    # basis points (IEX gives no bid/ask); fees default to realistic small values.
    backtest_slippage_bps: Decimal = Field(default=Decimal("2.0"))
    backtest_fee_per_share: Decimal = Field(default=Decimal("0"))
    backtest_min_fee: Decimal = Field(default=Decimal("0"))
    backtest_sec_fee_rate: Decimal = Field(default=Decimal("0.0000278"))
    backtest_taf_per_share: Decimal = Field(default=Decimal("0.000166"))

    log_level: str = "INFO"
    log_dir: Path = Path("./logs")
    environment: str = "development"

    @field_validator("account_equity_override", mode="before")
    @classmethod
    def _blank_or_comment_to_none(cls, v: Any) -> Any:
        """Tolerate an empty or stray-comment value in .env (a common foot-gun)."""
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s or s.startswith("#"):
                return None
        return v

    @field_validator(
        "alpaca_data_feed",
        "log_level",
        "environment",
        "backtest_slippage_bps",
        "backtest_fee_per_share",
        "backtest_min_fee",
        "backtest_sec_fee_rate",
        "backtest_taf_per_share",
        mode="before",
    )
    @classmethod
    def _strip_trailing_comment(cls, v: Any) -> Any:
        """Strip a trailing `# ...` comment so .env inline comments don't poison values."""
        if isinstance(v, str) and "#" in v:
            head, _, _ = v.partition("#")
            return head.strip()
        return v

    @property
    def is_paper(self) -> bool:
        return "paper" in self.alpaca_base_url.lower()


@lru_cache
def get_settings() -> Settings:
    return Settings()
