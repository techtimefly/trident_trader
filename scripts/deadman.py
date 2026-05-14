"""Dead-man's switch.

A separate process from paper_run.py. Its only job: if the runner's heartbeat
goes stale (>STALE_THRESHOLD without an update), and there is at least one open
position or open order on Alpaca, cancel everything and close everything.

Why it's a separate process: if paper_run.py crashes, an in-process safety task
crashes with it. The watchdog has to live independently. Run it in a second
terminal, a tmux pane, or a systemd unit.

Usage:
    PYTHONPATH=src python3 scripts/deadman.py
"""
from __future__ import annotations

import asyncio
import signal as os_signal
from datetime import UTC, datetime, timedelta

from trident.audit.log import configure_logging, get_logger, record
from trident.execution.alpaca import AlpacaBroker
from trident.persistence.state import last_heartbeat

STALE_THRESHOLD = timedelta(seconds=45)
POLL_INTERVAL_SECONDS = 5


async def main() -> None:
    configure_logging()
    log = get_logger("deadman")
    broker = AlpacaBroker()

    stop_event = asyncio.Event()

    def _stop(*_: object) -> None:
        log.info("deadman_stop_signal")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig_name in (os_signal.SIGINT, os_signal.SIGTERM):
        loop.add_signal_handler(sig_name, _stop)

    log.info("deadman_started", stale_threshold_seconds=STALE_THRESHOLD.total_seconds())
    record("deadman_started", actor="deadman", payload={})
    armed = False

    while not stop_event.is_set():
        hb = last_heartbeat()
        if hb is None:
            armed = False
        else:
            age = datetime.now(UTC) - hb
            if age <= STALE_THRESHOLD:
                armed = True
            elif armed:
                # Heartbeat used to be fresh and now isn't — the runner has died.
                # Check if there is anything to clean up before tripping.
                try:
                    positions = broker.list_positions()
                    open_orders = broker.list_open_orders()
                except Exception:
                    log.exception("deadman_check_failed")
                    positions = []
                    open_orders = []
                if positions or open_orders:
                    log.error(
                        "deadman_tripping",
                        age_seconds=age.total_seconds(),
                        positions=len(positions),
                        open_orders=len(open_orders),
                    )
                    record(
                        "deadman_tripped",
                        actor="deadman",
                        payload={
                            "age_seconds": age.total_seconds(),
                            "positions": len(positions),
                            "open_orders": len(open_orders),
                        },
                    )
                    try:
                        broker.close_all_positions(cancel_orders=True)
                    except Exception:
                        log.exception("deadman_flatten_failed")
                else:
                    log.info("deadman_stale_but_nothing_to_flatten", age_seconds=age.total_seconds())
                armed = False  # disarm until heartbeat resumes
            else:
                # heartbeat never went fresh — runner never started, don't trip
                pass

        try:
            await asyncio.wait_for(stop_event.wait(), POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass

    log.info("deadman_stopped")


if __name__ == "__main__":
    asyncio.run(main())
