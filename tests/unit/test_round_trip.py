from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trident.accounting.round_trip import (
    ExitCandidate,
    compute_round_trip,
    is_wash_sale,
    pick_exit_order,
)

ENTRY = datetime(2026, 5, 14, 14, 0, tzinfo=UTC)
EXIT = datetime(2026, 5, 14, 15, 30, tzinfo=UTC)


def test_long_profit() -> None:
    rt = compute_round_trip(
        symbol="AAPL", side="long", qty=10,
        entry_ts=ENTRY, entry_price=Decimal("100"),
        exit_ts=EXIT, exit_price=Decimal("104"),
    )
    assert rt.gross_pnl == Decimal("40")
    assert rt.net_pnl == Decimal("40")
    assert rt.holding_period_seconds == 90 * 60


def test_long_loss() -> None:
    rt = compute_round_trip(
        symbol="AAPL", side="long", qty=10,
        entry_ts=ENTRY, entry_price=Decimal("100"),
        exit_ts=EXIT, exit_price=Decimal("97"),
    )
    assert rt.gross_pnl == Decimal("-30")


def test_short_profit() -> None:
    rt = compute_round_trip(
        symbol="AAPL", side="short", qty=10,
        entry_ts=ENTRY, entry_price=Decimal("100"),
        exit_ts=EXIT, exit_price=Decimal("96"),
    )
    assert rt.gross_pnl == Decimal("40")


def test_short_loss() -> None:
    rt = compute_round_trip(
        symbol="AAPL", side="short", qty=10,
        entry_ts=ENTRY, entry_price=Decimal("100"),
        exit_ts=EXIT, exit_price=Decimal("103"),
    )
    assert rt.gross_pnl == Decimal("-30")


def test_fees_reduce_net_pnl() -> None:
    rt = compute_round_trip(
        symbol="AAPL", side="long", qty=10,
        entry_ts=ENTRY, entry_price=Decimal("100"),
        exit_ts=EXIT, exit_price=Decimal("104"),
        fees=Decimal("2.50"),
    )
    assert rt.gross_pnl == Decimal("40")
    assert rt.net_pnl == Decimal("37.50")


def test_r_multiple_with_stop() -> None:
    # risk = |100 - 98| * 10 = 20; net = 40 -> R = 2.0
    rt = compute_round_trip(
        symbol="AAPL", side="long", qty=10,
        entry_ts=ENTRY, entry_price=Decimal("100"),
        exit_ts=EXIT, exit_price=Decimal("104"),
        stop_price=Decimal("98"),
    )
    assert rt.r_multiple == Decimal("2")


def test_r_multiple_none_without_stop() -> None:
    rt = compute_round_trip(
        symbol="AAPL", side="long", qty=10,
        entry_ts=ENTRY, entry_price=Decimal("100"),
        exit_ts=EXIT, exit_price=Decimal("104"),
    )
    assert rt.r_multiple is None


def test_rejects_unknown_side() -> None:
    with pytest.raises(ValueError, match="unknown side"):
        compute_round_trip(
            symbol="AAPL", side="flat", qty=10,
            entry_ts=ENTRY, entry_price=Decimal("100"),
            exit_ts=EXIT, exit_price=Decimal("104"),
        )


def test_rejects_non_positive_qty() -> None:
    with pytest.raises(ValueError, match="qty must be > 0"):
        compute_round_trip(
            symbol="AAPL", side="long", qty=0,
            entry_ts=ENTRY, entry_price=Decimal("100"),
            exit_ts=EXIT, exit_price=Decimal("104"),
        )


def test_wash_sale_profit_is_never_a_wash() -> None:
    assert is_wash_sale(
        symbol="AAPL", exit_ts=EXIT, net_pnl=Decimal("40"),
        other_entries=[("AAPL", EXIT + timedelta(days=2))],
    ) is False


def test_wash_sale_loss_with_reentry_within_30_days() -> None:
    assert is_wash_sale(
        symbol="AAPL", exit_ts=EXIT, net_pnl=Decimal("-30"),
        other_entries=[("AAPL", EXIT + timedelta(days=10))],
    ) is True


def test_wash_sale_loss_with_reentry_before_the_sale() -> None:
    # The window covers 30 days BEFORE the sale too.
    assert is_wash_sale(
        symbol="AAPL", exit_ts=EXIT, net_pnl=Decimal("-30"),
        other_entries=[("AAPL", EXIT - timedelta(days=20))],
    ) is True


def test_wash_sale_loss_with_reentry_outside_30_days() -> None:
    assert is_wash_sale(
        symbol="AAPL", exit_ts=EXIT, net_pnl=Decimal("-30"),
        other_entries=[("AAPL", EXIT + timedelta(days=45))],
    ) is False


def test_wash_sale_ignores_other_symbols() -> None:
    assert is_wash_sale(
        symbol="AAPL", exit_ts=EXIT, net_pnl=Decimal("-30"),
        other_entries=[("MSFT", EXIT + timedelta(days=2))],
    ) is False


def test_pick_exit_order_long_closed_by_a_sell() -> None:
    cands = [
        ExitCandidate("buy", ENTRY, Decimal("100")),  # the entry — wrong side
        ExitCandidate("sell", EXIT, Decimal("104")),  # the exit
    ]
    chosen = pick_exit_order(cands, entry_side="long", opened_at=ENTRY)
    assert chosen is not None
    assert chosen.avg_fill_price == Decimal("104")


def test_pick_exit_order_short_closed_by_a_buy() -> None:
    cands = [ExitCandidate("buy", EXIT, Decimal("96"))]
    chosen = pick_exit_order(cands, entry_side="short", opened_at=ENTRY)
    assert chosen is not None
    assert chosen.avg_fill_price == Decimal("96")


def test_pick_exit_order_takes_the_most_recent() -> None:
    cands = [
        ExitCandidate("sell", EXIT, Decimal("104")),
        ExitCandidate("sell", EXIT + timedelta(minutes=5), Decimal("105")),
    ]
    chosen = pick_exit_order(cands, entry_side="long", opened_at=ENTRY)
    assert chosen is not None
    assert chosen.avg_fill_price == Decimal("105")


def test_pick_exit_order_ignores_fills_before_the_open() -> None:
    cands = [ExitCandidate("sell", ENTRY - timedelta(hours=1), Decimal("99"))]
    assert pick_exit_order(cands, entry_side="long", opened_at=ENTRY) is None


def test_pick_exit_order_none_when_no_opposite_side() -> None:
    cands = [ExitCandidate("buy", EXIT, Decimal("104"))]
    assert pick_exit_order(cands, entry_side="long", opened_at=ENTRY) is None
