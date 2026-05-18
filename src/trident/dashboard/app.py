"""Dashboard for the personal trading bot.

A small FastAPI app with HTMX panels that poll JSON endpoints every few seconds.
Designed to be opened on localhost only — no auth, no CORS exposure. If you want
to access it remotely, put it behind Tailscale or an SSH tunnel.

Run with:
    PYTHONPATH=src uvicorn trident.dashboard.app:app --host 127.0.0.1 --port 8765 --reload
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func, select, text

from trident.audit.log import configure_logging, get_logger
from trident.clock import ET, is_market_open, now_et, nth_business_day_back
from trident.dashboard.alpaca_view import (
    QuoteView,
    get_account,
    get_quotes,
    list_positions,
)
from trident.dashboard.settings_io import rewrite_env
from trident.persistence.daily_plan import (
    day_trades_in_window,
    get_for_day,
    notional_deployed_today,
    upsert,
)
from trident.persistence.models import (
    AuditEvent,
    LiveTrade,
    ReplayRun,
    ReplayTrade,
    Signal,
)
from trident.persistence.models import (
    Order as OrderModel,
)
from trident.persistence.screen_presets_store import (
    activate_preset,
    delete_preset,
    list_presets,
    upsert_preset,
)
from trident.persistence.session import session_scope
from trident.persistence.state import (
    kill_switch_engaged,
    last_heartbeat,
    set_kill_switch,
)
from trident.persistence.watchlist_store import (
    activate_watchlist,
    add_symbols,
    create_watchlist,
    delete_watchlist,
    get_watchlist,
    list_watchlists,
    remove_symbol,
    rename_watchlist,
    set_watchlist_symbols,
)
from trident.screener.criteria import ScreenCriteria
from trident.screener.data import build_candidates, resolve_universe
from trident.screener.engine import screen
from trident.screener.fmp import EXCHANGES, SECTORS, is_configured
from trident.screener.persistence import get_latest_screen, save_screen
from trident.screener.presets import (
    DEFAULT_CRITERIA,
    DEFAULT_LOOKBACK_DAYS,
    resolve_screen_criteria,
)
from trident.settings import get_settings
from trident.strategies.registry import available_strategies
from trident.suggest.client import suggest_stocks
from trident.suggest.persistence import get_latest_suggestions, save_suggestions
from trident.suggest.suggestion import PlanContext
from trident.watchlist import WATCHLIST

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
# Project root: app.py lives at src/trident/dashboard/app.py — go up 4 levels.
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

configure_logging()
app = FastAPI(title="Trident Trader")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _fmt_money(d: Decimal | None) -> str:
    if d is None:
        return "—"
    return f"${d:,.2f}"


def _fmt_pct(d: Decimal | None) -> str:
    if d is None:
        return "—"
    return f"{d:+.2f}%"


def _plain_decimal(d: Decimal) -> str:
    """Decimal as a plain string — trailing zeros trimmed, no scientific notation."""
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def _bot_status() -> dict[str, Any]:
    """Inferred from the heartbeat: fresh = running, stale = idle, never = not started."""
    hb = last_heartbeat()
    if hb is None:
        return {"label": "never_started", "fresh": False, "age_seconds": None}
    age = (datetime.now(UTC) - hb).total_seconds()
    return {
        "label": "running" if age < 30 else "stale",
        "fresh": age < 30,
        "age_seconds": int(age),
        "last_seen": hb.isoformat(),
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> Any:
    return templates.TemplateResponse(request, "trading.html", {"active_page": "trading"})


@app.get("/plan", response_class=HTMLResponse)
def plan_page(request: Request) -> Any:
    return templates.TemplateResponse(request, "plan.html", {"active_page": "plan"})


@app.get("/screener", response_class=HTMLResponse)
def screener_page(request: Request) -> Any:
    return templates.TemplateResponse(request, "screener.html", {"active_page": "screener"})


@app.get("/research", response_class=HTMLResponse)
def research_page(request: Request) -> Any:
    return templates.TemplateResponse(request, "research.html", {"active_page": "research"})


@app.get("/system", response_class=HTMLResponse)
def system_page(request: Request) -> Any:
    return templates.TemplateResponse(request, "system.html", {"active_page": "system"})


@app.get("/api/hero-mini", response_class=HTMLResponse)
def api_hero_mini(request: Request) -> Any:
    return templates.TemplateResponse(request, "_hero_mini.html", _hero_mini_context())


@app.get("/api/hero", response_class=HTMLResponse)
def api_hero(request: Request) -> Any:
    account = get_account()
    return templates.TemplateResponse(
        request,
        "_hero.html",
        {
            "account": account,
            "equity_fmt": _fmt_money(account.equity) if account else "—",
            "buying_power_fmt": _fmt_money(account.buying_power) if account else "—",
            "cash_fmt": _fmt_money(account.cash) if account else "—",
            "market_open": is_market_open(),
            "et_now": now_et().strftime("%H:%M ET • %a %b %d"),
            "bot": _bot_status(),
            "kill": kill_switch_engaged(),
        },
    )


@app.get("/api/positions", response_class=HTMLResponse)
def api_positions(request: Request) -> Any:
    positions = list_positions()
    rows = [
        {
            "symbol": p.symbol,
            "qty": p.qty,
            "side": p.side,
            "avg_entry": _fmt_money(p.avg_entry_price),
            "current": _fmt_money(p.current_price),
            "pl": _fmt_money(p.unrealized_pl),
            "plpc": _fmt_pct(p.unrealized_plpc),
            "pl_positive": p.unrealized_pl >= 0,
        }
        for p in positions
    ]
    return templates.TemplateResponse(request, "_positions.html", {"rows": rows})


@app.get("/api/signals", response_class=HTMLResponse)
def api_signals(request: Request) -> Any:
    today = now_et().date()
    cutoff = datetime.combine(today, datetime.min.time(), tzinfo=ET)
    with session_scope() as s:
        stmt = (
            select(Signal).where(Signal.ts >= cutoff).order_by(Signal.ts.desc()).limit(20)
        )
        rows = list(s.scalars(stmt))
        rendered = [
            {
                "ts": r.ts.astimezone(ET).strftime("%H:%M:%S"),
                "symbol": r.symbol,
                "side": r.side,
                "entry": _fmt_money(r.entry_price),
                "stop": _fmt_money(r.stop_price),
                "target": _fmt_money(r.target_price),
                "decision": r.gate_decision or "—",
                "reason": r.gate_reason or "—",
                "approved": r.gate_decision == "approved",
            }
            for r in rows
        ]
    return templates.TemplateResponse(request, "_signals.html", {"rows": rendered})


@app.get("/api/orders", response_class=HTMLResponse)
def api_orders(request: Request) -> Any:
    today = now_et().date()
    cutoff = datetime.combine(today, datetime.min.time(), tzinfo=ET)
    with session_scope() as s:
        stmt = (
            select(OrderModel)
            .where(OrderModel.submitted_at >= cutoff)
            .order_by(OrderModel.submitted_at.desc())
            .limit(20)
        )
        rows = list(s.scalars(stmt))
        rendered = [
            {
                "ts": r.submitted_at.astimezone(ET).strftime("%H:%M:%S") if r.submitted_at else "—",
                "symbol": r.symbol,
                "side": r.side,
                "qty": r.qty,
                "state": r.state,
                "filled_at": r.filled_at.astimezone(ET).strftime("%H:%M:%S") if r.filled_at else "—",
                "avg_fill": _fmt_money(r.avg_fill_price) if r.avg_fill_price else "—",
                "client_id": r.client_order_id,
                "is_terminal": r.state in {"filled", "canceled", "cancelled", "rejected", "expired"},
                "is_filled": r.state == "filled",
                "is_rejected": r.state in {"rejected", "canceled", "cancelled", "expired"},
            }
            for r in rows
        ]
    return templates.TemplateResponse(request, "_orders.html", {"rows": rendered})


@app.get("/api/audit", response_class=HTMLResponse)
def api_audit(request: Request) -> Any:
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    with session_scope() as s:
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.ts >= cutoff)
            .order_by(AuditEvent.ts.desc())
            .limit(30)
        )
        rows = list(s.scalars(stmt))
        rendered = [
            {
                "ts": r.ts.astimezone(ET).strftime("%H:%M:%S"),
                "event_type": r.event_type,
                "actor": r.actor,
                "summary": _summarize_payload(r.payload),
            }
            for r in rows
        ]
    return templates.TemplateResponse(request, "_audit.html", {"rows": rendered})


def _summarize_payload(payload: dict[str, Any]) -> str:
    if not payload:
        return ""
    pieces: list[str] = []
    for key in ("symbol", "side", "approved", "reason", "shares", "engaged"):
        if key in payload:
            pieces.append(f"{key}={payload[key]}")
    return " ".join(pieces) or str(payload)[:100]


def _hero_mini_context() -> dict[str, Any]:
    account = get_account()
    return {
        "account": account,
        "equity_fmt": _fmt_money(account.equity) if account else "—",
        "market_open": is_market_open(),
        "et_now": now_et().strftime("%H:%M ET • %a %b %d"),
        "bot": _bot_status(),
        "kill": kill_switch_engaged(),
    }


@app.get("/api/hero-bar", response_class=HTMLResponse)
def api_hero_bar(request: Request) -> Any:
    return templates.TemplateResponse(request, "_hero_bar.html", _hero_mini_context())


@app.post("/api/kill", response_class=HTMLResponse)
def api_kill_engage(request: Request) -> Any:
    set_kill_switch(True, actor="dashboard")
    return templates.TemplateResponse(request, "_hero_bar.html", _hero_mini_context())


@app.post("/api/kill/release", response_class=HTMLResponse)
def api_kill_release(request: Request) -> Any:
    set_kill_switch(False, actor="dashboard")
    return templates.TemplateResponse(request, "_hero_bar.html", _hero_mini_context())


def _daily_plan_context(notice: str | None = None, error: bool = False) -> dict[str, Any]:
    """Render context for the Daily Plan panel: today's plan + live usage.

    Defensive — the panel is outer-ring; a DB or Alpaca hiccup degrades it to
    placeholders rather than 500-ing.
    """
    today = now_et().date()
    ctx: dict[str, Any] = {
        "trading_day": today.strftime("%a %b %d"),
        "window_start": nth_business_day_back(today, 5).strftime("%b %d"),
        "notice": notice,
        "notice_error": error,
        "budget_pct_value": "",
        "max_day_trades_value": "",
        "has_budget": False,
        "budget_fmt": "—",
        "deployed_fmt": "—",
        "budget_used_pct": 0,
        "has_trade_cap": False,
        "day_trades_used": 0,
        "day_trades_cap": 0,
    }
    try:
        plan = get_for_day(today)
        if plan is not None and plan.budget_pct is not None:
            ctx["has_budget"] = True
            ctx["budget_pct_value"] = _plain_decimal(plan.budget_pct)
            deployed = notional_deployed_today(today)
            ctx["deployed_fmt"] = _fmt_money(deployed)
            account = get_account()
            if account is not None:
                budget = account.equity * (plan.budget_pct / Decimal("100"))
                ctx["budget_fmt"] = _fmt_money(budget)
                if budget > 0:
                    ctx["budget_used_pct"] = min(int(deployed / budget * 100), 100)
        if plan is not None and plan.max_day_trades is not None:
            ctx["has_trade_cap"] = True
            ctx["max_day_trades_value"] = str(plan.max_day_trades)
            ctx["day_trades_cap"] = plan.max_day_trades
            ctx["day_trades_used"] = day_trades_in_window(today)
    except Exception:
        if not notice:
            ctx["notice"] = "Could not load today's plan."
            ctx["notice_error"] = True
    return ctx


def _save_daily_plan(budget_pct_raw: str, max_day_trades_raw: str) -> tuple[str, bool]:
    """Parse, validate and persist today's plan. Returns (notice, is_error).

    A blank field means 'no cap' for that knob.
    """
    budget_pct: Decimal | None = None
    max_day_trades: int | None = None
    try:
        if budget_pct_raw.strip():
            budget_pct = Decimal(budget_pct_raw.strip())
            if not (Decimal("0") < budget_pct <= Decimal("100")):
                return "Capital budget must be between 0 and 100%.", True
        if max_day_trades_raw.strip():
            max_day_trades = int(max_day_trades_raw.strip())
            if max_day_trades < 0:
                return "Max day-trades cannot be negative.", True
    except (ValueError, ArithmeticError):
        return "Could not parse those values — check the numbers.", True
    try:
        upsert(now_et().date(), budget_pct, max_day_trades, actor="dashboard")
    except Exception:
        return "Failed to save the plan.", True
    return "Today's plan saved.", False


@app.get("/api/daily-plan", response_class=HTMLResponse)
def api_daily_plan(request: Request) -> Any:
    return templates.TemplateResponse(request, "_daily_plan.html", _daily_plan_context())


@app.post("/api/daily-plan", response_class=HTMLResponse)
async def api_daily_plan_save(request: Request) -> Any:
    # HTMX posts an application/x-www-form-urlencoded body; parse_qs is stdlib,
    # so no python-multipart dependency is needed for this one form.
    form = parse_qs((await request.body()).decode("utf-8"))
    budget_pct = (form.get("budget_pct") or [""])[0]
    max_day_trades = (form.get("max_day_trades") or [""])[0]
    notice, error = _save_daily_plan(budget_pct, max_day_trades)
    return templates.TemplateResponse(
        request, "_daily_plan.html", _daily_plan_context(notice=notice, error=error)
    )


@app.get("/api/replay", response_class=HTMLResponse)
def api_replay(request: Request) -> Any:
    with session_scope() as s:
        latest = s.scalars(
            select(ReplayRun).order_by(ReplayRun.started_at.desc()).limit(1)
        ).first()
        if latest is None:
            return templates.TemplateResponse(
                request, "_replay.html", {"run": None, "rows": []}
            )

        trades = list(
            s.scalars(
                select(ReplayTrade)
                .where(ReplayTrade.run_id == latest.id)
                .order_by(ReplayTrade.entry_ts)
            )
        )
        rows = [
            {
                "date": t.entry_ts.astimezone(ET).strftime("%Y-%m-%d"),
                "time": t.entry_ts.astimezone(ET).strftime("%H:%M"),
                "symbol": t.symbol,
                "side": t.side,
                "qty": t.qty,
                "entry": _fmt_money(t.entry_price),
                "exit": _fmt_money(t.exit_price),
                "exit_reason": t.exit_reason,
                "pnl": _fmt_money(t.pnl),
                "r": f"{t.r_multiple:+.2f}",
                "pl_positive": t.pnl >= 0,
                "is_target": t.exit_reason == "target",
                "is_stop": t.exit_reason == "stop",
                "is_eod": t.exit_reason == "eod",
            }
            for t in trades
        ]
        win_rate = (
            float(latest.wins) / float(latest.num_trades) * 100.0 if latest.num_trades else 0.0
        )
        run_view = {
            # first_day/last_day are calendar dates stored as midnight UTC —
            # format directly, do NOT tz-convert (that would shift a day back).
            "first_day": latest.first_day.strftime("%Y-%m-%d"),
            "last_day": latest.last_day.strftime("%Y-%m-%d"),
            "days": latest.days,
            "num_trades": latest.num_trades,
            "wins": latest.wins,
            "losses": latest.losses,
            "win_rate": f"{win_rate:.1f}%",
            "total_pnl": _fmt_money(latest.total_pnl),
            "total_pnl_positive": latest.total_pnl >= 0,
            "avg_r": f"{latest.avg_r:+.2f}",
            "started_at": latest.started_at.astimezone(ET).strftime("%Y-%m-%d %H:%M ET"),
            "strategy": latest.strategy,
            "mode": latest.mode,
            "is_honest": latest.mode == "honest",
            "gross_pnl": _fmt_money(latest.gross_pnl),
            "total_fees": _fmt_money(latest.total_fees),
            "slippage_bps": (
                _plain_decimal(latest.slippage_bps) if latest.slippage_bps is not None else "—"
            ),
        }
    return templates.TemplateResponse(
        request, "_replay.html", {"run": run_view, "rows": rows}
    )


def _comparison_context() -> dict[str, Any]:
    """Render context for the strategy-comparison panel: the most recent
    replay/backtest run for each strategy, ranked best-first by net P&L.

    Defensive — outer-ring; a DB hiccup degrades the panel to a placeholder
    rather than 500-ing the page.
    """
    try:
        with session_scope() as s:
            runs = list(
                s.scalars(select(ReplayRun).order_by(ReplayRun.started_at.desc()))
            )
            # Runs are newest-first, so the first row seen per strategy is its
            # most recent run.
            latest_by_strategy: dict[str, ReplayRun] = {}
            for run in runs:
                latest_by_strategy.setdefault(run.strategy, run)
            ranked = sorted(
                latest_by_strategy.values(), key=lambda r: r.total_pnl, reverse=True
            )
            rows = [
                {
                    "strategy": run.strategy,
                    "is_honest": run.mode == "honest",
                    "window": (
                        f"{run.first_day.strftime('%b %d')} → "
                        f"{run.last_day.strftime('%b %d')}"
                    ),
                    "days": run.days,
                    "num_trades": run.num_trades,
                    "wins": run.wins,
                    "losses": run.losses,
                    "win_rate": (
                        f"{float(run.wins) / float(run.num_trades) * 100.0:.1f}%"
                        if run.num_trades
                        else "—"
                    ),
                    "total_pnl": _fmt_money(run.total_pnl),
                    "total_pnl_positive": run.total_pnl >= 0,
                    "avg_r": f"{run.avg_r:+.2f}",
                    "avg_r_positive": run.avg_r >= 0,
                    "started_at": run.started_at.astimezone(ET).strftime("%Y-%m-%d %H:%M ET"),
                }
                for run in ranked
            ]
    except Exception:
        return {"rows": [], "load_error": True}
    return {"rows": rows, "load_error": False}


@app.get("/api/compare", response_class=HTMLResponse)
def api_compare(request: Request) -> Any:
    return templates.TemplateResponse(request, "_compare.html", _comparison_context())


def _fmt_volume(v: int) -> str:
    """Compact share-volume label: 2.4M, 530K, or the raw count."""
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return str(v)


def _screen_context(
    notice: str | None = None, error: bool = False, selected_id: str | None = None
) -> dict[str, Any]:
    """Render context for the screener panel: the latest run + its matches,
    plus the watchlists a result can be added to.

    ``selected_id`` is the watchlist the Add controls should target — carried
    through re-renders so a non-active list stays picked.

    Defensive — the screener is outer-ring; a DB hiccup degrades the panel to
    a placeholder rather than 500-ing the page.
    """
    # Watchlists for the "add to" picker, plus a symbol -> [list names] map so
    # each screener row can show which watchlists already hold that symbol.
    watchlists: list[dict[str, Any]] = []
    membership: dict[str, list[str]] = {}
    with contextlib.suppress(Exception):
        for rec in list_watchlists():
            watchlists.append(
                {"id": str(rec.id), "name": rec.name, "is_active": rec.is_active}
            )
            for sym in rec.symbols:
                membership.setdefault(sym, []).append(rec.name)
    # Which watchlist the Add controls target: the user's pick when it is still
    # valid, else the active list, else the first. This is what lets a
    # non-active list stay selected across the panel's re-renders.
    chosen = selected_id if any(w["id"] == selected_id for w in watchlists) else None
    if chosen is None:
        chosen = next(
            (w["id"] for w in watchlists if w["is_active"]),
            watchlists[0]["id"] if watchlists else None,
        )
    for w in watchlists:
        w["is_selected"] = w["id"] == chosen
    base: dict[str, Any] = {
        "watchlists": watchlists,
        "notice": notice,
        "notice_error": error,
    }
    try:
        latest = get_latest_screen()
    except Exception:
        return {**base, "run": None, "rows": [], "load_error": True}
    if latest is None:
        return {**base, "run": None, "rows": [], "load_error": False}

    crit = latest.criteria
    run_view = {
        "started_at": latest.started_at.astimezone(ET).strftime("%Y-%m-%d %H:%M ET"),
        "universe_size": latest.universe_size,
        "scanned": latest.scanned,
        "matched": latest.matched,
        "lookback_days": latest.lookback_days,
        "filters": crit.describe(),
    }
    rows = [
        {
            "rank": idx,
            "symbol": c.symbol,
            "price": _fmt_money(c.price),
            "avg_volume": _fmt_volume(c.avg_volume),
            "change_pct": _fmt_pct(c.change_pct),
            "change_positive": c.change_pct >= 0,
            "on_lists": membership.get(c.symbol, []),
        }
        for idx, c in enumerate(latest.matches, start=1)
    ]
    return {**base, "run": run_view, "rows": rows, "load_error": False}


@app.get("/api/screen", response_class=HTMLResponse)
def api_screen(request: Request, watchlist_id: str | None = None) -> Any:
    return templates.TemplateResponse(
        request, "_screen.html", _screen_context(selected_id=watchlist_id)
    )


def _add_screen_symbols_to_watchlist(form: dict[str, list[str]]) -> tuple[str, bool]:
    """Add screener result(s) to a chosen watchlist. Returns (notice, is_error).

    The clicked button supplies ``symbol``: a single ticker, or the sentinel
    ``__all__`` to add every match from the latest screen.
    """
    wid = _watchlist_id_from_form(form)
    if wid is None:
        return "Pick a watchlist first.", True
    try:
        target = get_watchlist(wid)
    except Exception:
        return "Could not read the watchlist.", True
    if target is None:
        return "That watchlist no longer exists.", True
    symbol = (form.get("symbol") or [""])[0].strip()
    if symbol == "__all__":
        try:
            latest = get_latest_screen()
        except Exception:
            return "Could not read the latest screen.", True
        if latest is None or not latest.matches:
            return "No screen results to add.", True
        symbols = [c.symbol for c in latest.matches]
    elif symbol:
        symbols = [symbol]
    else:
        return "No symbol selected.", True
    try:
        added = add_symbols(wid, symbols)
    except ValueError as exc:
        return str(exc), True
    except Exception:
        return "Could not add to the watchlist.", True
    if not added:
        return f"Already on {target.name!r} — nothing to add.", False
    return f"Added {len(added)} symbol(s) to {target.name!r}: {', '.join(added)}.", False


@app.post("/api/screen/add-to-watchlist", response_class=HTMLResponse)
async def api_screen_add_to_watchlist(request: Request) -> Any:
    form = parse_qs((await request.body()).decode("utf-8"))
    notice, error = _add_screen_symbols_to_watchlist(form)
    selected = (form.get("watchlist_id") or [""])[0].strip() or None
    return templates.TemplateResponse(
        request,
        "_screen.html",
        _screen_context(notice=notice, error=error, selected_id=selected),
    )


def _criteria_form_values(c: ScreenCriteria, lookback: int) -> dict[str, Any]:
    """A ScreenCriteria as string form-input values for the edit form."""

    def _s(v: Any) -> str:
        return "" if v is None else str(v)

    return {
        "min_price": _s(c.min_price),
        "max_price": _s(c.max_price),
        "min_avg_volume": _s(c.min_avg_volume),
        "min_change": _s(c.min_change_pct),
        "max_change": _s(c.max_change_pct),
        "min_market_cap": _s(c.min_market_cap),
        "max_market_cap": _s(c.max_market_cap),
        "sectors": list(c.sectors),
        "exchanges": list(c.exchanges),
        "lookback_days": str(lookback),
    }


def _screen_presets_context(notice: str | None = None, error: bool = False) -> dict[str, Any]:
    """Render context for the Screen filters panel: the presets list plus the
    active preset's editable bounds.

    Defensive — the screener is outer-ring; a DB hiccup degrades the panel to a
    placeholder rather than 500-ing the page.
    """
    ctx: dict[str, Any] = {
        "notice": notice,
        "notice_error": error,
        "load_error": False,
        "fmp_configured": is_configured(),
        "sectors_all": list(SECTORS),
        "exchanges_all": list(EXCHANGES),
        "presets": [],
        "active": None,
        "form": _criteria_form_values(DEFAULT_CRITERIA, DEFAULT_LOOKBACK_DAYS),
    }
    try:
        presets = list_presets()
    except Exception:
        ctx["load_error"] = True
        return ctx
    ctx["presets"] = [
        {
            "id": str(p.id),
            "name": p.name,
            "is_active": p.is_active,
            "summary": p.criteria.describe(),
            "lookback_days": p.lookback_days,
            "source": p.source,
        }
        for p in presets
    ]
    active = next((p for p in presets if p.is_active), None)
    if active is not None:
        ctx["active"] = {"id": str(active.id), "name": active.name}
        ctx["form"] = _criteria_form_values(active.criteria, active.lookback_days)
    return ctx


def _optional_decimal(raw: str) -> Decimal | None:
    raw = raw.strip()
    return Decimal(raw) if raw else None


def _optional_int(raw: str) -> int | None:
    raw = raw.strip()
    return int(raw) if raw else None


def _preset_id_from_form(form: dict[str, list[str]]) -> uuid.UUID | None:
    raw = (form.get("preset_id") or [""])[0].strip()
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _save_screen_preset(form: dict[str, list[str]]) -> tuple[str, bool]:
    """Parse, validate and persist a screen preset from the edit form.

    Returns (notice, is_error). A saved preset becomes the active one. A blank
    numeric field means 'no bound' for that knob.
    """
    name = (form.get("name") or [""])[0].strip()
    if not name:
        return "Give the preset a name.", True
    try:
        min_price = _optional_decimal((form.get("min_price") or [""])[0])
        max_price = _optional_decimal((form.get("max_price") or [""])[0])
        min_avg_volume = _optional_int((form.get("min_avg_volume") or [""])[0])
        min_change = _optional_decimal((form.get("min_change") or [""])[0])
        max_change = _optional_decimal((form.get("max_change") or [""])[0])
        min_market_cap = _optional_int((form.get("min_market_cap") or [""])[0])
        max_market_cap = _optional_int((form.get("max_market_cap") or [""])[0])
        lookback_raw = (form.get("lookback_days") or ["20"])[0].strip()
        lookback = int(lookback_raw) if lookback_raw else 20
    except (ValueError, ArithmeticError):
        return "Could not parse those values — check the numbers.", True

    if min_price is not None and max_price is not None and min_price > max_price:
        return "Min price is above max price.", True
    if min_change is not None and max_change is not None and min_change > max_change:
        return "Min % change is above max % change.", True
    if (
        min_market_cap is not None
        and max_market_cap is not None
        and min_market_cap > max_market_cap
    ):
        return "Min market cap is above max market cap.", True
    if lookback < 2:
        return "Lookback must be at least 2 trading days.", True

    sectors = tuple(s for s in (form.get("sector") or []) if s.strip())
    exchanges = tuple(e for e in (form.get("exchange") or []) if e.strip())
    criteria = ScreenCriteria(
        min_price=min_price,
        max_price=max_price,
        min_avg_volume=min_avg_volume,
        min_change_pct=min_change,
        max_change_pct=max_change,
        min_market_cap=min_market_cap,
        max_market_cap=max_market_cap,
        sectors=sectors,
        exchanges=exchanges,
    )
    try:
        preset_id = upsert_preset(name, criteria, lookback, source="manual")
        activate_preset(preset_id)
    except ValueError as exc:
        return str(exc), True
    except Exception:
        return "Could not save the preset.", True
    return f"Preset {name!r} saved and activated.", False


def _activate_screen_preset(form: dict[str, list[str]]) -> tuple[str, bool]:
    preset_id = _preset_id_from_form(form)
    if preset_id is None:
        return "No preset selected.", True
    try:
        activate_preset(preset_id)
    except ValueError as exc:
        return str(exc), True
    except Exception:
        return "Could not activate that preset.", True
    return "Preset activated.", False


def _delete_screen_preset(form: dict[str, list[str]]) -> tuple[str, bool]:
    preset_id = _preset_id_from_form(form)
    if preset_id is None:
        return "No preset selected.", True
    try:
        delete_preset(preset_id)
    except ValueError as exc:
        return str(exc), True
    except Exception:
        return "Could not delete that preset.", True
    return "Preset deleted.", False


@app.get("/api/screen-presets", response_class=HTMLResponse)
def api_screen_presets(request: Request) -> Any:
    return templates.TemplateResponse(
        request, "_screen_presets.html", _screen_presets_context()
    )


@app.post("/api/screen-presets", response_class=HTMLResponse)
async def api_screen_presets_save(request: Request) -> Any:
    form = parse_qs((await request.body()).decode("utf-8"))
    action = (form.get("action") or ["save"])[0]
    if action == "activate":
        notice, error = _activate_screen_preset(form)
    elif action == "delete":
        notice, error = _delete_screen_preset(form)
    else:
        notice, error = _save_screen_preset(form)
    return templates.TemplateResponse(
        request,
        "_screen_presets.html",
        _screen_presets_context(notice=notice, error=error),
    )


# Guards a single screen run at a time — a run is slow (FMP + Alpaca daily
# bars), so it executes in a daemon thread and the lock rejects overlapping
# clicks. In-memory: a dashboard restart clears it, which is fine.
_screen_run_lock = threading.Lock()


def _run_screen_background() -> None:
    """Run a screen with the active preset in a daemon thread, then persist it.

    Outer-ring: every failure is logged and swallowed — a failed run just
    leaves the Latest screen panel showing the previous run. Always releases
    the lock so a later run can start.
    """
    log = get_logger("dashboard.screen_run")
    try:
        criteria, lookback = resolve_screen_criteria()
        # resolve_universe sends the criteria to FMP; the metadata map it
        # returns is empty when FMP is unconfigured OR its call failed.
        symbols, fmp_meta = resolve_universe(
            criteria, max_symbols=500, fallback_limit=500
        )
        run_criteria = criteria
        if not fmp_meta:
            # No FMP metadata -> the engine would reject every candidate for the
            # market-cap / sector / exchange bounds. Drop them and run the
            # price / volume / % change screen over the Alpaca universe.
            run_criteria = replace(
                criteria,
                min_market_cap=None,
                max_market_cap=None,
                sectors=(),
                exchanges=(),
            )
        candidates = build_candidates(symbols, lookback_days=lookback, fmp_meta=fmp_meta)
        result = screen(candidates, run_criteria)
        save_screen(
            result=result,
            universe_size=len(symbols),
            lookback_days=lookback,
            actor="dashboard",
        )
        log.info(
            "dashboard_screen_run_done",
            scanned=result.scanned,
            matched=result.matched,
            fmp_used=bool(fmp_meta),
        )
    except Exception:
        log.exception("dashboard_screen_run_failed")
    finally:
        _screen_run_lock.release()


def _start_screen_run() -> tuple[str, bool]:
    """Kick off a background screen run with the active preset.

    Returns (notice, is_error). Rejects a second run while one is in flight.
    """
    if not _screen_run_lock.acquire(blocking=False):
        return "A screen is already running — results appear shortly.", True
    try:
        threading.Thread(target=_run_screen_background, daemon=True).start()
    except Exception:
        _screen_run_lock.release()
        return "Could not start the screen run.", True
    return "Screen started — the Latest screen panel updates when it finishes.", False


def _run_premarket_background() -> None:
    """Run screen then AI suggest in sequence — the full pre-market precheck.

    Shares _screen_run_lock with standalone screen runs so the two can never
    overlap. Outer-ring: every failure is logged and swallowed; the lock is
    always released so a later run can start.
    """
    _log = get_logger("dashboard.premarket")
    try:
        criteria, lookback = resolve_screen_criteria()
        symbols, fmp_meta = resolve_universe(
            criteria, max_symbols=500, fallback_limit=500
        )
        run_criteria = criteria
        if not fmp_meta:
            run_criteria = replace(
                criteria,
                min_market_cap=None,
                max_market_cap=None,
                sectors=(),
                exchanges=(),
            )
        candidates = build_candidates(
            symbols, lookback_days=lookback, fmp_meta=fmp_meta
        )
        result = screen(candidates, run_criteria)
        screen_run_id = save_screen(
            result=result,
            universe_size=len(symbols),
            lookback_days=lookback,
            actor="dashboard",
        )
        _log.info("premarket_screen_done", matched=result.matched)

        plan = PlanContext()
        suggestion_result = suggest_stocks(result.matches, plan)
        save_suggestions(
            result=suggestion_result,
            screen_run_id=screen_run_id,
            actor="dashboard",
        )
        _log.info(
            "premarket_suggest_done",
            ok=suggestion_result.ok,
            count=suggestion_result.count,
        )
    except Exception:
        _log.exception("dashboard_premarket_failed")
    finally:
        _screen_run_lock.release()


def _start_premarket_run() -> tuple[str, bool]:
    """Kick off a background pre-market run (screen then AI suggest).

    Shares _screen_run_lock with standalone screen runs — the two must not
    overlap since both write screen results. Returns (notice, is_error).
    """
    if not _screen_run_lock.acquire(blocking=False):
        return "A screen or pre-market run is already in progress — results appear shortly.", True
    try:
        threading.Thread(target=_run_premarket_background, daemon=True).start()
    except Exception:
        _screen_run_lock.release()
        return "Could not start the pre-market run.", True
    return "Pre-market check started — panels update when it finishes.", False


@app.post("/api/screen-run", response_class=HTMLResponse)
def api_screen_run(request: Request) -> Any:
    notice, error = _start_screen_run()
    return templates.TemplateResponse(
        request,
        "_screen_presets.html",
        _screen_presets_context(notice=notice, error=error),
    )


# Confidence label -> pill style for the AI-suggestions panel.
_CONFIDENCE_PILLS = {
    "high": "pill-green",
    "medium": "pill-amber",
    "low": "pill-dim",
}


def _suggest_context() -> dict[str, Any]:
    """Render context for the AI-suggestions panel: the latest run + its rows.

    Defensive — the AI suggestion feature is outer-ring and advisory only; a
    DB hiccup degrades the panel to a placeholder rather than 500-ing the page.
    A not-ok run (no API key, nothing to review, an API error) still renders:
    the run's ``notice`` explains why there are no suggestions.
    """
    checking = _screen_run_lock.locked()
    try:
        latest = get_latest_suggestions()
    except Exception:
        return {"run": None, "rows": [], "load_error": True, "checking": checking}
    if latest is None:
        return {"run": None, "rows": [], "load_error": False, "checking": checking}

    run_view = {
        "started_at": latest.started_at.astimezone(ET).strftime("%Y-%m-%d %H:%M ET"),
        "model": latest.model,
        "ok": latest.ok,
        "notice": latest.notice,
    }
    rows = [
        {
            "rank": s.rank,
            "symbol": s.symbol,
            "confidence": s.confidence,
            "confidence_pill": _CONFIDENCE_PILLS.get(s.confidence, "pill-dim"),
            "rationale": s.rationale,
        }
        for s in latest.suggestions
    ]
    return {"run": run_view, "rows": rows, "load_error": False, "checking": checking}


@app.get("/api/suggest", response_class=HTMLResponse)
def api_suggest(request: Request) -> Any:
    return templates.TemplateResponse(request, "_suggest.html", _suggest_context())


@app.post("/api/premarket/run", response_class=HTMLResponse)
def api_premarket_run(request: Request) -> Any:
    notice, error = _start_premarket_run()
    ctx = _suggest_context()
    ctx["notice"] = notice
    ctx["notice_error"] = error
    return templates.TemplateResponse(request, "_suggest.html", ctx)


def _quote_row(symbol: str, q: QuoteView | None) -> dict[str, Any]:
    """One symbol's display row for a watchlist quote table."""
    return {
        "symbol": symbol,
        "last": _fmt_money(q.last) if q else "—",
        "change_pct": _fmt_pct(q.change_pct) if q and q.change_pct is not None else "—",
        "bid_ask": f"{q.bid:.2f} / {q.ask:.2f}" if q and q.bid and q.ask else "—",
        "volume": _fmt_volume(int(q.volume)) if q and q.volume is not None else "—",
        "positive": bool(q and q.change_pct is not None and q.change_pct >= 0),
        "has_quote": bool(q and q.change_pct is not None),
    }


