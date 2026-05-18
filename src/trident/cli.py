from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

app = typer.Typer(
    name="trident",
    help="Trident Trader — personal day-trading bot.",
    no_args_is_help=True,
)
run_app = typer.Typer(help="Start a runner process.", no_args_is_help=True)
watchlist_app = typer.Typer(help="Manage named watchlists.")
db_app = typer.Typer(help="Database management.", no_args_is_help=True)

app.add_typer(run_app, name="run")
app.add_typer(watchlist_app, name="watchlist")
app.add_typer(db_app, name="db")

_SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"


def _run(cmd: list[str]) -> None:
    raise SystemExit(subprocess.run(cmd).returncode)


def _load_script(name: str) -> Any:
    """Import a script from the scripts/ directory by adding it to sys.path."""
    scripts = str(_SCRIPTS_DIR)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module(name)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@run_app.command("shadow")
def run_shadow(
    strategy: Annotated[
        str | None, typer.Option(help="Strategy to run. Default: configured default.")
    ] = None,
) -> None:
    """Live data feed, signals + gate evaluated, no orders submitted."""
    from trident.settings import get_settings

    _m: Any = _load_script("shadow_run")
    asyncio.run(_m.main(strategy or get_settings().default_strategy))


@run_app.command("paper")
def run_paper(
    strategy: Annotated[
        str | None, typer.Option(help="Strategy to run. Default: configured default.")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the deadman reminder.")] = False,
) -> None:
    """Submit bracket orders to Alpaca paper account (run deadman alongside)."""
    if not yes:
        typer.echo("Paper trading submits bracket orders to your Alpaca paper account.")
        typer.echo(
            f"Run deadman.py alongside in a separate terminal:\n"
            f"  PYTHONPATH=src python {_SCRIPTS_DIR / 'deadman.py'}"
        )
        if not typer.confirm("Continue?", default=True):
            raise typer.Abort()
    from trident.settings import get_settings

    _m: Any = _load_script("paper_run")
    asyncio.run(_m.main(strategy or get_settings().default_strategy))


@run_app.command("dashboard")
def run_dashboard() -> None:
    """Launch the FastAPI dashboard (http://127.0.0.1:8765)."""
    import uvicorn

    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.environ.get("DASHBOARD_PORT", "8765"))
    typer.echo(f"Dashboard → http://127.0.0.1:{port}/")
    uvicorn.run("trident.dashboard.app:app", host=host, port=port, log_level="info")


# ---------------------------------------------------------------------------
# analysis — subprocess delegates to the existing scripts
# ---------------------------------------------------------------------------


@app.command()
def replay(
    date_: Annotated[
        str | None,
        typer.Option("--date", metavar="YYYY-MM-DD", help="Specific trading day."),
    ] = None,
    days: Annotated[int, typer.Option(help="Last N trading days (ignored if --date given).")] = 1,
    equity: Annotated[str, typer.Option(help="Account equity for position sizing.")] = "100000",
    strategy: Annotated[str | None, typer.Option(help="Strategy name.")] = None,
    no_persist: Annotated[
        bool, typer.Option("--no-persist", help="Skip writing results to the DB.")
    ] = False,
) -> None:
    """Idealistic replay — no slippage or fees. Sanity-check only."""
    cmd = [
        sys.executable,
        str(_SCRIPTS_DIR / "replay.py"),
        "--days",
        str(days),
        "--equity",
        equity,
    ]
    if date_:
        cmd += ["--date", date_]
    if strategy:
        cmd += ["--strategy", strategy]
    if no_persist:
        cmd += ["--no-persist"]
    _run(cmd)


