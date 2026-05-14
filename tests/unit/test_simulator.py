from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trident.backtest.simulator import simulate_trade
from trident.data.bars import Bar
from trident.strategies.base import Signal


def make_signal(
    side: str = "long",
    entry: str = "100",
    stop: str = "99",
    target: str = "102",
) -> Signal:
    return Signal(
        ts=datetime(2026, 5, 13, 13, 35, tzinfo=UTC),
        strategy="orb_5m",
        symbol="AAPL",
        side=side,
        entry_price=Decimal(entry),
        stop_price=Decimal(stop),
        target_price=Decimal(target),
    )


def bar(
    minute_after: int,
    high: str,
    low: str,
    close: str,
    symbol: str = "AAPL",
) -> Bar:
    ts = datetime(2026, 5, 13, 13, 35, tzinfo=UTC) + timedelta(minutes=minute_after)
    return Bar(
        symbol=symbol,
        ts=ts,
        timeframe="1min",
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=10_000,
    )


def test_returns_none_when_no_followups() -> None:
    assert simulate_trade(make_signal(), qty=10, subsequent_bars=[]) is None


def test_long_target_hit() -> None:
    bars = [
        bar(1, "101.0", "100.5", "100.8"),
        bar(2, "102.1", "101.5", "102.0"),  # high >= 102
    ]
    trade = simulate_trade(make_signal(), qty=10, subsequent_bars=bars)
    assert trade is not None
    assert trade.exit_reason == "target"
    assert trade.exit_price == Decimal("102")
    assert trade.pnl == Decimal("20")  # (102 - 100) * 10
    assert trade.r_multiple == Decimal("2")


def test_long_stop_hit() -> None:
    bars = [
        bar(1, "100.5", "98.5", "99.5"),  # low <= 99
    ]
    trade = simulate_trade(make_signal(), qty=10, subsequent_bars=bars)
    assert trade is not None
    assert trade.exit_reason == "stop"
    assert trade.exit_price == Decimal("99")
    assert trade.pnl == Decimal("-10")
    assert trade.r_multiple == Decimal("-1")


def test_stop_wins_ties_with_target() -> None:
    # Single bar's range straddles both. Conservatively count it as a stop.
    bars = [bar(1, "102.5", "98.5", "101")]
    trade = simulate_trade(make_signal(), qty=10, subsequent_bars=bars)
    assert trade is not None
    assert trade.exit_reason == "stop"


def test_eod_close_when_neither_hits() -> None:
    bars = [
        bar(1, "100.5", "99.8", "100.2"),
        bar(2, "100.8", "100.1", "100.5"),
        bar(3, "101.0", "100.3", "100.7"),
    ]
    trade = simulate_trade(make_signal(), qty=10, subsequent_bars=bars)
    assert trade is not None
    assert trade.exit_reason == "eod"
    assert trade.exit_price == Decimal("100.7")
    assert trade.pnl == Decimal("7.0")


def test_filters_other_symbols() -> None:
    bars = [
        bar(1, "200", "150", "175", symbol="NVDA"),  # ignored
        bar(2, "100.6", "100.1", "100.4", symbol="AAPL"),
    ]
    trade = simulate_trade(make_signal(), qty=10, subsequent_bars=bars)
    assert trade is not None
    assert trade.exit_reason == "eod"
    assert trade.signal.symbol == "AAPL"


def test_short_target_hit() -> None:
    # entry=100, stop=102 → 2$ risk; target=98 → 2$ reward; so 1R win at target.
    sig = make_signal(side="short", entry="100", stop="102", target="98")
    bars = [
        bar(1, "100.5", "99.7", "100.0"),
        bar(2, "100.0", "97.9", "98.0"),  # low <= 98
    ]
    trade = simulate_trade(sig, qty=10, subsequent_bars=bars)
    assert trade is not None
    assert trade.exit_reason == "target"
    assert trade.exit_price == Decimal("98")
    assert trade.pnl == Decimal("20")
    assert trade.r_multiple == Decimal("1")


def test_short_stop_hit() -> None:
    sig = make_signal(side="short", entry="100", stop="102", target="98")
    bars = [bar(1, "102.5", "100", "101.5")]
    trade = simulate_trade(sig, qty=10, subsequent_bars=bars)
    assert trade is not None
    assert trade.exit_reason == "stop"
    assert trade.pnl == Decimal("-20")
    assert trade.r_multiple == Decimal("-1")


def test_short_eod_close() -> None:
    sig = make_signal(side="short", entry="100", stop="102", target="98")
    bars = [
        bar(1, "100.2", "99.5", "99.8"),
        bar(2, "99.7", "99.2", "99.4"),
    ]
    trade = simulate_trade(sig, qty=10, subsequent_bars=bars)
    assert trade is not None
    assert trade.exit_reason == "eod"
    assert trade.exit_price == Decimal("99.4")
    assert trade.pnl == Decimal("6.0")  # (100 - 99.4) * 10
