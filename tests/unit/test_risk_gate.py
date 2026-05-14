from __future__ import annotations

from datetime import UTC, datetime, time
from decimal import Decimal

import pytest

from trident.risk.gate import AccountState, GateDecision, MarketState, evaluate
from trident.risk.limits import RiskLimits
from trident.strategies.base import Signal


def make_signal(
    symbol: str = "AAPL",
    side: str = "long",
    entry: str = "100",
    stop: str = "90",
    target: str = "120",
) -> Signal:
    return Signal(
        ts=datetime(2026, 5, 14, 14, 30, tzinfo=UTC),
        strategy="orb_5m",
        symbol=symbol,
        side=side,
        entry_price=Decimal(entry),
        stop_price=Decimal(stop),
        target_price=Decimal(target),
    )


def make_account(
    equity: str = "50000",
    starting: str = "50000",
    buying_power: str = "100000",
    open_positions: dict[str, int] | None = None,
) -> AccountState:
    return AccountState(
        equity=Decimal(equity),
        starting_equity_today=Decimal(starting),
        buying_power=Decimal(buying_power),
        open_positions=open_positions or {},
    )


DEFAULTS = RiskLimits()
GOOD_TIME = time(10, 0)


def test_approves_clean_signal() -> None:
    decision = evaluate(make_signal(), make_account(), MarketState(), DEFAULTS, GOOD_TIME)
    assert decision.approved
    assert decision.reason == "ok"
    assert decision.shares > 0


def test_rejects_when_kill_switch_engaged() -> None:
    market = MarketState(kill_switch_active=True)
    d = evaluate(make_signal(), make_account(), market, DEFAULTS, GOOD_TIME)
    assert not d.approved
    assert d.reason == "kill_switch"


def test_rejects_before_no_entry_before() -> None:
    d = evaluate(make_signal(), make_account(), MarketState(), DEFAULTS, time(9, 31))
    assert d.reason == "too_early"


def test_rejects_after_no_entry_after() -> None:
    d = evaluate(make_signal(), make_account(), MarketState(), DEFAULTS, time(11, 0))
    assert d.reason == "too_late"


def test_rejects_existing_position() -> None:
    account = make_account(open_positions={"AAPL": 10})
    d = evaluate(make_signal("AAPL"), account, MarketState(), DEFAULTS, GOOD_TIME)
    assert d.reason == "existing_position"


def test_rejects_when_at_max_positions() -> None:
    account = make_account(open_positions={"MSFT": 1, "NVDA": 2, "AMD": 3})
    d = evaluate(make_signal("AAPL"), account, MarketState(), DEFAULTS, GOOD_TIME)
    assert d.reason == "max_positions"


def test_rejects_when_daily_loss_tripped() -> None:
    # 50k starting, current 48k = 4% drawdown, limit 2%
    account = make_account(equity="48000", starting="50000")
    d = evaluate(make_signal(), account, MarketState(), DEFAULTS, GOOD_TIME)
    assert d.reason == "daily_loss_limit"


def test_rejects_bad_long_stop() -> None:
    d = evaluate(
        make_signal(entry="100", stop="101", target="105"),
        make_account(),
        MarketState(),
        DEFAULTS,
        GOOD_TIME,
    )
    assert d.reason == "bad_stop"


def test_rejects_bad_long_target() -> None:
    d = evaluate(
        make_signal(entry="100", stop="98", target="99"),
        make_account(),
        MarketState(),
        DEFAULTS,
        GOOD_TIME,
    )
    assert d.reason == "bad_target"


def test_rejects_unknown_side() -> None:
    d = evaluate(make_signal(side="sideways"), make_account(), MarketState(), DEFAULTS, GOOD_TIME)
    assert d.reason == "bad_side"


def test_rejects_wide_spread() -> None:
    # mid=100, spread=0.5 → 0.5% > 0.2% limit
    market = MarketState(bid=Decimal("99.75"), ask=Decimal("100.25"))
    d = evaluate(make_signal(), make_account(), market, DEFAULTS, GOOD_TIME)
    assert d.reason == "wide_spread"


def test_accepts_tight_spread() -> None:
    # mid=100, spread=0.10 → 0.1% < 0.2% limit
    market = MarketState(bid=Decimal("99.95"), ask=Decimal("100.05"))
    d = evaluate(make_signal(), make_account(), market, DEFAULTS, GOOD_TIME)
    assert d.approved


def test_rejects_low_volume() -> None:
    market = MarketState(avg_daily_volume=500_000)
    d = evaluate(make_signal(), make_account(), market, DEFAULTS, GOOD_TIME)
    assert d.reason == "low_volume"


def test_rejects_zero_shares() -> None:
    # Tiny account so 1% / large stop distance → 0 shares
    account = make_account(equity="50", starting="50", buying_power="50")
    d = evaluate(
        make_signal(entry="1000", stop="900", target="1200"),
        account,
        MarketState(),
        DEFAULTS,
        GOOD_TIME,
    )
    assert d.reason == "zero_shares"


def test_rejects_insufficient_buying_power() -> None:
    account = make_account(equity="100000", starting="100000", buying_power="100")
    d = evaluate(make_signal(), account, MarketState(), DEFAULTS, GOOD_TIME)
    assert d.reason == "insufficient_buying_power"


def test_sizes_down_to_notional_cap() -> None:
    # equity 10k, default 50% cap = $5000 notional max. Risk-budget would buy 1000
    # shares (1% / $0.10 stop) but $5000 / $100 = 50 shares max. We expect approval
    # with 50 shares — the trade still happens, just smaller.
    account = make_account(equity="10000", starting="10000", buying_power="10000000")
    d = evaluate(
        make_signal(entry="100", stop="99.90", target="105"),
        account,
        MarketState(),
        DEFAULTS,
        GOOD_TIME,
    )
    assert d.approved
    assert d.shares == 50
    assert "sized down" in d.detail


def test_rejects_when_even_one_share_exceeds_notional_cap() -> None:
    # Equity $100, 50% cap = $50 max notional. Entry $60 — even 1 share blows the cap.
    # Risk-budget would buy 1 share (1% of $100 = $1, stop dist $1 → 1 share) so we
    # exercise the rejection branch rather than zero_shares.
    account = make_account(equity="100", starting="100", buying_power="1000")
    d = evaluate(
        make_signal(entry="60", stop="59", target="62"),
        account,
        MarketState(),
        DEFAULTS,
        GOOD_TIME,
    )
    assert d.reason == "position_too_large"


@pytest.mark.parametrize(
    "kwargs,expected_reason",
    [
        ({}, "ok"),
        ({"kill": True}, "kill_switch"),
        ({"now": time(9, 30)}, "too_early"),
    ],
)
def test_short_circuit_order(kwargs: dict[str, object], expected_reason: str) -> None:
    """The first failing check wins — verify ordering by stacking failures."""
    account = make_account(open_positions={"AAPL": 10})  # would trigger existing_position
    market = MarketState(kill_switch_active=bool(kwargs.get("kill", False)))
    now = kwargs.get("now", GOOD_TIME)
    assert isinstance(now, time)
    decision: GateDecision = evaluate(make_signal(), account, market, DEFAULTS, now)
    if expected_reason == "ok":
        # The clean kwargs case still has the existing_position pre-condition above —
        # so we expect that, not "ok". This documents that account-level checks fire
        # before market checks once kill / time gates pass.
        assert decision.reason == "existing_position"
    else:
        assert decision.reason == expected_reason
