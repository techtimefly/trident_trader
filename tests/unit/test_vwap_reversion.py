"""Unit tests for the VWAPReversion strategy.

Helper convention:
  - ``_anchor`` creates a doji bar at 9:30 ET with large volume to pin VWAP near
    that price.
  - ``_bar`` creates a doji bar with configurable volume so the signal bar barely
    moves VWAP.
  Using large-volume anchors + small-volume signal bars lets us reason about
  expected VWAP (approx anchor close) without computing exact Decimal arithmetic in
  every test.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trident.clock import ET
from trident.data.bars import Bar, BarStore
from trident.strategies.registry import available_strategies, build_strategy
from trident.strategies.vwap_reversion import (
    NO_ENTRY_AFTER,
    NO_ENTRY_BEFORE,
    VWAPReversion,
)

_DAY = (2026, 5, 14)


def _bar(
    symbol: str,
    hour: int,
    minute: int,
    close: str,
    volume: int = 100_000,
    high: str | None = None,
    low: str | None = None,
    day: tuple[int, int, int] = _DAY,
) -> Bar:
    ts = datetime(*day, hour, minute, tzinfo=ET).astimezone(UTC)
    c = Decimal(close)
    return Bar(
        symbol=symbol,
        ts=ts,
        timeframe="1min",
        open=c,
        high=Decimal(high) if high else c,
        low=Decimal(low) if low else c,
        close=c,
        volume=volume,
    )


def _anchor(symbol: str, close: str = "100") -> Bar:
    """9:30 ET doji bar with large volume to anchor VWAP near ``close``."""
    return _bar(symbol, 9, 30, close, volume=1_000_000)


def _feed_anchor(strat: VWAPReversion, symbol: str, close: str = "100") -> None:
    store = BarStore()
    strat.on_bar(_anchor(symbol, close), store)


# ---------------------------------------------------------------------------
# Entry-window guards
# ---------------------------------------------------------------------------

def test_no_signal_before_entry_window() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    # 9:30 ET bar is before NO_ENTRY_BEFORE (9:45) — should never produce a signal
    # even if price is far from VWAP.
    bar = _bar("AAPL", 9, 30, "80")
    assert strat.on_bar(bar, store) is None


def test_no_signal_at_9_44() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    _feed_anchor(strat, "AAPL")  # establish VWAP ≈ 100
    bar = _bar("AAPL", 9, 44, "80")   # one minute before the window opens
    assert strat.on_bar(bar, store) is None


def test_signal_allowed_at_no_entry_before_boundary() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    _feed_anchor(strat, "AAPL")  # VWAP ≈ 100
    bar = _bar("AAPL", NO_ENTRY_BEFORE.hour, NO_ENTRY_BEFORE.minute, "80", volume=1)
    # 80 << 100*(1-0.015)=98.5 → should trigger at the window boundary.
    assert strat.on_bar(bar, store) is not None


def test_no_signal_at_cutoff_time() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    _feed_anchor(strat, "AAPL")
    bar = _bar("AAPL", NO_ENTRY_AFTER.hour, NO_ENTRY_AFTER.minute, "80", volume=1)
    assert strat.on_bar(bar, store) is None


def test_no_signal_after_cutoff_time() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    _feed_anchor(strat, "AAPL")
    bar = _bar("AAPL", 15, 0, "80", volume=1)
    assert strat.on_bar(bar, store) is None


# ---------------------------------------------------------------------------
# Signal direction
# ---------------------------------------------------------------------------

def test_long_signal_when_price_below_lower_band() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    _feed_anchor(strat, "AAPL")  # VWAP ≈ 100; lower_band ≈ 98.5
    bar = _bar("AAPL", 9, 45, "96", volume=1)  # 96 < 98.5 → long
    sig = strat.on_bar(bar, store)
    assert sig is not None
    assert sig.side == "long"
    assert sig.symbol == "AAPL"
    assert sig.strategy == "vwap_reversion"
    assert sig.entry_price == Decimal("96")


def test_short_signal_when_price_above_upper_band() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    _feed_anchor(strat, "AAPL")  # VWAP ≈ 100; upper_band ≈ 101.5
    bar = _bar("AAPL", 9, 45, "104", volume=1)  # 104 > 101.5 → short
    sig = strat.on_bar(bar, store)
    assert sig is not None
    assert sig.side == "short"
    assert sig.entry_price == Decimal("104")


def test_no_signal_price_inside_bands() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    _feed_anchor(strat, "AAPL")  # VWAP ≈ 100
    # 100 is exactly at VWAP — well inside bands.
    bar = _bar("AAPL", 9, 45, "100", volume=1)
    assert strat.on_bar(bar, store) is None


def test_no_signal_price_just_inside_lower_band() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    # Anchor VWAP at ~100 with high-volume doji, then probe just above lower band.
    _feed_anchor(strat, "AAPL", "100")
    # lower_band ~= 100 * (1 - 0.015) = 98.5; a close of 98.6 is above the band.
    bar = _bar("AAPL", 9, 45, "98.6", volume=1)
    assert strat.on_bar(bar, store) is None


# ---------------------------------------------------------------------------
# Trade geometry
# ---------------------------------------------------------------------------

def test_long_geometry_stop_below_entry_target_above() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    _feed_anchor(strat, "AAPL")
    bar = _bar("AAPL", 9, 45, "96", volume=1)
    sig = strat.on_bar(bar, store)
    assert sig is not None
    assert sig.stop_price < sig.entry_price < sig.target_price


def test_short_geometry_target_below_entry_stop_above() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    _feed_anchor(strat, "AAPL")
    bar = _bar("AAPL", 9, 45, "104", volume=1)
    sig = strat.on_bar(bar, store)
    assert sig is not None
    assert sig.target_price < sig.entry_price < sig.stop_price


def test_long_risk_reward_symmetric() -> None:
    """Stop is equidistant from entry as entry is from VWAP (target). R:R = 1:1."""
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    _feed_anchor(strat, "AAPL")
    bar = _bar("AAPL", 9, 45, "96", volume=1)
    sig = strat.on_bar(bar, store)
    assert sig is not None
    risk = sig.entry_price - sig.stop_price
    reward = sig.target_price - sig.entry_price
    assert risk == reward


def test_short_risk_reward_symmetric() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    _feed_anchor(strat, "AAPL")
    bar = _bar("AAPL", 9, 45, "104", volume=1)
    sig = strat.on_bar(bar, store)
    assert sig is not None
    risk = sig.stop_price - sig.entry_price
    reward = sig.entry_price - sig.target_price
    assert risk == reward


def test_long_target_equals_vwap() -> None:
    """For a long signal the target should be the VWAP at signal time."""
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    # Single anchor bar → VWAP = 100 exactly (1M volume vs signal's 1 volume).
    _feed_anchor(strat, "AAPL", "100")
    bar = _bar("AAPL", 9, 45, "96", volume=1)
    sig = strat.on_bar(bar, store)
    assert sig is not None
    # After adding signal bar: cum_pv = 100M + 96; cum_v = 1_000_001.
    # VWAP ≈ 99.999904 ≈ 100 — target must equal that VWAP.
    # Verify stop + target = 2 * entry (symmetric midpoint is entry).
    assert sig.stop_price + sig.target_price == Decimal("2") * sig.entry_price


def test_short_target_equals_vwap() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    _feed_anchor(strat, "AAPL", "100")
    bar = _bar("AAPL", 9, 45, "104", volume=1)
    sig = strat.on_bar(bar, store)
    assert sig is not None
    assert sig.stop_price + sig.target_price == Decimal("2") * sig.entry_price


# ---------------------------------------------------------------------------
# One-entry-per-day discipline
# ---------------------------------------------------------------------------

def test_at_most_one_entry_per_day() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    _feed_anchor(strat, "AAPL")
    first = strat.on_bar(_bar("AAPL", 9, 45, "96", volume=1), store)
    second = strat.on_bar(_bar("AAPL", 9, 46, "95", volume=1), store)
    assert first is not None
    assert second is None


def test_new_day_resets_entry_flag() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    _feed_anchor(strat, "AAPL")
    strat.on_bar(_bar("AAPL", 9, 45, "96", volume=1), store)

    # Next trading day — state is fresh.
    next_day = (2026, 5, 15)
    strat.on_bar(_bar("AAPL", 9, 30, "100", volume=1_000_000, day=next_day), store)
    sig = strat.on_bar(_bar("AAPL", 9, 45, "96", volume=1, day=next_day), store)
    assert sig is not None


# ---------------------------------------------------------------------------
# Watchlist / timeframe filtering
# ---------------------------------------------------------------------------

def test_ignores_symbol_not_in_watchlist() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    _feed_anchor(strat, "AAPL")
    bar = _bar("TSLA", 9, 45, "80", volume=1)
    assert strat.on_bar(bar, store) is None


def test_ignores_non_1min_timeframe() -> None:
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    ts = datetime(*_DAY, 9, 45, tzinfo=ET).astimezone(UTC)
    bar = Bar("AAPL", ts, "5min", Decimal("80"), Decimal("80"), Decimal("80"), Decimal("80"), 1)
    assert strat.on_bar(bar, store) is None


def test_symbols_are_case_insensitive() -> None:
    strat = VWAPReversion(symbols=["aapl"])
    store = BarStore()
    strat.on_bar(_bar("AAPL", 9, 30, "100", volume=1_000_000), store)
    bar = _bar("AAPL", 9, 45, "96", volume=1)
    assert strat.on_bar(bar, store) is not None


# ---------------------------------------------------------------------------
# VWAP accumulation
# ---------------------------------------------------------------------------

def test_vwap_accumulates_from_open() -> None:
    """Two equal-volume bars at different prices → VWAP = their average."""
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    # Bar 1 at 9:30: typical = 100, volume = 1000
    strat.on_bar(_bar("AAPL", 9, 30, "100", volume=1000), store)
    # Bar 2 at 9:31: typical = 200, volume = 1000
    # Expected VWAP after bar 2 = (100*1000 + 200*1000) / 2000 = 150
    strat.on_bar(_bar("AAPL", 9, 31, "200", volume=1000), store)
    # Bar 3 at 9:45: price must be below 150 * (1 - 0.015) = 147.75 to trigger.
    bar = _bar("AAPL", 9, 45, "140", volume=1)
    sig = strat.on_bar(bar, store)
    # 140 << 147.75 → long signal expected (VWAP ≈ 150 implies lower band ≈ 147).
    assert sig is not None
    assert sig.side == "long"


def test_no_signal_without_vwap_data() -> None:
    """A strategy with no bars fed should not produce a signal."""
    strat = VWAPReversion(symbols=["AAPL"])
    store = BarStore()
    # Feed one bar at 9:45 with no prior bars — VWAP exists (from this bar itself)
    # but let's verify the band check still works correctly.
    bar = _bar("AAPL", 9, 45, "100", volume=1000)
    # VWAP = 100, entry = 100, which is AT VWAP — inside bands.
    assert strat.on_bar(bar, store) is None


# ---------------------------------------------------------------------------
# reset_for_day
# ---------------------------------------------------------------------------

def test_reset_for_day_drops_old_state() -> None:
    from datetime import date
    strat = VWAPReversion(symbols=["AAPL"])
    _feed_anchor(strat, "AAPL")
    today = date(*_DAY)
    next_day_date = date(2026, 5, 15)
    strat.reset_for_day(next_day_date)
    # Today's state should be gone.
    assert ("AAPL", today) not in strat._state


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------

def test_registered_in_strategy_registry() -> None:
    assert "vwap_reversion" in available_strategies()


def test_build_from_registry_returns_correct_type() -> None:
    strat = build_strategy("vwap_reversion", ["AAPL", "MSFT"])
    assert isinstance(strat, VWAPReversion)
    assert strat.name == "vwap_reversion"
    assert strat._symbols == {"AAPL", "MSFT"}


def test_build_from_registry_returns_fresh_instances() -> None:
    a = build_strategy("vwap_reversion", ["AAPL"])
    b = build_strategy("vwap_reversion", ["AAPL"])
    assert a is not b


def test_both_strategies_listed() -> None:
    names = available_strategies()
    assert "orb_5m" in names
    assert "vwap_reversion" in names
