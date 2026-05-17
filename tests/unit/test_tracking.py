from __future__ import annotations

from trident.portfolio.tracking import child_leg_client_id, managed_symbols_to_drop


def test_child_leg_client_id_is_deterministic_and_unique() -> None:
    a = child_leg_client_id("trident-sig-1", "broker-tp-9")
    b = child_leg_client_id("trident-sig-1", "broker-sl-9")
    assert a == "trident-sig-1::leg::broker-tp-9"
    assert a != b  # distinct legs of the same parent never collide
    # Deterministic — the same inputs always yield the same id.
    assert child_leg_client_id("trident-sig-1", "broker-tp-9") == a


def test_managed_symbols_to_drop_finds_closed_positions() -> None:
    # AAPL is still held; TSLA and F are gone -> they should be dropped.
    dropped = managed_symbols_to_drop(["AAPL", "TSLA", "F"], ["AAPL", "MSFT"])
    assert dropped == ["F", "TSLA"]  # sorted


def test_managed_symbols_to_drop_empty_when_all_held() -> None:
    assert managed_symbols_to_drop(["AAPL", "MSFT"], ["AAPL", "MSFT", "NVDA"]) == []


def test_managed_symbols_to_drop_handles_empty_inputs() -> None:
    assert managed_symbols_to_drop([], ["AAPL"]) == []
    assert managed_symbols_to_drop(["AAPL"], []) == ["AAPL"]