def _watchlist_context(notice: str | None = None, error: bool = False) -> dict[str, Any]:
    """Render context for the watchlist panel: every named watchlist with live
    quotes, plus management state.

    Defensive — outer-ring; a DB hiccup degrades the panel to a placeholder.
    """
    base: dict[str, Any] = {
        "notice": notice,
        "notice_error": error,
        "fallback_csv": ", ".join(WATCHLIST),
    }
    try:
        records = list_watchlists()
    except Exception:
        return {**base, "load_error": True, "watchlists": [], "has_active": False}

    # One snapshot call for the union of every list's symbols. get_quotes is
    # outer-ring and never raises — a quote outage degrades rows to "—".
    union: list[str] = []
    seen: set[str] = set()
    for r in records:
        for sym in r.symbols:
            if sym not in seen:
                seen.add(sym)
                union.append(sym)
    quotes = get_quotes(union)

    watchlists = [
        {
            "id": str(r.id),
            "name": r.name,
            "is_active": r.is_active,
            "source": r.source,
            "count": len(r.symbols),
            "symbols_csv": ", ".join(r.symbols),
            "updated_at": r.updated_at.astimezone(ET).strftime("%Y-%m-%d %H:%M ET"),
            "rows": [_quote_row(sym, quotes.get(sym)) for sym in r.symbols],
        }
        for r in records
    ]
    return {
        **base,
        "load_error": False,
        "watchlists": watchlists,
        "has_active": any(r.is_active for r in records),
    }


