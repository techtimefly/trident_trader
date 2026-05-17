from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trident.backtest.costs import CostModel
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


# --- cost model: slippage + fees -------------------------------------------------


def test_zero_cost_matches_idealistic() -> None:
    # The default costs arg is ZERO_COST; passing CostModel() explicitly is identical.
    bars = [bar(1, "102.1", "101.5", "102.0")]
    sig = make_signal()
    default = simulate_trade(sig, qty=10, subsequent_bars=bars)
    explicit = simulate_trade(sig, qty=10, subsequent_bars=bars, costs=CostModel())
    assert default is not None
    assert explicit is not None
    assert default.pnl == explicit.pnl == Decimal("20")
    assert default.entry_price == explicit.entry_price == Decimal("100")
    assert default.gross_pnl == default.pnl  # no fees → gross == net


def test_long_entry_slips_up() -> None:
    # 100 bps: a long entry fills above the intended 100.
    costs = CostModel(slippage_bps=Decimal("100"))
    bars = [bar(1, "102.1", "101.5", "102.0")]
    trade = simulate_trade(make_signal(), qty=10, subsequent_bars=bars, costs=costs)
    assert trade is not None
    assert trade.ideal_entry_price == Decimal("100")
    assert trade.entry_price == Decimal("101.00")


def test_target_exit_does_not_slip() -> None:
    # A target is a resting limit order — it fills at exactly the target.
    costs = CostModel(slippage_bps=Decimal("100"))
    bars = [bar(1, "102.1", "101.5", "102.0")]
    trade = simulate_trade(make_signal(), qty=10, subsequent_bars=bars, costs=costs)
    assert trade is not None
    assert trade.exit_reason == "target"
    assert trade.exit_price == Decimal("102")


def test_stop_exit_slips_past_stop() -> None:
    # A stop is a market order — the long exit fills below the 99 stop.
    costs = CostModel(slippage_bps=Decimal("100"))
    bars = [bar(1, "100.5", "98.5", "99.5")]
    trade = simulate_trade(make_signal(), qty=10, subsequent_bars=bars, costs=costs)
    assert trade is not None
    assert trade.exit_reason == "stop"
    assert trade.exit_price == Decimal("98.01")  # 99 - 1%


def test_eod_exit_slips() -> None:
    # EOD flatten is a market order — the long exit slips below the last close.
    costs = CostModel(slippage_bps=Decimal("100"))
    bars = [bar(1, "100.5", "99.8", "100.2"), bar(2, "100.8", "100.1", "100.5")]
    trade = simulate_trade(make_signal(), qty=10, subsequent_bars=bars, costs=costs)
    assert trade is not None
    assert trade.exit_reason == "eod"
    assert trade.exit_price == Decimal("99.50")  # 100.5 - 1%, rounded


def test_fees_reduce_net_pnl() -> None:
    # Flat per-share commission on both legs, no slippage.
    costs = CostModel(fee_per_share=Decimal("0.01"))
    bars = [bar(1, "102.1", "101.5", "102.0")]
    trade = simulate_trade(make_signal(), qty=10, subsequent_bars=bars, costs=costs)
    assert trade is not None
    assert trade.gross_pnl == Decimal("20")  # unchanged: no slippage
    assert trade.entry_fee == Decimal("0.10")  # 0.01 * 10
    assert trade.exit_fee == Decimal("0.10")
    assert trade.pnl == trade.gross_pnl - trade.entry_fee - trade.exit_fee
    assert trade.pnl == Decimal("19.80")


def test_r_multiple_excludes_fees() -> None:
    # R is gross P&L over planned risk — fees do not move it.
    costs = CostModel(fee_per_share=Decimal("0.05"))
    bars = [bar(1, "102.1", "101.5", "102.0")]
    trade = simulate_trade(make_signal(), qty=10, subsequent_bars=bars, costs=costs)
    assert trade is not None
    assert trade.r_multiple == Decimal("2")  # gross 20 over qty*risk 10
    assert trade.pnl < trade.gross_pnl  # fees ate into net
