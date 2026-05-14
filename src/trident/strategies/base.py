from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from trident.data.bars import Bar, BarStore


@dataclass(frozen=True)
class Signal:
    ts: datetime
    strategy: str
    symbol: str
    side: str  # "long" | "short"
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def risk_per_share(self) -> Decimal:
        return abs(self.entry_price - self.stop_price)

    @property
    def reward_per_share(self) -> Decimal:
        return abs(self.target_price - self.entry_price)

    @property
    def reward_to_risk(self) -> Decimal:
        risk = self.risk_per_share
        return self.reward_per_share / risk if risk > 0 else Decimal("0")


class Strategy(Protocol):
    name: str

    def on_bar(self, bar: Bar, store: BarStore) -> Signal | None:
        """Called once per closed 1-min bar. Return a Signal to propose a trade."""
        ...