def _watchlist_id_from_form(form: dict[str, list[str]]) -> uuid.UUID | None:
    raw = (form.get("watchlist_id") or [""])[0].strip()
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _parse_symbol_list(raw: str) -> list[str]:
    """Split a free-text symbol field on commas or whitespace."""
    return [tok for tok in raw.replace(",", " ").split() if tok]


def _do_watchlist_action(action: str, form: dict[str, list[str]]) -> tuple[str, bool]:
    """Execute one watchlist management action. Returns (notice, is_error)."""
    if action == "create":
        name = (form.get("name") or [""])[0].strip()
        if not name:
            return "Give the watchlist a name.", True
        try:
            create_watchlist(name, source="manual")
        except ValueError as exc:
            return str(exc), True
        except Exception:
            return "Could not create the watchlist.", True
        return f"Watchlist {name!r} created.", False

    wid = _watchlist_id_from_form(form)
    if wid is None:
        return "No watchlist selected.", True

    if action == "activate":
        try:
            activate_watchlist(wid)
        except ValueError as exc:
            return str(exc), True
        except Exception:
            return "Could not activate that watchlist.", True
        return "Watchlist activated — runners will use it.", False

    if action == "delete":
        try:
            delete_watchlist(wid)
        except ValueError as exc:
            return str(exc), True
        except Exception:
            return "Could not delete that watchlist.", True
        return "Watchlist deleted.", False

    if action == "rename":
        try:
            rename_watchlist(wid, (form.get("name") or [""])[0])
        except ValueError as exc:
            return str(exc), True
        except Exception:
            return "Could not rename the watchlist.", True
        return "Watchlist renamed.", False

    if action == "add_symbols":
        symbols = _parse_symbol_list((form.get("symbols") or [""])[0])
        if not symbols:
            return "Enter at least one symbol to add.", True
        try:
            added = add_symbols(wid, symbols)
        except ValueError as exc:
            return str(exc), True
        except Exception:
            return "Could not add symbols.", True
        if not added:
            return "Those symbols are already on the list.", False
        return f"Added {len(added)} symbol(s): {', '.join(added)}.", False

    if action == "set_symbols":
        symbols = _parse_symbol_list((form.get("symbols") or [""])[0])
        try:
            set_watchlist_symbols(wid, symbols)
        except ValueError as exc:
            return str(exc), True
        except Exception:
            return "Could not update symbols.", True
        return f"Watchlist replaced with {len(symbols)} symbol(s).", False

    if action == "remove_symbol":
        symbol = (form.get("symbol") or [""])[0].strip().upper()
        if not symbol:
            return "No symbol given.", True
        try:
            remove_symbol(wid, symbol)
        except ValueError as exc:
            return str(exc), True
        except Exception:
            return "Could not remove that symbol.", True
        return f"Removed {symbol}.", False

    return "Unknown watchlist action.", True


