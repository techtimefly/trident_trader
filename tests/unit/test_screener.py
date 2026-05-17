"""Exhaustive unit tests for the pure screener core (criteria + engine).

No network, no database — only the pure value objects and filter/rank functions.
"""
from __future__ import annotations

from decimal import Decimal

from trident.screener.criteria import ScreenCandidate, ScreenCriteria, ScreenResult
from trident.screener.engine import passes, rank, screen


def _cand(
    symbol: str = "AAA",
    price: str = "10.00",
    avg_volume: int = 1_000_000,
    change_pct: str = "0.00",
) -> ScreenCandidate:
    return ScreenCandidate(
        symbol=symbol,
        price=Decimal(price),
        avg_volume=avg_volume,
        change_pct=Decimal(change_pct),
    )


# --------------------------------------------------------------------------
# ScreenCriteria
# --------------------------------------------------------------------------


def test_empty_criteria_describes_as_no_filters() -> None:
    assert ScreenCriteria().describe() == "no filters (matches everything)"


def test_describe_lists_active_bounds() -> None:
    crit = ScreenCriteria(
        min_price=Decimal("1"),
        max_price=Decimal("5"),
        min_avg_volume=2_000_000,
        min_change_pct=Decimal("-3"),
        max_change_pct=Decimal("8"),
    )
    text = crit.describe()
    assert "price >= $1" in text
    assert "price <= $5" in text
    assert "avg vol >= 2,000,000" in text
    assert "change >= -3%" in text
    assert "change <= 8%" in text


def test_criteria_is_frozen() -> None:
    crit = ScreenCriteria()
    try:
        crit.min_price = Decimal("1")  # type: ignore[misc]
    except AttributeError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("ScreenCriteria should be frozen")


# --------------------------------------------------------------------------
# passes() — each bound, inclusive edges, None = no bound
# --------------------------------------------------------------------------


def test_empty_criteria_passes_everything() -> None:
    assert passes(_cand(price="0.01"), ScreenCriteria()) is True
    assert passes(_cand(price="999999"), ScreenCriteria()) is True


def test_min_price_rejects_below_and_accepts_at_bound() -> None:
    crit = ScreenCriteria(min_price=Decimal("5.00"))
    assert passes(_cand(price="4.99"), crit) is False
    assert passes(_cand(price="5.00"), crit) is True  # inclusive
    assert passes(_cand(price="5.01"), crit) is True


def test_max_price_rejects_above_and_accepts_at_bound() -> None:
    crit = ScreenCriteria(max_price=Decimal("1.00"))
    assert passes(_cand(price="1.01"), crit) is False
    assert passes(_cand(price="1.00"), crit) is True  # inclusive
    assert passes(_cand(price="0.99"), crit) is True


def test_sub_dollar_screen_finds_penny_stocks() -> None:
    """The headline use case: max_price = $1 surfaces sub-$1 names only."""
    crit = ScreenCriteria(max_price=Decimal("1.00"))
    penny = _cand(symbol="PENY", price="0.42")
    normal = _cand(symbol="BIG", price="250.00")
    assert passes(penny, crit) is True
    assert passes(normal, crit) is False


def test_min_avg_volume_rejects_thin_names() -> None:
    crit = ScreenCriteria(min_avg_volume=1_000_000)
    assert passes(_cand(avg_volume=999_999), crit) is False
    assert passes(_cand(avg_volume=1_000_000), crit) is True  # inclusive
    assert passes(_cand(avg_volume=1_000_001), crit) is True


def test_min_change_pct_bound() -> None:
    crit = ScreenCriteria(min_change_pct=Decimal("5"))
    assert passes(_cand(change_pct="4.99"), crit) is False
    assert passes(_cand(change_pct="5.00"), crit) is True
    assert passes(_cand(change_pct="20.00"), crit) is True


def test_max_change_pct_bound() -> None:
    crit = ScreenCriteria(max_change_pct=Decimal("-5"))
    assert passes(_cand(change_pct="-4.99"), crit) is False
    assert passes(_cand(change_pct="-5.00"), crit) is True
    assert passes(_cand(change_pct="-20.00"), crit) is True


