"""Financial Modeling Prep (FMP) data layer for the screener.

This module is the *only* place that talks to FMP. FMP is used as a fast
server-side universe pre-filter: instead of scanning every US-equity symbol's
daily bars, the screener asks FMP for the symbols matching a
:class:`~trident.screener.criteria.ScreenCriteria`'s market-cap / sector /
exchange / price / volume bounds, then the Alpaca daily-bar pipeline
(:mod:`trident.screener.data`) computes the authoritative Decimal price /
average volume / % change for that much smaller universe.

FMP is optional. When no ``FMP_API_KEY`` is configured — or any request fails —
:func:`fetch_fmp_universe` degrades to an empty list and the caller falls back
to the alphabetical Alpaca universe. The screener is outer-ring: it degrades,
never aborts.

Money crossing the FMP boundary is stringified before the ``Decimal``
constructor so no float ever enters the pipeline (same discipline as
:mod:`trident.screener.data`).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from trident.audit.log import get_logger
from trident.screener.criteria import ScreenCriteria
from trident.settings import get_settings

log = get_logger("screener.fmp")

# The screener endpoint. FMP retired the legacy v3 path
# ("/api/v3/stock-screener" — now HTTP 403 "Legacy Endpoint"); the current path
# is "/stable/company-screener". Note FMP has moved this endpoint behind a paid
# plan: a free-tier key returns HTTP 402 "Restricted Endpoint", which
# fetch_fmp_universe degrades from cleanly (the caller falls back to the Alpaca
# universe). Kept as constants so the path is a one-line change.
FMP_BASE = "https://financialmodelingprep.com"
FMP_SCREENER_PATH = "/stable/company-screener"

# The eleven sector strings FMP's screener accepts for its `sector` param and
# returns in each row's `sector` field.
SECTORS: tuple[str, ...] = (
    "Basic Materials",
    "Communication Services",
    "Consumer Cyclical",
    "Consumer Defensive",
    "Energy",
    "Financial Services",
    "Healthcare",
    "Industrials",
    "Real Estate",
    "Technology",
    "Utilities",
)

# Exchange short codes FMP returns in `exchangeShortName` and accepts for its
# `exchange` param.
EXCHANGES: tuple[str, ...] = ("NASDAQ", "NYSE", "AMEX")

# How far to widen FMP's price band. FMP `price` is a recent quote while the
# engine's authoritative price is the latest Alpaca daily close, so the FMP
# pre-filter is deliberately loose — it must never exclude a symbol the engine
# would keep; the engine re-applies the exact price bound.
_PRICE_WIDEN = Decimal("0.10")

# FMP `volume` is SIP-scale; the engine's average volume is computed from the
# IEX feed, a fraction of consolidated volume. Pass FMP only a loose floor so
# it never wrongly excludes a symbol — the engine's volume bound is binding.
_VOLUME_FLOOR_FRACTION = Decimal("0.20")


@dataclass(frozen=True, slots=True)
class FmpAsset:
    """One row from the FMP screener — raw metadata, before the Alpaca leg.

    ``price`` and ``volume`` are FMP's own (SIP-scale) numbers; they are
    advisory only — the screener's authoritative price / average volume come
    from Alpaca daily bars. ``market_cap`` / ``sector`` / ``exchange`` are the
    fields FMP is the *sole* source for.
    """

    symbol: str
    market_cap: int | None
    sector: str | None
    exchange: str | None
    price: Decimal | None
    volume: int | None


def is_configured() -> bool:
    """True iff a non-blank ``FMP_API_KEY`` is set."""
    return bool(get_settings().fmp_api_key.strip())


def build_screener_params(criteria: ScreenCriteria, *, max_symbols: int) -> dict[str, str]:
    """Map a :class:`ScreenCriteria` to FMP stock-screener query parameters.

    Pure — no I/O. Price and volume bounds are widened into *loose* pre-filters
    (see ``_PRICE_WIDEN`` / ``_VOLUME_FLOOR_FRACTION``) because FMP's
    price/volume differ in scale and timing from the Alpaca-derived numbers the
    engine treats as authoritative. The % change bounds are dropped — FMP's
    screener has no such parameter; the engine applies % change against Alpaca
    bars. ``sector`` / ``exchange`` are sent only when exactly one value is
    selected (FMP accepts a single value per request); a multi-value allow-list
    is left for the engine to enforce.

    The API key is NOT included here — :func:`fetch_fmp_universe` adds it at
    call time so it never lands in a logged param dict.
    """
    params: dict[str, str] = {
        "country": "US",
        "isActivelyTrading": "true",
        "limit": str(max_symbols),
    }
    if criteria.min_price is not None:
        lo = criteria.min_price * (Decimal("1") - _PRICE_WIDEN)
        params["priceMoreThan"] = f"{lo:.2f}"
    if criteria.max_price is not None:
        hi = criteria.max_price * (Decimal("1") + _PRICE_WIDEN)
        params["priceLowerThan"] = f"{hi:.2f}"
    if criteria.min_avg_volume is not None:
        floor = int(Decimal(criteria.min_avg_volume) * _VOLUME_FLOOR_FRACTION)
        params["volumeMoreThan"] = str(floor)
    if criteria.min_market_cap is not None:
        params["marketCapMoreThan"] = str(criteria.min_market_cap)
    if criteria.max_market_cap is not None:
        params["marketCapLowerThan"] = str(criteria.max_market_cap)
    if len(criteria.sectors) == 1:
        params["sector"] = criteria.sectors[0]
    if len(criteria.exchanges) == 1:
        params["exchange"] = criteria.exchanges[0]
    return params


def _to_decimal(value: Any) -> Decimal | None:
    """FMP JSON number → Decimal, via ``str`` so no float enters the pipeline."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    """FMP JSON number → int. FMP sometimes sends ``1.39e12``-style floats."""
    if value is None:
        return None
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None


def parse_screener_response(payload: list[dict[str, Any]]) -> list[FmpAsset]:
    """Convert the FMP screener JSON array to :class:`FmpAsset` rows.

    Pure — no I/O. Tolerant: a row missing a field yields ``None`` for it; a
    row with no usable symbol, or a punctuated symbol (warrants / units /
    rights, which cannot be priced as ordinary equities — the same exclusion as
    :func:`trident.screener.data.fetch_universe`), is dropped.
    """
    out: list[FmpAsset] = []
    for row in payload:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol or "." in symbol or "/" in symbol:
            continue
        sector = row.get("sector")
        exchange = row.get("exchangeShortName")
        out.append(
            FmpAsset(
                symbol=symbol,
                market_cap=_to_int(row.get("marketCap")),
                sector=str(sector) if sector else None,
                exchange=str(exchange) if exchange else None,
                price=_to_decimal(row.get("price")),
                volume=_to_int(row.get("volume")),
            )
        )
    return out


def fetch_fmp_universe(
    criteria: ScreenCriteria,
    *,
    max_symbols: int = 500,
    timeout: float = 15.0,
) -> list[FmpAsset]:
    """Ask FMP for the symbols matching ``criteria`` — the one network call.

    Returns a list of :class:`FmpAsset`, or an empty list (degraded) when FMP
    is not configured, the request fails, the response is not 200, or the body
    is unparseable. Never raises — the screener is outer-ring; the caller falls
    back to the Alpaca universe on an empty result.
    """
    api_key = get_settings().fmp_api_key.strip()
    if not api_key:
        log.info("fmp_not_configured")
        return []

    params = build_screener_params(criteria, max_symbols=max_symbols)
    # Log the params WITHOUT the key — the key is added only on the wire.
    log.info("fmp_screener_request", path=FMP_SCREENER_PATH, params=params)
    try:
        resp = httpx.get(
            f"{FMP_BASE}{FMP_SCREENER_PATH}",
            params={**params, "apikey": api_key},
            timeout=timeout,
        )
    except Exception as exc:
        # Never log the exception object or traceback: httpx error messages and
        # the request repr embed the request URL, which carries the apikey
        # query parameter. Log only the exception class name.
        log.warning("fmp_screener_request_failed", error=type(exc).__name__)
        return []

    if resp.status_code != 200:
        # 402 = the screener endpoint is not on the key's FMP plan; 403 = the
        # legacy path. Log the status + a keyless body snippet, never the URL.
        log.warning(
            "fmp_screener_http_error",
            status=resp.status_code,
            detail=resp.text[:160],
        )
        return []

    try:
        payload = resp.json()
    except Exception:
        log.warning("fmp_screener_bad_json")
        return []

    if not isinstance(payload, list):
        log.warning("fmp_screener_bad_payload", payload_type=type(payload).__name__)
        return []

    assets = parse_screener_response(payload)
    log.info("fmp_screener_ok", returned=len(payload), parsed=len(assets))
    return assets