@app.get("/api/watchlist", response_class=HTMLResponse)
def api_watchlist(request: Request) -> Any:
    return templates.TemplateResponse(request, "_watchlist.html", _watchlist_context())


@app.post("/api/watchlist", response_class=HTMLResponse)
async def api_watchlist_save(request: Request) -> Any:
    form = parse_qs((await request.body()).decode("utf-8"))
    action = (form.get("action") or [""])[0]
    notice, error = _do_watchlist_action(action, form)
    return templates.TemplateResponse(
        request, "_watchlist.html", _watchlist_context(notice=notice, error=error)
    )


def _manage_context(notice: str | None = None, error: bool = False) -> dict[str, Any]:
    """Context for the manual-control panel: open symbols + a notice.

    Defensive — outer-ring; an Alpaca hiccup degrades to an empty symbol hint
    rather than 500-ing the page.
    """
    open_symbols: list[str] = []
    with contextlib.suppress(Exception):
        open_symbols = [p.symbol for p in list_positions()]
    return {"notice": notice, "notice_error": error, "open_symbols": open_symbols}


def _do_manage_action(action: str, form: dict[str, list[str]]) -> tuple[str, bool]:
    """Execute one manual-control action. Returns (notice, is_error).

    The broker is constructed lazily and defensively — if credentials are
    missing the panel reports it rather than crashing.
    """
    symbol = (form.get("symbol") or [""])[0].strip().upper()
    if action == "stop":
        # Adjusting the live managed stop is a DB write — no broker needed.
        stop_raw = (form.get("stop_price") or [""])[0].strip()
        if not symbol:
            return "Symbol is required.", True
        try:
            new_stop = Decimal(stop_raw)
            if new_stop <= 0:
                return "Stop price must be positive.", True
        except (ValueError, ArithmeticError):
            return "Could not parse the stop price.", True
        try:
            from trident.persistence import managed_position

            managed_position.update_stop(symbol, new_stop)
        except Exception:
            return f"Failed to update the stop for {symbol}.", True
        return f"Stop for {symbol} set to {_plain_decimal(new_stop)}.", False

    # close / cancel both need the broker.
    try:
        from trident.execution.alpaca import AlpacaBroker

        broker = AlpacaBroker()
    except Exception:
        return "Broker unavailable — check Alpaca credentials.", True

    if action == "close":
        if not symbol:
            return "Symbol is required.", True
        try:
            broker.close_position(symbol)
            from trident.persistence import managed_position

            managed_position.remove(symbol)
        except Exception:
            return f"Failed to close {symbol}.", True
        return f"Close order submitted for {symbol}.", False

    if action == "cancel":
        order_id = (form.get("order_id") or [""])[0].strip()
        if not order_id:
            return "Order id is required.", True
        try:
            broker.cancel_order(order_id)
        except Exception:
            return "Failed to cancel that order.", True
        return f"Cancel submitted for order {order_id}.", False

    return f"Unknown action {action!r}.", True