def test_change_band_brackets_both_sides() -> None:
    crit = ScreenCriteria(min_change_pct=Decimal("-2"), max_change_pct=Decimal("2"))
    assert passes(_cand(change_pct="0"), crit) is True
    assert passes(_cand(change_pct="-2"), crit) is True
    assert passes(_cand(change_pct="2"), crit) is True
    assert passes(_cand(change_pct="-2.01"), crit) is False
    assert passes(_cand(change_pct="2.01"), crit) is False


def test_all_bounds_must_pass_together() -> None:
    crit = ScreenCriteria(
        min_price=Decimal("1"),
        max_price=Decimal("10"),
        min_avg_volume=500_000,
    )
    # Passes price but fails volume.
    assert passes(_cand(price="5", avg_volume=100_000), crit) is False
    # Passes volume but fails price.
    assert passes(_cand(price="50", avg_volume=900_000), crit) is False
    # Passes all three.
    assert passes(_cand(price="5", avg_volume=900_000), crit) is True


# --------------------------------------------------------------------------
# rank()
# --------------------------------------------------------------------------


def test_rank_orders_by_volume_descending() -> None:
    low = _cand(symbol="LOW", avg_volume=100)
    mid = _cand(symbol="MID", avg_volume=5_000)
    high = _cand(symbol="HIGH", avg_volume=9_000)
    ordered = rank([low, high, mid])
    assert [c.symbol for c in ordered] == ["HIGH", "MID", "LOW"]


def test_rank_breaks_volume_ties_on_symbol() -> None:
    a = _cand(symbol="ZZZ", avg_volume=1_000)
    b = _cand(symbol="AAA", avg_volume=1_000)
    ordered = rank([a, b])
    assert [c.symbol for c in ordered] == ["AAA", "ZZZ"]


def test_rank_of_empty_is_empty() -> None:
    assert rank([]) == ()


def test_rank_returns_tuple() -> None:
    assert isinstance(rank([_cand()]), tuple)


# --------------------------------------------------------------------------
# screen() — the full pipeline
# --------------------------------------------------------------------------


def test_screen_filters_then_ranks() -> None:
    crit = ScreenCriteria(max_price=Decimal("1.00"))
    cands = [
        _cand(symbol="A", price="0.50", avg_volume=2_000),
        _cand(symbol="B", price="0.90", avg_volume=9_000),
        _cand(symbol="C", price="5.00", avg_volume=99_999),  # too expensive
    ]
    result = screen(cands, crit)
    assert result.scanned == 3
    assert result.matched == 2
    # B has higher volume so it ranks first.
    assert [c.symbol for c in result.matches] == ["B", "A"]


def test_screen_empty_universe() -> None:
    result = screen([], ScreenCriteria())
    assert result.scanned == 0
    assert result.matched == 0
    assert result.matches == ()


def test_screen_no_matches() -> None:
    crit = ScreenCriteria(min_price=Decimal("1000"))
    result = screen([_cand(price="10"), _cand(price="20")], crit)
    assert result.scanned == 2
    assert result.matched == 0


def test_screen_all_match_with_empty_criteria() -> None:
    cands = [_cand(symbol="A", avg_volume=1), _cand(symbol="B", avg_volume=2)]
    result = screen(cands, ScreenCriteria())
    assert result.matched == 2
    assert result.criteria == ScreenCriteria()


def test_screen_accepts_a_generator() -> None:
    """screen() must materialise its iterable so scanned/matched are consistent."""
    crit = ScreenCriteria()
    result = screen((c for c in [_cand(symbol="X"), _cand(symbol="Y")]), crit)
    assert result.scanned == 2
    assert result.matched == 2


def test_screen_result_matched_property() -> None:
    res = ScreenResult(criteria=ScreenCriteria(), matches=(_cand(), _cand()), scanned=5)
    assert res.matched == 2
    assert res.scanned == 5


def test_screen_result_defaults_are_empty() -> None:
    res = ScreenResult(criteria=ScreenCriteria())
    assert res.matches == ()
    assert res.scanned == 0
    assert res.matched == 0
