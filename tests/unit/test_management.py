from __future__ import annotations

from decimal import Decimal

from trident.data.bars import Bar, BarStore
from trident.strategies.management import (
    ClosePosition,
    ManagedPositionView,
    ManagesPositions,
    ScaleIn,
    ScaleOut,
    TrailStop,
)
from trident.strategies.orb import OpeningRangeBreakout
from trident.strategies.vwap_reversion import VWAPReversion


def _view() -> ManagedPositionView:
    return ManagedPositionView(
        symbol="AAPL",
        side="long",
        qty=10,
        avg_entry=Decimal("100"),
        stop_price=Decimal("98"),
        target_price=Decimal("104"),
    )


def test_action_types_construct() -> None:
    assert TrailStop(new_stop=Decimal("99")).new_stop == Decimal("99")
    assert ScaleIn(qty=5).qty == 5
    assert ScaleOut(qty=3).qty == 3
    assert ClosePosition().reason == "strategy_exit"
    assert ClosePosition(reason="target_near").reason == "target_near"


def test_managed_position_view_is_frozen() -> None:
    view = _view()
    assert view.symbol == "AAPL"
    assert view.side == "long"


def test_entry_only_strategies_are_not_managers() -> None:
    # ORB and VwapReversion are entry-only — the runner must skip management
    # for them, so they must NOT satisfy ManagesPositions.
    assert not isinstance(OpeningRangeBreakout(symbols=["AAPL"]), ManagesPositions)
    assert not isinstance(VWAPReversion(symbols=["AAPL"]), ManagesPositions)


def test_a_strategy_with_manage_satisfies_the_protocol() -> None:
    class Managing:
        name = "managing"

        def on_bar(self, bar: Bar, store: BarStore):  # type: ignore[no-untyped-def]
            return None

        def manage(self, bar, store, position):  # type: ignore[no-untyped-def]
            return [TrailStop(new_stop=Decimal("99"))]

    strat = Managing()
    assert isinstance(strat, ManagesPositions)
    actions = strat.manage(None, None, _view())  # type: ignore[arg-type]
    assert actions == [TrailStop(new_stop=Decimal("99"))]
