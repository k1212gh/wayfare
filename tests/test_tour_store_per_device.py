"""Per-device active-tour slot — pure unit tests for tour_store.

Verifies the slot map allows independent tours on different serials but
catches collisions on the same serial.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_slots():
    """Each test gets a clean slot map."""
    from dashboard.backend.services import tour_store as js
    js._active_tours_by_device.clear()
    js._cancel_events.clear()
    yield
    js._active_tours_by_device.clear()
    js._cancel_events.clear()


def test_slot_set_and_get_round_trip():
    from dashboard.backend.services import tour_store as js
    js.set_active_tour_on("emulator-5554", "tour-A")
    assert js.get_active_tour_on("emulator-5554") == "tour-A"
    assert js.get_active_tour_on("R5CT20G1ZFL") is None


def test_slot_release_with_none():
    from dashboard.backend.services import tour_store as js
    js.set_active_tour_on("emulator-5554", "tour-A")
    js.set_active_tour_on("emulator-5554", None)
    assert js.get_active_tour_on("emulator-5554") is None
    assert not js.is_any_tour_active()


def test_two_devices_independent():
    from dashboard.backend.services import tour_store as js
    js.set_active_tour_on("emulator-5554", "tour-A")
    js.set_active_tour_on("R5CT20G1ZFL", "tour-B")
    assert js.get_active_tour_on("emulator-5554") == "tour-A"
    assert js.get_active_tour_on("R5CT20G1ZFL") == "tour-B"
    assert js.is_any_tour_active()


def test_find_device_for_tour_reverse_lookup():
    from dashboard.backend.services import tour_store as js
    js.set_active_tour_on("emulator-5554", "tour-A")
    js.set_active_tour_on("R5CT20G1ZFL", "tour-B")
    assert js.find_device_for_tour("tour-A") == "emulator-5554"
    assert js.find_device_for_tour("tour-B") == "R5CT20G1ZFL"
    assert js.find_device_for_tour("tour-C") is None


def test_slot_overwrite_same_serial():
    """Setting a new tour_id on the same serial replaces the old (caller's tour
    to refuse this at /run; tour_store itself is a dumb store)."""
    from dashboard.backend.services import tour_store as js
    js.set_active_tour_on("emulator-5554", "tour-A")
    js.set_active_tour_on("emulator-5554", "tour-B")
    assert js.get_active_tour_on("emulator-5554") == "tour-B"
    # Old tour no longer reverse-resolves
    assert js.find_device_for_tour("tour-A") is None


def test_active_tours_snapshot_is_isolated_copy():
    from dashboard.backend.services import tour_store as js
    js.set_active_tour_on("emulator-5554", "tour-A")
    snap = js.active_tours_snapshot()
    snap["emulator-5554"] = "MUTATED"
    # Original map untouched
    assert js.get_active_tour_on("emulator-5554") == "tour-A"


def test_release_idempotent():
    """Releasing a slot that was never claimed must not raise."""
    from dashboard.backend.services import tour_store as js
    js.set_active_tour_on("emulator-5554", None)  # no-op
    js.set_active_tour_on("emulator-5554", "tour-A")
    js.set_active_tour_on("emulator-5554", None)
    js.set_active_tour_on("emulator-5554", None)  # double release
    assert js.get_active_tour_on("emulator-5554") is None


def test_lock_object_is_reentrant_safe_acquire():
    """get_lock returns the shared lock — acquire/release pattern works."""
    from dashboard.backend.services import tour_store as js
    lock = js.get_lock()
    with lock:
        js.set_active_tour_on("emulator-5554", "tour-A")
    # after release, another acquire works
    with lock:
        assert js.get_active_tour_on("emulator-5554") == "tour-A"