def _fmt_duration(seconds: int) -> str:
    """Compact holding-period label: '1h 30m' or '12m'."""
    minutes = seconds // 60
    if minutes >= 60:
        return f"{minutes // 60}h {minutes % 60}m"
    return f"{minutes}m"


def _pnl_context() -> dict[str, Any]:
    """Context for the per-trade P&L panel: recent closed live trades + a
    summary. Defensive — outer-ring; a DB hiccup degrades to a placeholder.
    """
    try:
        with session_scope() as s:
            trades = list(
                s.scalars(
                    select(LiveTrade).order_by(LiveTrade.entry_ts.desc()).limit(50)
                )
            )
            rows = []
            total_net = total_gross = total_fees = Decimal("0")
            wins = losses = washes = 0
            for t in trades:
                total_net += t.net_pnl
                total_gross += t.gross_pnl
                total_fees += t.fees
                if t.net_pnl > 0:
                    wins += 1
                elif t.net_pnl < 0:
                    losses += 1
                if t.wash_sale:
                    washes += 1
                rows.append(
                    {
                        "date": t.entry_ts.astimezone(ET).strftime("%Y-%m-%d"),
                        "time": t.entry_ts.astimezone(ET).strftime("%H:%M"),
                        "symbol": t.symbol,
                        "side": t.side,
                        "qty": t.qty,
                        "entry": _fmt_money(t.entry_price),
                        "exit": _fmt_money(t.exit_price),
                        "net": _fmt_money(t.net_pnl),
                        "net_positive": t.net_pnl >= 0,
                        "r": f"{t.r_multiple:+.2f}" if t.r_multiple is not None else "—",
                        "hold": _fmt_duration(t.holding_period_seconds),
                        "wash": t.wash_sale,
                    }
                )
            n = len(trades)
            summary = (
                {
                    "count": n,
                    "wins": wins,
                    "losses": losses,
                    "win_rate": f"{wins / n * 100:.1f}%" if n else "—",
                    "total_net": _fmt_money(total_net),
                    "total_net_positive": total_net >= 0,
                    "total_gross": _fmt_money(total_gross),
                    "total_fees": _fmt_money(total_fees),
                    "washes": washes,
                }
                if n
                else None
            )
    except Exception:
        return {"rows": [], "summary": None, "load_error": True}
    return {"rows": rows, "summary": summary, "load_error": False}


