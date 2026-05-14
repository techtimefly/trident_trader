from __future__ import annotations

from datetime import UTC, datetime, time
from decimal import Decimal

from trident.clock import ET
from trident.data.bars import Bar, BarStore
from trident.strategies.orb import OpeningRangeBreakout


def et_dt(hour: int, minute: int, day: tuple[int, int, int] = (2026, 5, 14)) -> datetime:
    return datetime(*day, hour, minute, tzinfo=ET).astimezone(UTC)


def or_bar(
    symbol: str,
    minute_after_open: int,
    high: str,
    low: str,
    close: str,
    volume: int = 50_000,
) -> Bar:
    ts = et_dt(9, 30 + minute_after_open)
    o = Decimal(close)
    return Bar(
        symbol=symbol,
        ts=ts,
        timeframe="1min",
        open=o,
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
    )


def post_or_bar(
    symbol: str,
    minute_et: time,
    close: str,
    volume: int = 100_000,
    high: str | None = None,
    low: str | None = None,
) -> Bar:
    ts = et_dt(minute_et.hour, minute_et.minute)
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


def test_or_window_accumulates_without_signal() -> None:
    strat = OpeningRangeBreakout(symbols=["AAPL"])
    store = BarStore()
    bars = [
        or_bar("AAPL", 0, "100.5", "99.5", "100.0"),
        or_bar("AAPL", 1, "100.7", "99.8", "100.4"),
        or_bar("AAPL", 2, "100.6", "99.9", "100.2"),
        or_bar("AAPL", 3, "100.9", "100.0", "100.5"),
        or_bar("AAPL", 4, "101.0", "100.1", "100.8"),
    ]
    for b in bars:
        assert strat.on_bar(b, store) is None


def test_breakout_signal_after_or() -> None:
    strat = OpeningRangeBreakout(symbols=["AAPL"])
    store = BarStore()
    for i in range(5):
        strat.on_bar(or_bar("AAPL", i, "101.0", "99.0", "100.0", volume=10_000), store)

    # Next bar (9:35 ET) closes above OR high with strong volume.
    breakout = post_or_bar("AAPL", time(9, 35), close="101.50", volume=50_000)
    signal = strat.on_bar(breakout, store)
    assert signal is not None
    assert signal.side == "long"
    assert signal.entry_price == Decimal("101.50")
    assert signal.stop_price == Decimal("99.0")
    # Target is 1R above entry: entry + (entry - stop) = 101.50 + 2.50 = 104.00
    assert signal.target_price == Decimal("104.00")
    assert signal.reward_to_risk == Decimal("1")


def test_at_most_one_entry_per_day() -> None:
    strat = OpeningRangeBreakout(symbols=["AAPL"])
    store = BarStore()
    for i in range(5):
        strat.on_bar(or_bar("AAPL", i, "101.0", "99.0", "100.0", volume=10_000), store)
    first = strat.on_bar(post_or_bar("AAPL", time(9, 35), "101.5", volume=50_000), store)
    second = strat.on_bar(post_or_bar("AAPL", time(9, 36), "102.0", volume=50_000), store)
    assert first is not None
    assert second is None


def test_no_signal_if_volume_too_low() -> None:
    strat = OpeningRangeBreakout(symbols=["AAPL"])
    store = BarStore()
    for i in range(5):
        strat.on_bar(or_bar("AAPL", i, "101.0", "99.0", "100.0", volume=10_000), store)
    # avg OR volume = 10_000; need >= 15_000 to trigger. 12_000 fails.
    bar = post_or_bar("AAPL", time(9, 35), "101.5", volume=12_000)
    assert strat.on_bar(bar, store) is None


def test_no_signal_after_last_entry_time() -> None:
    strat = OpeningRangeBreakout(symbols=["AAPL"])
    store = BarStore()
    for i in range(5):
        strat.on_bar(or_bar("AAPL", i, "101.0", "99.0", "100.0", volume=10_000), store)
    bar = post_or_bar("AAPL", time(11, 0), "101.5", volume=50_000)
    assert strat.on_bar(bar, store) is None


def test_no_signal_if_or_incomplete() -> None:
    """If we miss any OR bar, skip the symbol for the day."""
    strat = OpeningRangeBreakout(symbols=["AAPL"])
    store = BarStore()
    # Only deliver 4 of 5 OR bars
    for i in [0, 1, 2, 3]:
        strat.on_bar(or_bar("AAPL", i, "101.0", "99.0", "100.0", volume=10_000), store)
    # First post-OR bar — should mark skipped and never trade.
    bar1 = post_or_bar("AAPL", time(9, 35), "101.5", volume=50_000)
    bar2 = post_or_bar("AAPL", time(9, 40), "102.5", volume=50_000)
    assert strat.on_bar(bar1, store) is None
    assert strat.on_bar(bar2, store) is None


def test_ignores_symbols_not_in_watchlist() -> None:
    strat = OpeningRangeBreakout(symbols=["AAPL"])
    store = BarStore()
    bar = or_bar("TSLA", 0, "100.5", "99.5", "100.0")
    assert strat.on_bar(bar, store) is None


def test_resets_for_new_day() -> None:
    strat = OpeningRangeBreakout(symbols=["AAPL"])
    store = BarStore()
    for i in range(5):
        strat.on_bar(or_bar("AAPL", i, "101.0", "99.0", "100.0", volume=10_000), store)
    strat.on_bar(post_or_bar("AAPL", time(9, 35), "101.5", volume=50_000), store)

    # Bars on the next trading day should be able to trigger a fresh signal.
    next_day = (2026, 5, 15)
    def od(m: int) -> Bar:
        ts = datetime(*next_day, 9, 30 + m, tzinfo=ET).astimezone(UTC)
        return Bar("AAPL", ts, "1min", Decimal("100"), Decimal("101.0"), Decimal("99.0"), Decimal("100"), 10_000)
    for i in range(5):
        strat.on_bar(od(i), store)
    next_ts = datetime(*next_day, 9, 35, tzinfo=ET).astimezone(UTC)
    next_bar = Bar("AAPL", next_ts, "1min", Decimal("101.5"), Decimal("101.5"), Decimal("101.5"), Decimal("101.5"), 50_000)
    assert strat.on_bar(next_bar, store) is not None


def test_dropping_old_day_state_with_reset() -> None:
    strat = OpeningRangeBreakout(symbols=["AAPL"])
    store = BarStore()
    for i in range(5):
        strat.on_bar(or_bar("AAPL", i, "101.0", "99.0", "100.0"), store)
    strat.reset_for_day(datetime(2026, 5, 15, tzinfo=ET).date())
    assert (("AAPL", datetime(2026, 5, 14).date()) not in strat._state)  # noqa: SLF001
