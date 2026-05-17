from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# Hand-maintained holiday list for v0.1. The Alpaca calendar endpoint is the source
# of truth at runtime; this fallback covers offline / unit-test cases.
US_MARKET_HOLIDAYS_2026: frozenset[date] = frozenset(
    {
        date(2026, 1, 1),    # New Year's Day
        date(2026, 1, 19),   # MLK Day
        date(2026, 2, 16),   # Presidents Day
        date(2026, 4, 3),    # Good Friday
        date(2026, 5, 25),   # Memorial Day
        date(2026, 6, 19),   # Juneteenth
        date(2026, 7, 3),    # Independence Day (observed)
        date(2026, 9, 7),    # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
    }
)

US_MARKET_EARLY_CLOSES_2026: frozenset[date] = frozenset(
    {
        date(2026, 7, 2),    # Day before Independence Day
        date(2026, 11, 27),  # Day after Thanksgiving
        date(2026, 12, 24),  # Christmas Eve
    }
)


@dataclass(frozen=True)
class MarketSession:
    trading_day: date
    open_at: datetime
    close_at: datetime
    is_early_close: bool


def now_et() -> datetime:
    return datetime.now(UTC).astimezone(ET)


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def is_trading_day(d: date) -> bool:
    return is_weekday(d) and d not in US_MARKET_HOLIDAYS_2026


def session_for(d: date) -> MarketSession | None:
    """Returns the session window for `d`, or None if the market is closed that day."""
    if not is_trading_day(d):
        return None
    is_early = d in US_MARKET_EARLY_CLOSES_2026
    open_at = datetime.combine(d, REGULAR_OPEN, tzinfo=ET)
    close_at = datetime.combine(d, EARLY_CLOSE if is_early else REGULAR_CLOSE, tzinfo=ET)
    return MarketSession(d, open_at, close_at, is_early)


def current_session(at: datetime | None = None) -> MarketSession | None:
    at = (at or now_et()).astimezone(ET)
    return session_for(at.date())


def is_market_open(at: datetime | None = None) -> bool:
    at = (at or now_et()).astimezone(ET)
    sess = session_for(at.date())
    if sess is None:
        return False
    return sess.open_at <= at < sess.close_at


def minutes_since_open(at: datetime | None = None) -> int | None:
    at = (at or now_et()).astimezone(ET)
    sess = session_for(at.date())
    if sess is None or at < sess.open_at:
        return None
    return int((at - sess.open_at).total_seconds() // 60)


def minutes_until_close(at: datetime | None = None) -> int | None:
    at = (at or now_et()).astimezone(ET)
    sess = session_for(at.date())
    if sess is None or at >= sess.close_at:
        return None
    return int((sess.close_at - at).total_seconds() // 60)


def next_trading_day(after: date) -> date:
    d = after + timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def nth_business_day_back(end: date, n: int) -> date:
    """The trading day that begins an ``n``-trading-day window ending on ``end``.

    Counts inclusively from ``end``: ``n=1`` returns ``end`` if it is a trading
    day, otherwise the most recent trading day on or before it. A non-trading
    ``end`` is skipped and not counted. Raises ``ValueError`` for ``n < 1``.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    count = 0
    d = end
    while True:
        if is_trading_day(d):
            count += 1
            if count == n:
                return d
        d -= timedelta(days=1)
