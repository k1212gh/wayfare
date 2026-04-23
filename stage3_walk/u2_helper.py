"""uiautomator2 bridge with CLI fallback.

Why: the stock `adb shell uiautomator dump` requires the device's UI to be
"idle" — no animations, no ticking clocks, no rotating banners. Real-world
apps almost always have something moving (ad carousels, loading spinners,
live timers), causing dump to fail with "could not get idle state". The
CLI has no way to bypass this.

`uiautomator2` runs a lightweight HTTP server on the device (installed
automatically on first `u2.connect()`) that exposes the same UiAutomation
API without the idle requirement — returns hierarchy instantly even during
animations.

This wrapper:
  1. Tries to connect via uiautomator2 on first call
  2. If connection/install fails, silently falls back to the legacy CLI path
  3. Exposes a single `dump_hierarchy(serial) -> str` function returning XML

Graceful degradation means existing devices where u2 can't install (older
Android, restricted profiles) still work via the original CLI path.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

# Cache u2 device objects by serial — connect() is expensive (ADB roundtrip,
# possibly agent install). Re-connecting per frame would destroy throughput.
_u2_devices: dict[str, Any] = {}
_u2_failed: set[str] = set()  # serials where u2 setup failed, don't retry


def _u2_available() -> bool:
    try:
        import uiautomator2  # noqa: F401
        return True
    except Exception:
        return False


def _connect_u2(serial: str):
    """Connect to device via uiautomator2. Returns device object or None."""
    if serial in _u2_failed:
        return None
    if serial in _u2_devices:
        return _u2_devices[serial]
    if not _u2_available():
        _u2_failed.add(serial)
        logger.info("uiautomator2 package unavailable — using CLI fallback")
        return None
    try:
        import uiautomator2 as u2
        d = u2.connect(serial)
        # Probe — triggers agent install if needed. Small HTTP call.
        _ = d.info
        _u2_devices[serial] = d
        logger.info("uiautomator2 connected to %s (agent ready)", serial)
        return d
    except Exception as e:
        _u2_failed.add(serial)
        logger.warning("uiautomator2 connect failed for %s: %s — CLI fallback",
                       serial, e)
        return None


def dump_hierarchy(serial: str, timeout: float = 5.0) -> str:
    """Return UI hierarchy XML, or empty string on failure.

    Tries uiautomator2 first (no idle requirement). Falls back to CLI `uiautomator
    dump` which may fail with "could not get idle state" on animated screens.
    """
    d = _connect_u2(serial)
    if d is not None:
        try:
            # compressed=True → strips padding/non-interactive containers, faster
            return d.dump_hierarchy(compressed=True, pretty=False)
        except Exception as e:
            logger.debug("u2 dump_hierarchy failed on %s: %s — CLI fallback",
                         serial, e)
            # Do NOT cache this as permanent failure — u2 can recover on next call

    # CLI fallback — original path with idle-check restriction
    try:
        subprocess.run(
            ["adb", "-s", serial, "shell",
             "uiautomator dump --compressed /sdcard/u2fb_dump.xml"],
            capture_output=True, timeout=timeout,
        )
        r = subprocess.run(
            ["adb", "-s", serial, "shell", "cat /sdcard/u2fb_dump.xml"],
            capture_output=True, timeout=timeout,
        )
        xml = (r.stdout or b"").decode("utf-8", errors="replace")
        return xml if "<hierarchy" in xml else ""
    except subprocess.TimeoutExpired:
        return ""
    except Exception as e:
        logger.debug("CLI dump fallback failed on %s: %s", serial, e)
        return ""


def disable_animations(serial: str) -> bool:
    """Turn off system animations on the device (window/transition/animator).

    These scales are multipliers for Android's built-in animations. Setting to
    0 means instant-finish, not skip. Does NOT affect app-controlled animations
    (ticking clocks, carousels) — those need u2 to dump through.

    Returns True on success. Silent no-op on failure (best-effort).
    """
    ok = True
    for key in ("window_animation_scale", "transition_animation_scale",
                "animator_duration_scale"):
        try:
            r = subprocess.run(
                ["adb", "-s", serial, "shell", "settings", "put", "global",
                 key, "0"],
                capture_output=True, timeout=5,
            )
            if r.returncode != 0:
                ok = False
        except Exception:
            ok = False
    if ok:
        logger.info("System animations disabled on %s", serial)
    else:
        logger.debug("Could not disable all animations on %s (best-effort)", serial)
    return ok


def restore_animations(serial: str) -> None:
    """Restore default animation scales (1.0) — call on walker shutdown."""
    for key in ("window_animation_scale", "transition_animation_scale",
                "animator_duration_scale"):
        try:
            subprocess.run(
                ["adb", "-s", serial, "shell", "settings", "put", "global",
                 key, "1"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