@app.get("/api/pnl", response_class=HTMLResponse)
def api_pnl(request: Request) -> Any:
    return templates.TemplateResponse(request, "_pnl.html", _pnl_context())


@app.get("/api/manage", response_class=HTMLResponse)
def api_manage(request: Request) -> Any:
    return templates.TemplateResponse(request, "_manage.html", _manage_context())


@app.post("/api/manage", response_class=HTMLResponse)
async def api_manage_action(request: Request) -> Any:
    form = parse_qs((await request.body()).decode("utf-8"))
    action = (form.get("action") or ["close"])[0]
    notice, error = _do_manage_action(action, form)
    return templates.TemplateResponse(
        request, "_manage.html", _manage_context(notice=notice, error=error)
    )


# ---------------------------------------------------------------------------
# Phase 1 — Symbol performance panel
# ---------------------------------------------------------------------------

def _symbol_perf_context() -> dict[str, Any]:
    """Per-symbol aggregated stats from ReplayTrade + LiveTrade.

    Outer-ring: a DB hiccup degrades to a placeholder rather than 500-ing.
    Queries every ReplayTrade ever stored (all runs), giving the largest
    available sample. LiveTrade rows supplement with real paper-trade results.
    """
    try:
        with session_scope() as s:
            # Replay aggregates across all runs, grouped by symbol
            replay_rows = s.execute(
                select(
                    ReplayTrade.symbol,
                    func.count().label("trades"),
                    func.sum(case((ReplayTrade.pnl > 0, 1), else_=0)).label("wins"),
                    func.sum(case((ReplayTrade.pnl <= 0, 1), else_=0)).label("losses"),
                    func.avg(ReplayTrade.r_multiple).label("avg_r"),
                    func.sum(ReplayTrade.pnl).label("total_pnl"),
                    func.max(ReplayTrade.trade_date).label("last_traded"),
                )
                .group_by(ReplayTrade.symbol)
                .order_by(func.sum(ReplayTrade.pnl).desc())
            ).all()

            # Live trades (real paper round-trips)
            live_rows = s.execute(
                select(
                    LiveTrade.symbol,
                    func.count().label("trades"),
                    func.sum(case((LiveTrade.net_pnl > 0, 1), else_=0)).label("wins"),
                    func.sum(case((LiveTrade.net_pnl <= 0, 1), else_=0)).label("losses"),
                    func.avg(LiveTrade.r_multiple).label("avg_r"),
                    func.sum(LiveTrade.net_pnl).label("total_pnl"),
                    func.max(LiveTrade.entry_ts).label("last_traded"),
                )
                .group_by(LiveTrade.symbol)
            ).all()

        live_by_sym: dict[str, Any] = {r.symbol: r for r in live_rows}

        rows: list[dict[str, Any]] = []
        for r in replay_rows:
            win_rate = (float(r.wins) / float(r.trades) * 100.0) if r.trades else 0.0
            live = live_by_sym.get(r.symbol)
            live_view = None
            if live and live.trades:
                lr = float(live.wins) / float(live.trades) * 100.0
                live_view = {
                    "trades": live.trades,
                    "win_rate": f"{lr:.0f}%",
                    "total_pnl": _fmt_money(Decimal(str(live.total_pnl))),
                    "total_pnl_positive": float(live.total_pnl) >= 0,
                }
            rows.append(
                {
                    "symbol": r.symbol,
                    "trades": r.trades,
                    "wins": r.wins,
                    "losses": r.losses,
                    "win_rate": f"{win_rate:.0f}%",
                    "win_rate_positive": win_rate >= 50,
                    "avg_r": f"{float(r.avg_r):+.2f}" if r.avg_r is not None else "—",
                    "avg_r_positive": float(r.avg_r or 0) >= 0,
                    "total_pnl": _fmt_money(Decimal(str(r.total_pnl))),
                    "total_pnl_positive": float(r.total_pnl) >= 0,
                    "last_traded": (
                        r.last_traded.astimezone(ET).strftime("%Y-%m-%d")
                        if r.last_traded
                        else "—"
                    ),
                    "live": live_view,
                }
            )
    except Exception:
        return {"rows": [], "load_error": True}
    return {"rows": rows, "load_error": False}


@app.get("/api/symbol-perf", response_class=HTMLResponse)
def api_symbol_perf(request: Request) -> Any:
    return templates.TemplateResponse(request, "_symbol_perf.html", _symbol_perf_context())


# ---------------------------------------------------------------------------
# Phase 2 — Backtest trigger + history UI
# ---------------------------------------------------------------------------

_backtest_run_lock = threading.Lock()