@app.command()
def backtest(
    date_: Annotated[
        str | None,
        typer.Option("--date", metavar="YYYY-MM-DD", help="Specific trading day."),
    ] = None,
    days: Annotated[int, typer.Option(help="Last N trading days (ignored if --date given).")] = 1,
    equity: Annotated[str, typer.Option(help="Account equity for position sizing.")] = "100000",
    strategy: Annotated[str | None, typer.Option(help="Strategy name.")] = None,
    window_days: Annotated[
        int, typer.Option(help="Walk-forward window size in trading days.")
    ] = 5,
    slippage_bps: Annotated[
        str | None, typer.Option(help="Synthetic slippage in basis points.")
    ] = None,
    no_persist: Annotated[
        bool, typer.Option("--no-persist", help="Skip writing results to the DB.")
    ] = False,
) -> None:
    """Honest backtest with slippage + fees and walk-forward windows."""
    cmd = [
        sys.executable,
        str(_SCRIPTS_DIR / "backtest.py"),
        "--days",
        str(days),
        "--equity",
        equity,
        "--window-days",
        str(window_days),
    ]
    if date_:
        cmd += ["--date", date_]
    if strategy:
        cmd += ["--strategy", strategy]
    if slippage_bps:
        cmd += ["--slippage-bps", slippage_bps]
    if no_persist:
        cmd += ["--no-persist"]
    _run(cmd)


@app.command()
def compare(
    date_: Annotated[
        str | None,
        typer.Option("--date", metavar="YYYY-MM-DD", help="Specific trading day."),
    ] = None,
    days: Annotated[int, typer.Option(help="Last N trading days (ignored if --date given).")] = 30,
    equity: Annotated[str, typer.Option(help="Account equity for position sizing.")] = "100000",
    strategy: Annotated[
        list[str],
        typer.Option("--strategy", help="Strategy to include (repeatable). Default: all."),
    ] = [],  # noqa: B006
    no_persist: Annotated[
        bool, typer.Option("--no-persist", help="Skip writing results to the DB.")
    ] = False,
) -> None:
    """Compare all registered strategies over the same days and cost model."""
    cmd = [
        sys.executable,
        str(_SCRIPTS_DIR / "compare.py"),
        "--days",
        str(days),
        "--equity",
        equity,
    ]
    if date_:
        cmd += ["--date", date_]
    for s in strategy:
        cmd += ["--strategy", s]
    if no_persist:
        cmd += ["--no-persist"]
    _run(cmd)


@app.command()
def screen(
    preset: Annotated[
        str | None, typer.Option(help="Named preset to run instead of the active one.")
    ] = None,
    min_price: Annotated[
        str | None, typer.Option("--min-price", metavar="DOLLARS", help="Minimum price.")
    ] = None,
    max_price: Annotated[
        str | None, typer.Option("--max-price", metavar="DOLLARS", help="Maximum price.")
    ] = None,
    min_avg_volume: Annotated[
        int | None, typer.Option("--min-avg-volume", help="Minimum avg daily volume.")
    ] = None,
    min_change: Annotated[
        str | None,
        typer.Option("--min-change", metavar="PCT", help="Minimum recent %% change."),
    ] = None,
    max_change: Annotated[
        str | None,
        typer.Option("--max-change", metavar="PCT", help="Maximum recent %% change."),
    ] = None,
    lookback: Annotated[
        int | None, typer.Option(help="Trading-day window for avg volume and %% change.")
    ] = None,
    save_preset: Annotated[
        str | None, typer.Option("--save-preset", help="Save criteria as a named preset.")
    ] = None,
    no_persist: Annotated[
        bool, typer.Option("--no-persist", help="Skip writing results to the DB.")
    ] = False,
) -> None:
    """Run the stock screener against the active preset."""
    cmd = [sys.executable, str(_SCRIPTS_DIR / "screen.py")]
    if preset:
        cmd += ["--preset", preset]
    if min_price:
        cmd += ["--min-price", min_price]
    if max_price:
        cmd += ["--max-price", max_price]
    if min_avg_volume is not None:
        cmd += ["--min-avg-volume", str(min_avg_volume)]
    if min_change:
        cmd += ["--min-change", min_change]
    if max_change:
        cmd += ["--max-change", max_change]
    if lookback is not None:
        cmd += ["--lookback", str(lookback)]
    if save_preset:
        cmd += ["--save-preset", save_preset]
    if no_persist:
        cmd += ["--no-persist"]
    _run(cmd)


