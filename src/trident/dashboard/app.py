"""Dashboard for the personal trading bot.

A small FastAPI app with HTMX panels that poll JSON endpoints every few seconds.
Designed to be opened on localhost only — no auth, no CORS exposure. If you want
to access it remotely, put it behind Tailscale or an SSH tunnel.

Run with:
    PYTHONPATH=src uvicorn trident.dashboard.app:app --host 127.0.0.1 --port 8765 --reload
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from trident.audit.log import configure_logging
from trident.clock import ET, is_market_open, now_et, nth_business_day_back
from trident.dashboard.alpaca_view import get_account, list_positions
from trident.persistence.daily_plan import (
    day_trades_in_window,
    get_for_day,
    notional_deployed_today,
    upsert,
)
from trident.persistence.models import (
    AuditEvent,
    ReplayRun,
    ReplayTrade,
    Signal,
)
from trident.persistence.models import (
    Order as OrderModel,
)
from trident.persistence.session import session_scope
from trident.persistence.state import (
    kill_switch_engaged,
    last_heartbeat,
    set_kill_switch,
)
from trident.screener.persistence import get_latest_screen

TEMPLATES_DIR = Path(__file__).parent / "templates"

configure_logging()
app = FastAPI(title="Trident Trader")
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
    return templates.TemplateResponse(request, "index.html", {})


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


@app.post("/api/kill", response_class=JSONResponse)
def api_kill_engage() -> Any:
    set_kill_switch(True, actor="dashboard")
    return {"engaged": True}


@app.post("/api/kill/release", response_class=JSONResponse)
def api_kill_release() -> Any:
    set_kill_switch(False, actor="dashboard")
    return {"engaged": False}


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


def _fmt_volume(v: int) -> str:
    """Compact share-volume label: 2.4M, 530K, or the raw count."""
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return str(v)


def _screen_context() -> dict[str, Any]:
    """Render context for the screener panel: the latest run + its matches.

    Defensive — the screener is outer-ring; a DB hiccup degrades the panel to
    a placeholder rather than 500-ing the page.
    """
    try:
        latest = get_latest_screen()
    except Exception:
        return {"run": None, "rows": [], "load_error": True}
    if latest is None:
        return {"run": None, "rows": [], "load_error": False}

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
        }
        for idx, c in enumerate(latest.matches, start=1)
    ]
    return {"run": run_view, "rows": rows, "load_error": False}


@app.get("/api/screen", response_class=HTMLResponse)
def api_screen(request: Request) -> Any:
    return templates.TemplateResponse(request, "_screen.html", _screen_context())
