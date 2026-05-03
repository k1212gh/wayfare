"""Process-wide tour concurrency state.

Extracted from server.py (refactor Step 2.5). Concurrency is bounded by
**ADB device contention** (not by the backend itself), so we track an
"active tour" *slot per device serial*. Two tours targeting different
devices may run in parallel; two tours targeting the same serial are
rejected at /run with HTTP 409.

- a module-level lock guarding all mutations to the per-device slot map
- a dict of per-tour threading.Event objects that the pipeline thread polls
- helpers to signal/query cancellation

All callers (server.py, api/tours.py, services/pipeline_service.py) import
from this module rather than keep their own copies. Function-based
accessors are used instead of exposing the dict directly — imported
names snapshot at import time, so a plain `_active_tours_by_device`
reference would go stale.
"""

from __future__ import annotations

import threading

from dashboard.backend.paths import _cancel_path

# Acquire this before reading-then-writing the per-device slot map.
_tour_lock: threading.Lock = threading.Lock()

# serial -> tour_id. Each serial holds at most one running pipeline.
# Internal — do NOT import this name directly; it goes stale across
# modules. Use the accessors below.
_active_tours_by_device: dict[str, str] = {}

# Per-tour Event objects set by /stop. The pipeline thread polls on these so
# it can react faster than waiting for the on-disk cancel.flag to appear.
_cancel_events: dict[str, threading.Event] = {}


def get_lock() -> threading.Lock:
    """Return the shared tour lock. Callers use `with tour_store.get_lock(): ...`
    when they need atomic read-modify-write on the per-device slot map."""
    return _tour_lock


def get_active_tour_on(serial: str) -> str | None:
    """tour_id currently bound to this device serial, or None.

    Caller is expected to have already resolved 'auto' to a real serial —
    an empty `serial` here is treated as its own bucket (used by tests
    that don't care about device routing)."""
    return _active_tours_by_device.get(serial)


def set_active_tour_on(serial: str, tour_id: str | None) -> None:
    """Claim or release the slot for a serial. Caller holds get_lock()
    when transitioning from one tour to another; release-to-None is safe
    even without the lock (finally blocks call it on every pipeline exit
    path)."""
    if tour_id is None:
        _active_tours_by_device.pop(serial, None)
    else:
        _active_tours_by_device[serial] = tour_id


def find_device_for_tour(tour_id: str) -> str | None:
    """Reverse lookup: which serial does this tour_id currently occupy?

    Used by pipeline cleanup (knows tour_id, not serial) and DELETE guard
    (refuse delete if any device still holds this tour).
    """
    for serial, jid in _active_tours_by_device.items():
        if jid == tour_id:
            return serial
    return None


def is_any_tour_active() -> bool:
    """True if at least one device slot is occupied. Diagnostic only."""
    return bool(_active_tours_by_device)


def active_tours_snapshot() -> dict[str, str]:
    """Read-only copy of the slot map. For debugging / diagnostics."""
    return dict(_active_tours_by_device)


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
