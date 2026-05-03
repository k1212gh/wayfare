"""/api/tours/{id}/run endpoint — per-device locking integration tests.

Sets up a tmp WORKSPACE_ROOT, drops in pre-staged tour dirs with the
required pipeline_state.json, mocks the background-thread launch, and
verifies that two tours on different serials both succeed while two tours
on the same serial collide with 409.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def staged_workspace(tmp_path, monkeypatch):
    """Point WORKSPACE_ROOT at tmp_path and create two pre-staged tours.

    Both tours are in 'UPLOADED' state, so /run with from_stage=0 is allowed.
    """
    monkeypatch.setattr("dashboard.backend.paths.WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr("dashboard.backend.api.tours.WORKSPACE_ROOT", tmp_path)

    for jid in ("tour-alpha", "tour-beta"):
        d = tmp_path / jid
        d.mkdir()
        (d / "pipeline_state.json").write_text(
            json.dumps({"stage": "UPLOADED"}), encoding="utf-8"
        )
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_slots():
    from dashboard.backend.services import tour_store as js
    js._active_tours_by_device.clear()
    yield
    js._active_tours_by_device.clear()


def _client():
    """Build a TestClient against the live FastAPI app."""
    from fastapi.testclient import TestClient
    import dashboard.backend.server as srv
    return TestClient(srv.app)


def test_two_tours_on_different_serials_both_succeed(staged_workspace):
    """tour-alpha on emulator + tour-beta on real device → both 200."""
    with patch("dashboard.backend.api.tours.threading.Thread") as fake_thread:
        # Don't actually launch the pipeline — just record the call
        fake_thread.return_value.start.return_value = None
        client = _client()
        r1 = client.post(
            "/api/tours/tour-alpha/run", params={"device_serial": "emulator-5554"},
        )
        r2 = client.post(
            "/api/tours/tour-beta/run", params={"device_serial": "R5CT20G1ZFL"},
        )

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["device_serial"] == "emulator-5554"
    assert r2.json()["device_serial"] == "R5CT20G1ZFL"

    # Both slots occupied
    from dashboard.backend.services import tour_store as js
    assert js.get_active_tour_on("emulator-5554") == "tour-alpha"
    assert js.get_active_tour_on("R5CT20G1ZFL") == "tour-beta"


def test_same_serial_second_call_returns_409(staged_workspace):
    """Two different tours targeting the same serial → second is 409."""
    with patch("dashboard.backend.api.tours.threading.Thread") as fake_thread:
        fake_thread.return_value.start.return_value = None
        client = _client()
        r1 = client.post(
            "/api/tours/tour-alpha/run", params={"device_serial": "emulator-5554"},
        )
        r2 = client.post(
            "/api/tours/tour-beta/run", params={"device_serial": "emulator-5554"},
        )

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 409, r2.text
    assert "emulator-5554 is busy" in r2.json().get("detail", "")
    # Slot still belongs to alpha
    from dashboard.backend.services import tour_store as js
    assert js.get_active_tour_on("emulator-5554") == "tour-alpha"


def test_same_tour_on_two_serials_blocked(staged_workspace):
    """Re-running the SAME tour_id on a different device must also 409."""
    with patch("dashboard.backend.api.tours.threading.Thread") as fake_thread:
        fake_thread.return_value.start.return_value = None
        client = _client()
        r1 = client.post(
            "/api/tours/tour-alpha/run", params={"device_serial": "emulator-5554"},
        )
        r2 = client.post(
            "/api/tours/tour-alpha/run", params={"device_serial": "R5CT20G1ZFL"},
        )

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 409, r2.text
    assert "already running on device" in r2.json().get("detail", "")


def test_empty_serial_resolves_to_first_device(staged_workspace):
    """device_serial='' → resolves first attached device, locks under that
    real serial (so a 2nd empty-serial caller hitting same device → 409)."""
    with patch("dashboard.backend.api.tours.threading.Thread") as fake_thread, \
         patch("dashboard.backend.api.tours._get_first_device",
               return_value="emulator-5554"):
        fake_thread.return_value.start.return_value = None
        client = _client()
        r1 = client.post("/api/tours/tour-alpha/run")
        r2 = client.post("/api/tours/tour-beta/run")  # also empty serial

    assert r1.status_code == 200, r1.text
    assert r1.json()["device_serial"] == "emulator-5554"
    # Second one tried to land on the same first-attached device → 409
    assert r2.status_code == 409, r2.text


def test_no_attached_device_returns_503(staged_workspace):
    """Empty serial + zero attached devices → 503 (not silently succeed)."""
    with patch("dashboard.backend.api.tours.threading.Thread"), \
         patch("dashboard.backend.api.tours._get_first_device", return_value=""):
        client = _client()
        r = client.post("/api/tours/tour-alpha/run")
    assert r.status_code == 503, r.text


def test_delete_refuses_while_running_on_any_device(staged_workspace):
    """DELETE while the tour holds any device slot → 409."""
    from dashboard.backend.services import tour_store as js
    js.set_active_tour_on("emulator-5554", "tour-alpha")

    client = _client()
    r = client.delete("/api/tours/tour-alpha")
    assert r.status_code == 409, r.text
    assert "stop it first" in r.json().get("detail", "").lower()


def test_delete_succeeds_when_tour_idle(staged_workspace):
    """DELETE with no active slot for that tour → 200 + workspace removed."""
    client = _client()
    r = client.delete("/api/tours/tour-alpha")
    assert r.status_code == 200, r.text
    assert not (staged_workspace / "tour-alpha").exists()


def test_invalid_serial_format_returns_400(staged_workspace):
    """Reject argv-injection attempts via serial param."""
    client = _client()
    r = client.post(
        "/api/tours/tour-alpha/run", params={"device_serial": "-foo;rm -rf /"},
    )
    assert r.status_code == 400, r.text