@app.command()
def suggest(
    max_suggestions: Annotated[
        int, typer.Option("--max", help="Maximum symbols to suggest.")
    ] = 5,
    no_persist: Annotated[
        bool, typer.Option("--no-persist", help="Skip writing results to the DB.")
    ] = False,
) -> None:
    """AI stock suggestions — advisory only. Run screen first."""
    cmd = [sys.executable, str(_SCRIPTS_DIR / "suggest.py"), "--max", str(max_suggestions)]
    if no_persist:
        cmd += ["--no-persist"]
    _run(cmd)


@app.command()
def smoke() -> None:
    """Check DB and Alpaca credentials without submitting anything."""
    _run([sys.executable, str(_SCRIPTS_DIR / "smoke_test.py")])


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command()
def status() -> None:
    """System health: DB, Alpaca account, kill switch, watchlist, heartbeat."""
    from datetime import UTC, datetime

    from sqlalchemy import text

    from trident.audit.log import configure_logging
    from trident.clock import ET
    from trident.persistence.session import get_engine
    from trident.persistence.state import kill_switch_engaged, last_heartbeat
    from trident.persistence.watchlist_store import get_active_watchlist
    from trident.settings import get_settings

    configure_logging()
    settings = get_settings()
    ok = True

    try:
        with get_engine().connect() as conn:
            conn.execute(text("select 1")).scalar_one()
        typer.echo("DB            OK")
    except Exception as exc:
        typer.echo(f"DB            FAIL  {exc}")
        ok = False

    if not settings.alpaca_api_key:
        typer.echo("Alpaca        FAIL  no credentials in .env")
        ok = False
    else:
        try:
            from alpaca.trading.client import TradingClient

            client = TradingClient(
                api_key=settings.alpaca_api_key,
                secret_key=settings.alpaca_api_secret,
                paper=True,
            )
            acct: Any = client.get_account()
            typer.echo(
                f"Alpaca        OK    equity=${acct.equity}"
                f"  buying_power=${acct.buying_power}"
            )
        except Exception as exc:
            typer.echo(f"Alpaca        FAIL  {exc}")
            ok = False

    try:
        engaged = kill_switch_engaged()
        typer.echo(f"Kill switch   {'ENGAGED' if engaged else 'off'}")
    except Exception as exc:
        typer.echo(f"Kill switch   FAIL  {exc}")

    try:
        wl = get_active_watchlist()
        if wl:
            preview = ", ".join(wl.symbols[:5]) + ("…" if len(wl.symbols) > 5 else "")
            typer.echo(f"Watchlist     {wl.name!r}  ({len(wl.symbols)} symbols: {preview})")
        else:
            typer.echo("Watchlist     none active (using static fallback)")
    except Exception as exc:
        typer.echo(f"Watchlist     FAIL  {exc}")

    try:
        hb = last_heartbeat()
        if hb:
            delta = (datetime.now(UTC) - hb).total_seconds()
            typer.echo(
                f"Heartbeat     {hb.astimezone(ET).strftime('%H:%M:%S ET')}  ({delta:.0f}s ago)"
            )
        else:
            typer.echo("Heartbeat     none (shadow/paper runner not running)")
    except Exception as exc:
        typer.echo(f"Heartbeat     FAIL  {exc}")

    raise SystemExit(0 if ok else 1)


# ---------------------------------------------------------------------------
# kill-switch
# ---------------------------------------------------------------------------


@app.command("kill-switch")
def kill_switch_cmd(
    state: Annotated[str, typer.Argument(help="'on' to engage, 'off' to release.")],
) -> None:
    """Engage or release the kill switch without opening the dashboard."""
    if state not in ("on", "off"):
        typer.echo("Argument must be 'on' or 'off'.", err=True)
        raise typer.Exit(1)
    from trident.persistence.state import set_kill_switch

    engage = state == "on"
    set_kill_switch(engage, actor="cli")
    typer.echo(f"Kill switch {'ENGAGED' if engage else 'released'}.")