def _run_backtest_background(days: int, strategy: str, window_days: int) -> None:
    """Run scripts/backtest.py as a subprocess so it persists results to the DB.

    Always releases the lock when done. Outer-ring: a failure is logged and
    swallowed — the history panel just keeps showing the previous results.
    """
    log = get_logger("dashboard.backtest_run")
    try:
        env = dict(os.environ)
        src_path = str(_PROJECT_ROOT / "src")
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{src_path}:{existing}" if existing else src_path

        proc = subprocess.run(
            [
                sys.executable,
                "scripts/backtest.py",
                "--days",
                str(days),
                "--strategy",
                strategy,
                "--window-days",
                str(window_days),
            ],
            cwd=str(_PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0:
            log.warning(
                "backtest_subprocess_failed",
                returncode=proc.returncode,
                stderr=proc.stderr[:500],
            )
        else:
            log.info(
                "backtest_subprocess_done", days=days, strategy=strategy
            )
    except Exception:
        log.exception("backtest_subprocess_error")
    finally:
        _backtest_run_lock.release()


def _start_backtest_run(days: int, strategy: str, window_days: int) -> tuple[str, bool]:
    """Kick off a background backtest run. Returns (notice, is_error)."""
    if not _backtest_run_lock.acquire(blocking=False):
        return "A backtest is already running — results appear when it finishes.", True
    try:
        threading.Thread(
            target=_run_backtest_background,
            args=(days, strategy, window_days),
            daemon=True,
        ).start()
    except Exception:
        _backtest_run_lock.release()
        return "Could not start the backtest.", True
    return (
        f"Backtest started ({days}d · {strategy} · {window_days}d windows) — "
        "the history panel updates when it finishes.",
        False,
    )


def _backtest_history_context(
    notice: str | None = None, error: bool = False
) -> dict[str, Any]:
    """All ReplayRun rows ordered newest-first, plus the run trigger form.

    Outer-ring: a DB hiccup degrades to a placeholder.
    """
    base: dict[str, Any] = {
        "notice": notice,
        "notice_error": error,
        "running": _backtest_run_lock.locked(),
        "strategies": available_strategies(),
    }
    try:
        with session_scope() as s:
            runs = list(
                s.scalars(
                    select(ReplayRun).order_by(ReplayRun.started_at.desc()).limit(50)
                )
            )
        rows = [
            {
                "id": str(run.id),
                "started_at": run.started_at.astimezone(ET).strftime("%Y-%m-%d %H:%M ET"),
                "window": (
                    f"{run.first_day.strftime('%b %d')} → "
                    f"{run.last_day.strftime('%b %d')}"
                ),
                "days": run.days,
                "strategy": run.strategy,
                "num_trades": run.num_trades,
                "wins": run.wins,
                "losses": run.losses,
                "win_rate": (
                    f"{float(run.wins) / float(run.num_trades) * 100.0:.1f}%"
                    if run.num_trades
                    else "—"
                ),
                "avg_r": f"{run.avg_r:+.2f}",
                "avg_r_positive": run.avg_r >= 0,
                "total_pnl": _fmt_money(run.total_pnl),
                "total_pnl_positive": run.total_pnl >= 0,
                "mode": run.mode,
                "is_honest": run.mode == "honest",
            }
            for run in runs
        ]
    except Exception:
        return {**base, "rows": [], "load_error": True}
    return {**base, "rows": rows, "load_error": False}


@app.get("/api/backtest-history", response_class=HTMLResponse)
def api_backtest_history(request: Request) -> Any:
    return templates.TemplateResponse(
        request, "_backtest_history.html", _backtest_history_context()
    )


@app.post("/api/backtest-run", response_class=HTMLResponse)
async def api_backtest_run(request: Request) -> Any:
    form = parse_qs((await request.body()).decode("utf-8"))
    days_raw = (form.get("days") or ["30"])[0].strip()
    strategy = (form.get("strategy") or ["orb_5m"])[0].strip()
    window_raw = (form.get("window_days") or ["5"])[0].strip()
    try:
        days = int(days_raw)
        window_days = int(window_raw)
        if days < 1 or days > 500:
            raise ValueError("days out of range")
        if window_days < 1 or window_days > 90:
            raise ValueError("window_days out of range")
        if strategy not in available_strategies():
            raise ValueError("unknown strategy")
    except (ValueError, ArithmeticError) as exc:
        return templates.TemplateResponse(
            request,
            "_backtest_history.html",
            _backtest_history_context(notice=str(exc), error=True),
        )
    notice, err = _start_backtest_run(days, strategy, window_days)
    return templates.TemplateResponse(
        request, "_backtest_history.html", _backtest_history_context(notice=notice, error=err)
    )


# ---------------------------------------------------------------------------
# Phase 3 — Signal history + outcome linking
# ---------------------------------------------------------------------------

def _parse_signal_date(date_str: str | None) -> date:
    """Parse YYYY-MM-DD string to a date, defaulting to today (ET)."""
    if date_str:
        try:
            return date.fromisoformat(date_str.strip())
        except ValueError:
            pass
    return now_et().date()


def _signal_history_context(date_str: str | None = None) -> dict[str, Any]:
    """Signals for a given date with their entry-order outcomes.

    Outer-ring: a DB hiccup degrades to a placeholder.
    """
    target_date = _parse_signal_date(date_str)
    cutoff = datetime.combine(target_date, datetime.min.time(), tzinfo=ET)
    next_day = cutoff + timedelta(days=1)
    try:
        with session_scope() as s:
            signals = list(
                s.scalars(
                    select(Signal)
                    .where(Signal.ts >= cutoff)
                    .where(Signal.ts < next_day)
                    .order_by(Signal.ts.desc())
                    .limit(100)
                )
            )
            # Map signal_id → entry order (parent order, no parent_order_id)
            sig_ids = [sig.id for sig in signals]
            entry_orders: dict[uuid.UUID, OrderModel] = {}
            if sig_ids:
                orders = list(
                    s.scalars(
                        select(OrderModel)
                        .where(OrderModel.signal_id.in_(sig_ids))
                        .where(OrderModel.parent_order_id.is_(None))
                    )
                )
                entry_orders = {o.signal_id: o for o in orders if o.signal_id}

        rows = []
        for sig in signals:
            order = entry_orders.get(sig.id)
            order_view: dict[str, Any] | None = None
            if order:
                order_view = {
                    "state": order.state,
                    "fill_price": (
                        _fmt_money(order.avg_fill_price) if order.avg_fill_price else "—"
                    ),
                    "filled_at": (
                        order.filled_at.astimezone(ET).strftime("%H:%M:%S")
                        if order.filled_at
                        else "—"
                    ),
                    "is_filled": order.state == "filled",
                    "is_rejected": order.state in {"rejected", "canceled", "cancelled", "expired"},
                    "is_pending": order.state not in {
                        "filled", "rejected", "canceled", "cancelled", "expired"
                    },
                }
            rows.append(
                {
                    "ts": sig.ts.astimezone(ET).strftime("%H:%M:%S"),
                    "symbol": sig.symbol,
                    "side": sig.side,
                    "strategy": sig.strategy,
                    "entry": _fmt_money(sig.entry_price),
                    "stop": _fmt_money(sig.stop_price),
                    "target": _fmt_money(sig.target_price),
                    "decision": sig.gate_decision or "—",
                    "reason": sig.gate_reason or "—",
                    "approved": sig.gate_decision == "approved",
                    "order": order_view,
                }
            )
    except Exception:
        return {
            "rows": [],
            "load_error": True,
            "selected_date": target_date.isoformat(),
        }
    return {
        "rows": rows,
        "load_error": False,
        "selected_date": target_date.isoformat(),
    }


@app.get("/api/signal-history", response_class=HTMLResponse)
def api_signal_history(request: Request, date: str | None = None) -> Any:
    return templates.TemplateResponse(
        request, "_signal_history.html", _signal_history_context(date)
    )


# ---------------------------------------------------------------------------
# Phase 4 — Screener → performance feedback
# ---------------------------------------------------------------------------

def _screen_perf_context() -> dict[str, Any]:
    """Per-symbol replay stats overlaid on the latest screen's matches.

    For each symbol in the most recent screen run, shows how it performed
    in backtests (if any trades exist). Also shows the overall backtest
    average so the reader can compare screener-sourced symbols vs. the field.
    Outer-ring: a DB hiccup degrades to a placeholder.
    """
    try:
        latest = get_latest_screen()
    except Exception:
        return {"rows": [], "summary": None, "load_error": True}
    if latest is None or not latest.matches:
        return {"rows": [], "summary": None, "load_error": False}

    screen_symbols = [c.symbol for c in latest.matches]

    try:
        with session_scope() as s:
            # Stats for all symbols ever backtested
            all_stats = {
                r.symbol: r
                for r in s.execute(
                    select(
                        ReplayTrade.symbol,
                        func.count().label("trades"),
                        func.sum(case((ReplayTrade.pnl > 0, 1), else_=0)).label("wins"),
                        func.avg(ReplayTrade.r_multiple).label("avg_r"),
                        func.sum(ReplayTrade.pnl).label("total_pnl"),
                    )
                    .group_by(ReplayTrade.symbol)
                ).all()
            }

            # Overall backtest summary (for comparison baseline)
            overall = s.execute(
                select(
                    func.count().label("trades"),
                    func.sum(case((ReplayTrade.pnl > 0, 1), else_=0)).label("wins"),
                    func.avg(ReplayTrade.r_multiple).label("avg_r"),
                    func.sum(ReplayTrade.pnl).label("total_pnl"),
                )
            ).one()
    except Exception:
        return {"rows": [], "summary": None, "load_error": True}

    rows: list[dict[str, Any]] = []
    for sym in screen_symbols:
        st = all_stats.get(sym)
        if st and st.trades:
            win_rate = float(st.wins) / float(st.trades) * 100.0
            rows.append(
                {
                    "symbol": sym,
                    "trades": st.trades,
                    "win_rate": f"{win_rate:.0f}%",
                    "win_rate_positive": win_rate >= 50,
                    "avg_r": f"{float(st.avg_r):+.2f}" if st.avg_r is not None else "—",
                    "avg_r_positive": float(st.avg_r or 0) >= 0,
                    "total_pnl": _fmt_money(Decimal(str(st.total_pnl))),
                    "total_pnl_positive": float(st.total_pnl) >= 0,
                    "has_data": True,
                }
            )
        else:
            rows.append({"symbol": sym, "has_data": False})

    # Overall baseline
    summary = None
    if overall.trades:
        ov_wr = float(overall.wins) / float(overall.trades) * 100.0
        summary = {
            "trades": overall.trades,
            "win_rate": f"{ov_wr:.1f}%",
            "avg_r": f"{float(overall.avg_r):+.2f}" if overall.avg_r is not None else "—",
            "total_pnl": _fmt_money(Decimal(str(overall.total_pnl))),
            "total_pnl_positive": float(overall.total_pnl or 0) >= 0,
        }

    return {"rows": rows, "summary": summary, "load_error": False}


@app.get("/api/screen-perf", response_class=HTMLResponse)
def api_screen_perf(request: Request) -> Any:
    return templates.TemplateResponse(request, "_screen_perf.html", _screen_perf_context())


# ---------------------------------------------------------------------------
# Settings panel
# ---------------------------------------------------------------------------

def _key_status(val: str) -> dict[str, object]:
    """Produce display metadata for a secret key without exposing the value."""
    configured = bool(val and val.strip())
    hint = f"…{val[-4:]}" if configured and len(val) >= 4 else ""
    return {"configured": configured, "hint": hint}


def _db_display(url: str) -> str:
    """Return host:port/dbname from a database URL, hiding credentials."""
    try:
        p = urlparse(url)
        host = p.hostname or "?"
        port = f":{p.port}" if p.port else ""
        db = p.path.lstrip("/") or "?"
        return f"{host}{port}/{db}"
    except Exception:
        return "configured"


def _settings_context(notice: str | None = None, error: bool = False) -> dict[str, Any]:
    s = get_settings()
    return {
        "notice": notice,
        "notice_error": error,
        # API key statuses only — values never reach the template
        "alpaca_key": _key_status(s.alpaca_api_key),
        "alpaca_secret": _key_status(s.alpaca_api_secret),
        "anthropic_key": _key_status(s.anthropic_api_key),
        "fmp_key": _key_status(s.fmp_api_key),
        # Non-secret connection fields
        "alpaca_base_url": s.alpaca_base_url,
        "alpaca_data_feed": s.alpaca_data_feed,
        "is_paper": s.is_paper,
        # Risk
        "risk_per_trade_pct": _plain_decimal(s.risk_per_trade_pct),
        "daily_loss_limit_pct": _plain_decimal(s.daily_loss_limit_pct),
        "max_concurrent_positions": str(s.max_concurrent_positions),
        "account_equity_override": _plain_decimal(s.account_equity_override) if s.account_equity_override else "",
        # Operational
        "environment": s.environment,
        "log_level": s.log_level,
        "default_strategy": s.default_strategy,
        "available_strategies": available_strategies(),
        # Backtest costs
        "backtest_slippage_bps": _plain_decimal(s.backtest_slippage_bps),
        "backtest_fee_per_share": _plain_decimal(s.backtest_fee_per_share),
        "backtest_min_fee": _plain_decimal(s.backtest_min_fee),
        "backtest_sec_fee_rate": _plain_decimal(s.backtest_sec_fee_rate),
        "backtest_taf_per_share": _plain_decimal(s.backtest_taf_per_share),
        # DB — read-only display
        "db_display": _db_display(s.database_url),
    }


_RESTART_KEYS = frozenset({
    "ALPACA_API_KEY", "ALPACA_API_SECRET", "ALPACA_BASE_URL", "ALPACA_DATA_FEED",
    "ANTHROPIC_API_KEY", "FMP_API_KEY", "ENVIRONMENT", "LOG_LEVEL",
})


def _save_settings(form: dict[str, list[str]]) -> tuple[str, bool]:
    """Parse, validate and persist settings to .env. Returns (notice, is_error)."""
    from decimal import InvalidOperation

    updates: dict[str, str] = {}

    # API keys — only write if the user supplied a non-blank value
    for env_key, form_key in (
        ("ALPACA_API_KEY", "alpaca_api_key"),
        ("ALPACA_API_SECRET", "alpaca_api_secret"),
        ("ANTHROPIC_API_KEY", "anthropic_api_key"),
        ("FMP_API_KEY", "fmp_api_key"),
    ):
        val = (form.get(form_key) or [""])[0].strip()
        if val:
            updates[env_key] = val

    base_url = (form.get("alpaca_base_url") or [""])[0].strip()
    if base_url:
        updates["ALPACA_BASE_URL"] = base_url

    feed = (form.get("alpaca_data_feed") or [""])[0].strip()
    if feed in ("iex", "sip"):
        updates["ALPACA_DATA_FEED"] = feed

    # Risk params — validate then write
    try:
        rpt_raw = (form.get("risk_per_trade_pct") or [""])[0].strip()
        dll_raw = (form.get("daily_loss_limit_pct") or [""])[0].strip()
        mcp_raw = (form.get("max_concurrent_positions") or [""])[0].strip()
        aeo_raw = (form.get("account_equity_override") or [""])[0].strip()

        if rpt_raw:
            v = Decimal(rpt_raw)
            if not (Decimal("0") < v <= Decimal("100")):
                return "Risk per trade must be between 0 and 100.", True
            updates["RISK_PER_TRADE_PCT"] = rpt_raw
        if dll_raw:
            v = Decimal(dll_raw)
            if not (Decimal("0") < v <= Decimal("100")):
                return "Daily loss limit must be between 0 and 100.", True
            updates["DAILY_LOSS_LIMIT_PCT"] = dll_raw
        if mcp_raw:
            v_int = int(mcp_raw)
            if v_int < 1:
                return "Max concurrent positions must be at least 1.", True
            updates["MAX_CONCURRENT_POSITIONS"] = mcp_raw
        # blank equity override means "clear it"
        updates["ACCOUNT_EQUITY_OVERRIDE"] = aeo_raw
    except (ValueError, InvalidOperation):
        return "Could not parse risk parameters — check the numbers.", True

    # Operational
    env = (form.get("environment") or [""])[0].strip()
    if env in ("development", "paper"):
        updates["ENVIRONMENT"] = env

    log_level = (form.get("log_level") or [""])[0].strip()
    if log_level in ("DEBUG", "INFO", "WARNING", "ERROR"):
        updates["LOG_LEVEL"] = log_level

    strategy = (form.get("default_strategy") or [""])[0].strip()
    if strategy:
        updates["DEFAULT_STRATEGY"] = strategy

    # Backtest costs
    try:
        for env_key, form_key in (
            ("BACKTEST_SLIPPAGE_BPS", "backtest_slippage_bps"),
            ("BACKTEST_FEE_PER_SHARE", "backtest_fee_per_share"),
            ("BACKTEST_MIN_FEE", "backtest_min_fee"),
            ("BACKTEST_SEC_FEE_RATE", "backtest_sec_fee_rate"),
            ("BACKTEST_TAF_PER_SHARE", "backtest_taf_per_share"),
        ):
            val = (form.get(form_key) or [""])[0].strip()
            if val:
                v = Decimal(val)
                if v < 0:
                    return f"{form_key.replace('_', ' ').title()} cannot be negative.", True
                updates[env_key] = val
    except (ValueError, InvalidOperation):
        return "Could not parse backtest cost parameters — check the numbers.", True

    try:
        rewrite_env(updates)
    except Exception:
        return "Could not write to .env — check file permissions.", True

    # Invalidate the cached settings so the panel re-renders with fresh values
    get_settings.cache_clear()

    needs_restart = bool(updates.keys() & _RESTART_KEYS)
    if needs_restart:
        return "Settings saved — restart runners and the dashboard to apply API / operational changes.", False
    return "Settings saved.", False


@app.get("/api/settings", response_class=HTMLResponse)
def api_settings(request: Request) -> Any:
    return templates.TemplateResponse(request, "_settings.html", _settings_context())


@app.post("/api/settings", response_class=HTMLResponse)
async def api_settings_save(request: Request) -> Any:
    form = parse_qs((await request.body()).decode("utf-8"))
    notice, error = _save_settings(form)
    return templates.TemplateResponse(
        request, "_settings.html", _settings_context(notice=notice, error=error)
    )


# ---------------------------------------------------------------------------
# Connection health panel
# ---------------------------------------------------------------------------

def _health_context() -> dict[str, Any]:
    """Check live connectivity to Alpaca, FMP, and the database.

    Outer-ring: every check is isolated so one failure doesn't block others.
    """
    s = get_settings()

    # ── Alpaca ──────────────────────────────────────────────────────────────
    if not s.alpaca_api_key or not s.alpaca_api_secret:
        alpaca: dict[str, str] = {"status": "unconfigured", "detail": "API key / secret not set"}
    else:
        account = get_account()
        if account is not None:
            alpaca = {
                "status": "ok",
                "detail": f"account {account.status} · equity {_fmt_money(account.equity)}",
            }
        else:
            alpaca = {"status": "error", "detail": "authentication failed or unreachable"}

    # ── FMP ─────────────────────────────────────────────────────────────────
    if is_configured():
        fmp: dict[str, str] = {"status": "ok", "detail": "key configured"}
    else:
        fmp = {"status": "unconfigured", "detail": "key not set — market-cap / sector filters disabled"}

    # ── Database ─────────────────────────────────────────────────────────────
    try:
        with session_scope() as db_s:
            db_s.execute(text("SELECT 1"))
        db: dict[str, str] = {"status": "ok", "detail": "connected"}
    except Exception as exc:
        db = {"status": "error", "detail": str(exc)[:120]}

    return {"alpaca": alpaca, "fmp": fmp, "db": db}


@app.get("/api/health", response_class=HTMLResponse)
def api_health(request: Request) -> Any:
    return templates.TemplateResponse(request, "_health.html", _health_context())


# ---------------------------------------------------------------------------
# Log tail panel
# ---------------------------------------------------------------------------

def _parse_log_line(raw: str) -> dict[str, str]:
    """Return a display dict for one log line, parsing JSON where possible."""
    raw = raw.strip()
    try:
        obj = json.loads(raw)
        ts = str(obj.get("timestamp", ""))[:19].replace("T", " ")
        level = str(obj.get("level", "")).lower()
        event = str(obj.get("event", raw))
        # Include the most useful extra fields without overwhelming the display
        extras = {
            k: obj[k]
            for k in ("symbol", "strategy", "reason", "error", "matched", "count")
            if k in obj and obj[k] is not None
        }
        detail = "  ".join(f"{k}={v}" for k, v in extras.items())
        return {"ts": ts, "level": level, "event": event, "detail": detail, "is_json": "1"}
    except (json.JSONDecodeError, ValueError):
        return {"ts": "", "level": "", "event": raw, "detail": "", "is_json": ""}


def _tail_file(path: Path, n: int) -> list[dict[str, str]]:
    content = path.read_text(encoding="utf-8", errors="replace")
    return [_parse_log_line(ln) for ln in content.splitlines()[-n:] if ln.strip()]


def _log_context(selected: str | None = None) -> dict[str, Any]:
    log_dir = get_settings().log_dir.resolve()
    try:
        files = sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
    except Exception:
        return {"files": [], "selected": None, "lines": [], "load_error": True}

    file_names = [f.name for f in files]
    if not file_names:
        return {"files": [], "selected": None, "lines": [], "load_error": False}

    # Use the requested file if valid, else the most-recently-modified one
    target_name = selected if selected and selected in file_names else file_names[0]

    try:
        lines = _tail_file(log_dir / target_name, 50)
    except Exception:
        lines = []

    return {"files": file_names, "selected": target_name, "lines": lines, "load_error": False}


@app.get("/api/logs", response_class=HTMLResponse)
def api_logs(request: Request, file: str | None = None) -> Any:
    return templates.TemplateResponse(request, "_logs.html", _log_context(selected=file))
