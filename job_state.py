"""Process-wide (not per-browser-client) tracking for the single background
research job slot. Deliberately doesn't track "which run is active" - the
results grid is entirely DB-driven (see db.grid_rows) and doesn't need it;
this module only holds the one-job-at-a-time lock, cancellation signaling,
progress counters, and a cosmetic "what's it doing right now" caption - all
of which must be shared across every NiceGUI browser client, not scoped to
whichever tab triggered a job.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_running = False
_current: tuple[str, str] | None = None
_previous: tuple[str, str] | None = None
_scan_type = ""
_cancel_requested = False
_total = 0
_completed = 0


def try_start(scan_type: str) -> bool:
    """Atomically claims the single job slot. Returns False if already running."""
    global _running, _cancel_requested, _total, _completed, _current, _previous, _scan_type
    with _lock:
        if _running:
            return False
        _running = True
        _cancel_requested = False
        _total = 0
        _completed = 0
        _current = None
        _previous = None
        _scan_type = scan_type
        return True


def mark_finished() -> None:
    global _running
    with _lock:
        _running = False


def is_running() -> bool:
    with _lock:
        return _running


def note_activity(ticker: str, msg: str) -> None:
    """Tracks only the current ticker and the one before it (not every ticker
    ever touched this run) - the results grid already reflects everything
    permanently, so this caption is deliberately just a short "what's
    happening right now" glance, not a running log."""
    global _current, _previous
    with _lock:
        if _current is not None and _current[0] != ticker:
            _previous = _current
        _current = (ticker, msg)


def activity_snapshot() -> tuple[str, tuple[str, str] | None, tuple[str, str] | None]:
    """Returns (scan_type, current, previous)."""
    with _lock:
        return _scan_type, _current, _previous


def request_cancel() -> None:
    """Asks the running job to stop before starting its next ticker. Doesn't
    interrupt a ticker already in progress - that finishes normally, since
    aborting mid-LLM-call would just waste the in-flight work."""
    global _cancel_requested
    with _lock:
        _cancel_requested = True


def is_cancel_requested() -> bool:
    with _lock:
        return _cancel_requested


def set_total(total: int) -> None:
    global _total
    with _lock:
        _total = total


def mark_ticker_done() -> None:
    global _completed
    with _lock:
        _completed += 1


def progress_snapshot() -> tuple[int, int]:
    """Returns (completed, total)."""
    with _lock:
        return _completed, _total
