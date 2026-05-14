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

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from trident.audit.log import configure_logging
from trident.clock import ET, is_market_open, now_et
from trident.dashboard.alpaca_view import get_account, list_positions
from trident.persistence.models import AuditEvent, Signal
from trident.persistence.session import session_scope
from trident.persistence.state import (
    kill_switch_engaged,
    last_heartbeat,
    set_kill_switch,
)

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
