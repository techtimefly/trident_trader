from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import Decimal


@dataclass(frozen=True)
class RiskLimits:
    """Static, per-session limits. Loaded from config + .env at process start."""

    risk_per_trade_pct: Decimal = Decimal("1.0")
    daily_loss_limit_pct: Decimal = Decimal("2.0")
    max_concurrent_positions: int = 3
    # Day-trades on liquid large-caps with tight ORB stops routinely size into
    # large notionals (e.g., 1% of $100k = $1000 risk / $0.50 stop = $200k notional).
    # The cap is a concentration backstop, not the primary safety. Gate sizes DOWN
    # to fit; the risk-per-trade budget then becomes the binding constraint.
    max_position_notional_pct: Decimal = Decimal("50")
    no_entry_before: time = time(9, 35)
    no_entry_after: time = time(11, 0)
    max_spread_pct: Decimal = Decimal("0.2")  # refuse if (ask - bid) / mid > 0.2%
    min_avg_daily_volume: int = 1_000_000


def daily_loss_remaining(
    starting_equity: Decimal,
    current_equity: Decimal,
    daily_loss_limit_pct: Decimal,
) -> Decimal:
    """Dollars of loss budget still available today. Zero or negative = halt."""
    budget = starting_equity * (daily_loss_limit_pct / Decimal("100"))
    drawdown = starting_equity - current_equity
    return budget - drawdown


def daily_loss_tripped(
    starting_equity: Decimal,
    current_equity: Decimal,
    daily_loss_limit_pct: Decimal,
) -> bool:
    return daily_loss_remaining(starting_equity, current_equity, daily_loss_limit_pct) <= 0
