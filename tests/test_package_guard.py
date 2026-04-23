"""Unit tests for TapWalker._check_app_bounds (package guard).

Runnable as: `python tests/test_package_guard.py` from repo root.

Verifies: the guard presses BACK when the foreground package drifts away
from the target, and falls back to `monkey -p` if BACK doesn't recover.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_walker():
    """Construct a TapWalker without triggering its init dependencies."""
    from stage3_walk.tap_walker import TapWalker
    # Bypass __init__ — we only need a handful of attrs for _check_app_bounds.
    ex = TapWalker.__new__(TapWalker)
    ex.device_serial = "emulator-5554"
    ex.target_package = "com.target.app"
    ex._allow_external = False
    from collections import defaultdict
    ex.trap_stats = defaultdict(int)
    return ex


def test_guard_noop_when_inside_target():
    ex = _make_walker()
    # Fake dumpsys showing we're inside target package
    fake_out = b"topResumedActivity=ActivityRecord{abc u0 com.target.app/.MainActivity t123"
    calls = []
    def fake_run(args, **kw):
        calls.append(list(args))
        m = MagicMock(); m.stdout = fake_out; m.stderr = b""; m.returncode = 0
        return m
    with patch("stage3_walk.tap_walker.subprocess.run", side_effect=fake_run):
        ex._check_app_bounds()
    # Only the foreground-check subprocess call should have happened
    assert all("keyevent" not in c for c in calls), \
        f"guard pressed BACK when inside target: {calls}"
    assert all("monkey" not in c for c in calls), \
        f"guard relaunched when inside target: {calls}"
    print("[PASS] test_guard_noop_when_inside_target")


def test_guard_back_recovers_after_first_press():
    ex = _make_walker()
    outputs = [
        # 1st check: in Gmail (drifted)
        b"topResumedActivity=ActivityRecord{abc u0 com.google.gmail/.Main t9",
        # 2nd check (after BACK): back in target
        b"topResumedActivity=ActivityRecord{abc u0 com.target.app/.MainActivity t8",
    ]
    def fake_run(args, **kw):
        m = MagicMock(); m.returncode = 0; m.stderr = b""
        if any("dumpsys" in a for a in args):
            m.stdout = outputs.pop(0) if outputs else b""
        else:
            m.stdout = b""
        return m
    calls = []
    def tracking_run(args, **kw):
        calls.append(list(args))
        return fake_run(args, **kw)
    with patch("stage3_walk.tap_walker.subprocess.run", side_effect=tracking_run):
        ex._check_app_bounds()
    assert any("keyevent" in c and "KEYCODE_BACK" in c for c in calls), \
        f"guard did not press BACK when drifted: {calls}"
    assert all("monkey" not in c for c in calls), \
        f"guard relaunched unnecessarily: {calls}"
    assert ex.trap_stats["app_exit_bounced"] == 1
    assert ex.trap_stats["app_exit_relaunched"] == 0
    print("[PASS] test_guard_back_recovers_after_first_press")


def test_guard_relaunches_when_back_fails():
    ex = _make_walker()
    # Every foreground check returns external app — BACK can't recover
    external_out = b"topResumedActivity=ActivityRecord{abc u0 com.google.gmail/.Main t9"
    def fake_run(args, **kw):
        m = MagicMock(); m.returncode = 0; m.stderr = b""; m.stdout = external_out
        return m
    calls = []
    def tracking_run(args, **kw):
        calls.append(list(args))
        return fake_run(args, **kw)
    with patch("stage3_walk.tap_walker.subprocess.run", side_effect=tracking_run):
        ex._check_app_bounds()
    back_presses = [c for c in calls if "keyevent" in c and "KEYCODE_BACK" in c]
    relaunches = [c for c in calls if "monkey" in c]
    assert len(back_presses) == 3, f"expected 3 back presses, got {len(back_presses)}"
    assert len(relaunches) == 1, f"expected 1 monkey relaunch, got {len(relaunches)}"
    # Verify monkey is launching the target package
    assert "com.target.app" in relaunches[0]
    assert ex.trap_stats["app_exit_bounced"] == 1
    assert ex.trap_stats["app_exit_relaunched"] == 1
    print("[PASS] test_guard_relaunches_when_back_fails")


def test_guard_disabled_via_allow_external_flag():
    ex = _make_walker()
    ex._allow_external = True  # User set ALLOW_EXTERNAL=1
    calls = []
    def tracking_run(args, **kw):
        calls.append(list(args))
        m = MagicMock(); m.returncode = 0; m.stderr = b""; m.stdout = b""
        return m
    with patch("stage3_walk.tap_walker.subprocess.run", side_effect=tracking_run):
        ex._check_app_bounds()
    assert not calls, f"guard ran subprocess despite ALLOW_EXTERNAL: {calls}"
    print("[PASS] test_guard_disabled_via_allow_external_flag")


def test_guard_noop_when_target_package_unset():
    ex = _make_walker()
    ex.target_package = ""  # Not set yet
    calls = []
    def tracking_run(args, **kw):
        calls.append(list(args))
        m = MagicMock(); m.returncode = 0; m.stderr = b""; m.stdout = b""
        return m
    with patch("stage3_walk.tap_walker.subprocess.run", side_effect=tracking_run):
        ex._check_app_bounds()
    assert not calls
    print("[PASS] test_guard_noop_when_target_package_unset")


if __name__ == "__main__":
    test_guard_noop_when_inside_target()
    test_guard_back_recovers_after_first_press()
    test_guard_relaunches_when_back_fails()
    test_guard_disabled_via_allow_external_flag()
    test_guard_noop_when_target_package_unset()
    print()
    print("=== ALL PACKAGE GUARD TESTS PASSED ===")
