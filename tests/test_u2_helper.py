"""Unit tests for u2_helper — verifies fallback behavior without a real device.

Runnable as: `python tests/test_u2_helper.py` from repo root.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_dump_hierarchy_returns_u2_xml_when_connected():
    """When u2.connect succeeds, dump_hierarchy uses u2 and returns its XML."""
    from stage3_walk import u2_helper
    # Reset internal state for isolation
    u2_helper._u2_devices.clear()
    u2_helper._u2_failed.clear()

    fake_xml = '<?xml version="1.0"?><hierarchy><node class="android.view.View"/></hierarchy>'
    fake_device = MagicMock()
    fake_device.dump_hierarchy.return_value = fake_xml
    fake_device.info = {"sdkInt": 30}

    fake_u2_module = MagicMock()
    fake_u2_module.connect.return_value = fake_device

    with patch.dict(sys.modules, {"uiautomator2": fake_u2_module}):
        xml = u2_helper.dump_hierarchy("emulator-5554")

    assert xml == fake_xml, f"expected fake_xml, got {xml[:60]!r}"
    fake_device.dump_hierarchy.assert_called_once_with(compressed=True, pretty=False)
    print("[PASS] test_dump_hierarchy_returns_u2_xml_when_connected")


def test_dump_hierarchy_falls_back_to_cli_on_u2_connect_failure():
    """If u2 connect fails, falls back to adb shell uiautomator dump."""
    from stage3_walk import u2_helper
    u2_helper._u2_devices.clear()
    u2_helper._u2_failed.clear()

    # Fake u2 that raises on connect
    fake_u2_module = MagicMock()
    fake_u2_module.connect.side_effect = RuntimeError("agent install failed")

    fake_cli_xml = b'<?xml version="1.0"?><hierarchy>cli-fallback</hierarchy>'
    def fake_subprocess_run(args, **kw):
        r = MagicMock()
        r.returncode = 0
        # Second call (cat) returns the XML
        if "cat" in args[-1]:
            r.stdout = fake_cli_xml
        else:
            r.stdout = b""
        r.stderr = b""
        return r

    with patch.dict(sys.modules, {"uiautomator2": fake_u2_module}), \
         patch.object(u2_helper.subprocess, "run", side_effect=fake_subprocess_run):
        xml = u2_helper.dump_hierarchy("emulator-5554")

    assert "cli-fallback" in xml, f"expected CLI fallback content, got {xml[:100]!r}"
    # Serial now in failed set — subsequent calls should go straight to CLI
    assert "emulator-5554" in u2_helper._u2_failed
    print("[PASS] test_dump_hierarchy_falls_back_to_cli_on_u2_connect_failure")


def test_dump_hierarchy_empty_on_both_failures():
    """If u2 AND CLI both fail (timeout/no-hierarchy), return empty string."""
    from stage3_walk import u2_helper
    u2_helper._u2_devices.clear()
    u2_helper._u2_failed.clear()

    fake_u2_module = MagicMock()
    fake_u2_module.connect.side_effect = RuntimeError("boom")

    import subprocess as real_sp
    def fake_subprocess_run(args, **kw):
        raise real_sp.TimeoutExpired(cmd=args, timeout=5)

    with patch.dict(sys.modules, {"uiautomator2": fake_u2_module}), \
         patch.object(u2_helper.subprocess, "run", side_effect=fake_subprocess_run):
        xml = u2_helper.dump_hierarchy("emulator-5554")

    assert xml == "", f"expected empty string, got {xml!r}"
    print("[PASS] test_dump_hierarchy_empty_on_both_failures")


def test_disable_animations_calls_three_settings():
    """disable_animations issues `settings put global <key> 0` for all three."""
    from stage3_walk import u2_helper

    calls = []
    def fake_run(args, **kw):
        calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        return r

    with patch.object(u2_helper.subprocess, "run", side_effect=fake_run):
        ok = u2_helper.disable_animations("emulator-5554")

    assert ok is True
    keys_touched = {c[-2] for c in calls if "settings" in c}
    assert keys_touched == {
        "window_animation_scale",
        "transition_animation_scale",
        "animator_duration_scale",
    }, f"unexpected keys: {keys_touched}"
    # All set to 0
    for c in calls:
        if "settings" in c and "put" in c:
            assert c[-1] == "0", f"expected value 0, got {c[-1]}"
    print("[PASS] test_disable_animations_calls_three_settings")


def test_restore_animations_sets_scales_back_to_one():
    """restore_animations sets all three scales back to 1."""
    from stage3_walk import u2_helper

    calls = []
    def fake_run(args, **kw):
        calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        return r

    with patch.object(u2_helper.subprocess, "run", side_effect=fake_run):
        u2_helper.restore_animations("emulator-5554")

    for c in calls:
        if "settings" in c and "put" in c:
            assert c[-1] == "1", f"expected value 1 for restore, got {c[-1]}"
    print("[PASS] test_restore_animations_sets_scales_back_to_one")


if __name__ == "__main__":
    test_dump_hierarchy_returns_u2_xml_when_connected()
    test_dump_hierarchy_falls_back_to_cli_on_u2_connect_failure()
    test_dump_hierarchy_empty_on_both_failures()
    test_disable_animations_calls_three_settings()
    test_restore_animations_sets_scales_back_to_one()
    print()
    print("=== ALL U2_HELPER TESTS PASSED ===")
