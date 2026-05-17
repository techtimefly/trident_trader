from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trident.backtest.simulator import SimulatedTrade
from trident.backtest.stats import summarize
from trident.strategies.base import Signal

# entry 100 / stop 90 → 10 of risk per share; with qty=1, r_multiple == gross_pnl / 10.
_SIGNAL = Signal(
    ts=datetime(2026, 5, 13, 13, 35, tzinfo=UTC),
    strategy="orb_5m",
    symbol="AAPL",
    side="long",
    entry_price=Decimal("100"),
    stop_price=Decimal("90"),
    target_price=Decimal("110"),
)


def _trade(*, net: str, gross: str, fees: str, exit_reason: str = "target") -> SimulatedTrade:
    fee_each = Decimal(fees) / 2
    return SimulatedTrade(
        signal=_SIGNAL,
        qty=1,
        entry_price=Decimal("100"),
        exit_reason=exit_reason,
        exit_price=Decimal("110"),
        exit_ts_iso="2026-05-13T13:40:00+00:00",
        pnl=Decimal(net),
        gross_pnl=Decimal(gross),
        entry_fee=fee_each,
        exit_fee=fee_each,
        ideal_entry_price=Decimal("100"),
    )


def test_summarize_empty() -> None:
    s = summarize([])
    assert s.num_trades == 0
    assert s.wins == 0
    assert s.losses == 0
    assert s.gross_pnl == Decimal("0")
    assert s.total_pnl == Decimal("0")
    assert s.total_fees == Decimal("0")
    assert s.win_rate == Decimal("0")
    assert s.avg_r == Decimal("0")
    assert s.by_exit == {}


def test_summarize_all_wins() -> None:
    s = summarize(
        [_trade(net="9", gross="10", fees="1"), _trade(net="19", gross="20", fees="1")]
    )
    assert s.num_trades == 2
    assert s.wins == 2
    assert s.losses == 0
    assert s.win_rate == Decimal("100")
    assert s.avg_r == Decimal("1.5")  # (1 + 2) / 2


def test_summarize_mixed_counts_and_totals() -> None:
    s = summarize(
        [
            _trade(net="9", gross="10", fees="1", exit_reason="target"),
            _trade(net="19", gross="20", fees="1", exit_reason="target"),
            _trade(net="-11", gross="-10", fees="1", exit_reason="stop"),
        ]
    )
    assert s.num_trades == 3
    assert s.wins == 2
    assert s.losses == 1
    assert s.by_exit == {"target": 2, "stop": 1}
    assert s.gross_pnl == Decimal("20")
    assert s.total_pnl == Decimal("17")  # gross 20 - fees 3
    assert s.total_fees == Decimal("3")


def test_summarize_avg_r_and_win_rate() -> None:
    s = summarize(
        [
            _trade(net="9", gross="10", fees="1"),
            _trade(net="19", gross="20", fees="1"),
            _trade(net="-11", gross="-10", fees="1"),
        ]
    )
    # r_multiples are 1, 2, -1 → mean = 2/3
    assert s.avg_r == Decimal("2") / Decimal("3")
    assert s.win_rate == Decimal("2") / Decimal("3") * Decimal("100")
