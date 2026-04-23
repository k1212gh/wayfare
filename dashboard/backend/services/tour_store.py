"""Process-wide tour concurrency state.

Extracted from server.py (refactor Step 2.5). One pipeline runs at a time
because ADB is single-tenant per device, so we have:

- a module-level lock guarding the "active tour" slot
- a dict of per-tour threading.Event objects that the pipeline thread polls
- helpers to signal/query cancellation

All callers (server.py, api/tours.py, services/pipeline_service.py) import
from this module rather than keep their own copies. Function-based
accessors (`get_active_tour()`, `set_active_tour(...)`) are used instead of
exposing the `str | None` module-level variable directly — imported names
snapshot at import time, so a plain `_active_tour` would go stale.
"""

from __future__ import annotations

import threading

from dashboard.backend.paths import _cancel_path

# Only one pipeline runs at a time (single ADB device assumption). Acquire
# this before mutating _active_tour.
_tour_lock: threading.Lock = threading.Lock()

# Internal — do NOT import this name directly; it goes stale across modules.
# Use get_active_tour() / set_active_tour() instead.
_active_tour: str | None = None

# Per-tour Event objects set by /stop. The pipeline thread polls on these so
# it can react faster than waiting for the on-disk cancel.flag to appear.
_cancel_events: dict[str, threading.Event] = {}


def get_lock() -> threading.Lock:
    """Return the shared tour lock. Callers use `with tour_store.get_lock(): ...`
    when they need atomic read-modify-write on the active-tour slot."""
    return _tour_lock


def get_active_tour() -> str | None:
    """Current active tour_id, or None when no pipeline is running."""
    return _active_tour


def set_active_tour(tour_id: str | None) -> None:
    """Set / clear the active tour slot. Caller must already hold get_lock()
    when transitioning from one tour to another; reset-to-None is safe even
    without the lock (finally blocks call it on every pipeline exit path)."""
    global _active_tour
    _active_tour = tour_id


def register_cancel_event(tour_id: str) -> threading.Event:
    """Create (or replace) the cancellation Event for a tour. Pipeline thread
    calls this at start; /stop endpoint calls .set() on the returned event."""
    ev = threading.Event()
    _cancel_events[tour_id] = ev
    return ev


def signal_cancel(tour_id: str) -> None:
    """Set the tour's cancel Event if one is registered. No-op otherwise."""
    ev = _cancel_events.get(tour_id)
    if ev is not None:
        ev.set()


def discard_cancel_event(tour_id: str) -> None:
    """Pipeline thread calls this on exit so stale Events don't accumulate."""
    _cancel_events.pop(tour_id, None)


def is_cancelled(tour_id: str) -> bool:
    """True if either the in-memory Event is set OR the on-disk cancel.flag
    exists. Both checks matter: the flag persists across backend restarts;
    the event fires immediately without filesystem roundtrip."""
    ev = _cancel_events.get(tour_id)
    if ev is not None and ev.is_set():
        return True
    return _cancel_path(tour_id).exists()
