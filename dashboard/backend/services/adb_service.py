"""ADB / emulator subprocess helpers.

Extracted from server.py (refactor Step 2.4). All functions here either
shell out to `adb` / the emulator binary or inspect device state via
`dumpsys`-style queries. No FastAPI imports except HTTPException (raised
from `_adb_prefix` for serial validation, so callers can propagate it up).

Tests in tests/test_server_hardening.py patch `subprocess.run` via
`patch.object(subprocess, "run", …)`; since `subprocess` is a singleton
module, that patch covers calls made from here too.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from fastapi import HTTPException


# ─── ADB command prefix ─────────────────────────────────────────────

def _adb_prefix(serial: str) -> list[str]:
    """Build `adb -s <serial>` prefix, validating the serial to block
    argv injection via `?serial=-foo`. Falls back to bare `adb` when empty."""
    if serial:
        if not re.match(r"^[A-Za-z0-9._\-]{1,64}$", serial):
            raise HTTPException(400, "Invalid serial format")
        return ["adb", "-s", serial]
    return ["adb"]


# ─── Device discovery ──────────────────────────────────────────────

def _get_first_device() -> str:
    """Return the first ADB serial in 'device' state, or '' if none.

    Used only as a fallback when the caller didn't specify a device — prefer
    passing an explicit serial when multiple devices are attached.
    """
    try:
        result = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                return parts[0]
    except Exception:
        pass
    return ""


def _check_adb_device() -> bool:
    """True if at least one ADB device is in 'device' state."""
    try:
        result = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.strip().split("\n")[1:]
        for line in lines:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                return True
    except Exception:
        pass
    return False


# ─── Emulator discovery + status ───────────────────────────────────

def _find_emulator_bin() -> str | None:
    """Resolve the `emulator` binary path. None if Android SDK isn't installed."""
    found = shutil.which("emulator")
    if found:
        return found
    # Windows default SDK location
    candidate = (Path(os.path.expanduser("~")) / "AppData" / "Local"
                 / "Android" / "Sdk" / "emulator" / "emulator.exe")
    if candidate.exists():
        return str(candidate)
    # Linux / mac default
    for env_var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = os.environ.get(env_var)
        if root:
            for ext in ("", ".exe"):
                p = Path(root) / "emulator" / f"emulator{ext}"
                if p.exists():
                    return str(p)
    return None


def _running_avd_name(serial: str) -> str | None:
    """Ask an online emulator for its AVD name. Returns None on failure."""
    try:
        r = subprocess.run(
            ["adb", "-s", serial, "emu", "avd", "name"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0 and r.stdout:
            # Output: first line is the AVD name, second line is "OK"
            return r.stdout.strip().splitlines()[0].strip()
    except Exception:
        pass
    return None


def _list_running_emulators() -> list[dict]:
    """Return list of {serial, avd, state} for EVERY emulator-*, including offline."""
    try:
        r = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return []
    running = []
    for line in r.stdout.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0].startswith("emulator-"):
            state = parts[1]  # "device" | "offline" | "unauthorized"
            avd = _running_avd_name(parts[0]) or "" if state == "device" else ""
            running.append({"serial": parts[0], "avd": avd, "state": state})
    return running


def _boot_completed(serial: str) -> bool:
    """Check if an online emulator has finished booting."""
    try:
        r = subprocess.run(
            ["adb", "-s", serial, "shell", "getprop", "sys.boot_completed"],
            capture_output=True, text=True, timeout=3,
        )
        return r.stdout.strip() == "1"
    except Exception:
        return False
