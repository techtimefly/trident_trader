from __future__ import annotations

from pathlib import Path

import pytest

from trident.settings import Settings


def test_env_vars_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exported environment variables populate Settings with no .env file present."""
    monkeypatch.setenv("ALPACA_API_KEY", "env-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "env-secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    settings = Settings(_env_file=None)  # ignore any real .env on disk

    assert settings.alpaca_api_key == "env-key"
    assert settings.alpaca_api_secret == "env-secret"
    assert settings.alpaca_base_url == "https://paper-api.alpaca.markets"


def test_env_var_overrides_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An exported env var wins over the same key defined in a .env file."""
    env_file = tmp_path / ".env"
    env_file.write_text("ALPACA_API_KEY=file-key\n")
    monkeypatch.setenv("ALPACA_API_KEY", "env-key")

    settings = Settings(_env_file=env_file)

    assert settings.alpaca_api_key == "env-key"


def test_dotenv_used_when_env_var_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no exported env var, the .env file value is used."""
    env_file = tmp_path / ".env"
    env_file.write_text("ALPACA_API_KEY=file-key\n")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)

    settings = Settings(_env_file=env_file)

    assert settings.alpaca_api_key == "file-key"