# ---------------------------------------------------------------------------
# watchlist
# ---------------------------------------------------------------------------


@watchlist_app.callback(invoke_without_command=True)
def watchlist_default(ctx: typer.Context) -> None:
    """Manage named watchlists. With no subcommand, lists all."""
    if ctx.invoked_subcommand is not None:
        return
    from trident.persistence.watchlist_store import list_watchlists

    wls = list_watchlists()
    if not wls:
        typer.echo("No watchlists. Create one: trident watchlist create NAME")
        return
    for wl in wls:
        marker = " [active]" if wl.is_active else ""
        typer.echo(f"  {wl.name}{marker}  ({len(wl.symbols)} symbols)")


@watchlist_app.command()
def add(
    symbols: Annotated[
        list[str], typer.Argument(help="Symbols to add to the active watchlist.")
    ],
) -> None:
    """Add symbols to the active watchlist."""
    from trident.persistence.watchlist_store import add_symbols, get_active_watchlist

    wl = get_active_watchlist()
    if wl is None:
        typer.echo("No active watchlist. Create one: trident watchlist create NAME", err=True)
        raise typer.Exit(1)
    added = add_symbols(wl.id, symbols)
    if added:
        typer.echo(f"Added {', '.join(added)} → {wl.name!r}")
    else:
        typer.echo("All symbols already present — nothing added.")


@watchlist_app.command()
def remove(
    symbol: Annotated[
        str, typer.Argument(help="Symbol to remove from the active watchlist.")
    ],
) -> None:
    """Remove a symbol from the active watchlist."""
    from trident.persistence.watchlist_store import get_active_watchlist, remove_symbol

    wl = get_active_watchlist()
    if wl is None:
        typer.echo("No active watchlist.", err=True)
        raise typer.Exit(1)
    remove_symbol(wl.id, symbol)
    typer.echo(f"Removed {symbol.upper()} from {wl.name!r}.")


@watchlist_app.command()
def activate(
    name: Annotated[str, typer.Argument(help="Name of the watchlist to activate.")],
) -> None:
    """Make a named watchlist the active one."""
    from trident.persistence.watchlist_store import activate_watchlist, list_watchlists

    wls = list_watchlists()
    match = next((w for w in wls if w.name == name), None)
    if match is None:
        typer.echo(f"No watchlist named {name!r}. Run 'trident watchlist' to list all.", err=True)
        raise typer.Exit(1)
    activate_watchlist(match.id)
    typer.echo(f"Activated {name!r} ({len(match.symbols)} symbols).")


@watchlist_app.command()
def create(
    name: Annotated[str, typer.Argument(help="Name for the new watchlist.")],
    symbols: Annotated[
        list[str] | None, typer.Argument(help="Initial symbols (optional).")
    ] = None,
) -> None:
    """Create a new named watchlist."""
    from trident.persistence.watchlist_store import create_watchlist

    try:
        wl_id = create_watchlist(name, symbols or [])
        typer.echo(f"Created {name!r} (id={wl_id}).")
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@watchlist_app.command()
def delete(
    name: Annotated[str, typer.Argument(help="Name of the watchlist to delete.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Delete a named watchlist."""
    from trident.persistence.watchlist_store import delete_watchlist, list_watchlists

    wls = list_watchlists()
    match = next((w for w in wls if w.name == name), None)
    if match is None:
        typer.echo(f"No watchlist named {name!r}.", err=True)
        raise typer.Exit(1)
    if not yes and not typer.confirm(f"Delete {name!r}?", default=False):
        raise typer.Abort()
    delete_watchlist(match.id)
    typer.echo(f"Deleted {name!r}.")


# ---------------------------------------------------------------------------
# db
# ---------------------------------------------------------------------------


@db_app.command()
def upgrade() -> None:
    """Run alembic upgrade head (apply pending migrations)."""
    _run([sys.executable, "-m", "alembic", "upgrade", "head"])


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def main() -> None:
    app()
