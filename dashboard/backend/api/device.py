"""Per-device interactive control endpoints — tap / key / text / swipe /
screenshot / connected-device list. All subprocess calls delegate through
services/adb_service.py so the serial is always validated.

Extracted from server.py (refactor Step 2.4).
"""

from __future__ import annotations

import re
import subprocess

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from dashboard.backend.services.adb_service import _adb_prefix, _get_first_device

router = APIRouter()


@router.get("/api/device")
async def get_device_info():
    """Get connected ADB device info (one entry per device in 'device' state)."""
    try:
        result = subprocess.run(
            ["adb", "devices", "-l"], capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.strip().split("\n")[1:]
        devices = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append({
                    "serial": parts[0],
                    "info": " ".join(parts[2:]),
                })
        return {"connected": len(devices) > 0, "devices": devices}
    except Exception:
        return {"connected": False, "devices": [], "error": "ADB not available"}


@router.post("/api/device/tap")
async def device_tap(x: int, y: int, serial: str = ""):
    """Forward a tap to the connected device (used by paused-session live input)."""
    try:
        subprocess.run(
            _adb_prefix(serial) + ["shell", "input", "tap", str(x), str(y)],
            capture_output=True, timeout=4,
        )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(503, f"tap failed: {e}")


@router.post("/api/device/key")
async def device_key(code: str, serial: str = ""):
    """Send a key event (BACK / HOME / ENTER / etc). Safe-listed to prevent abuse."""
    allowed = {
        "BACK", "HOME", "ENTER", "TAB", "ESCAPE", "DEL", "DPAD_UP", "DPAD_DOWN",
        "DPAD_LEFT", "DPAD_RIGHT", "MENU", "RECENT_APPS", "VOLUME_UP", "VOLUME_DOWN",
        "POWER", "APP_SWITCH", "SPACE",
    }
    if code not in allowed:
        raise HTTPException(400, f"Key not allowed. Permitted: {sorted(allowed)}")
    try:
        subprocess.run(
            _adb_prefix(serial) + ["shell", "input", "keyevent", f"KEYCODE_{code}"],
            capture_output=True, timeout=4,
        )
        return {"ok": True, "code": code}
    except Exception as e:
        raise HTTPException(503, f"key failed: {e}")


@router.post("/api/device/text")
async def device_text(s: str, serial: str = ""):
    """Type a text string into the currently-focused input field."""
    if not s:
        return {"ok": True, "empty": True}
    safe = s.replace(" ", "%s").replace('"', '\\"')
    try:
        subprocess.run(
            _adb_prefix(serial) + ["shell", "input", "text", safe],
            capture_output=True, timeout=6,
        )
        return {"ok": True, "len": len(s)}
    except Exception as e:
        raise HTTPException(503, f"text failed: {e}")


@router.post("/api/device/swipe")
async def device_swipe(x1: int, y1: int, x2: int, y2: int, ms: int = 300, serial: str = ""):
    """Swipe from (x1,y1) to (x2,y2) over ms milliseconds."""
    try:
        subprocess.run(
            _adb_prefix(serial) + [
                "shell", "input", "swipe",
                str(x1), str(y1), str(x2), str(y2), str(ms),
            ],
            capture_output=True, timeout=6,
        )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(503, f"swipe failed: {e}")


@router.get("/api/device/screenshot")
async def get_device_screenshot(serial: str = ""):
    """Live mirror of the current device screen.

    Streams PNG via `adb exec-out screencap -p` — no intermediate file on disk,
    doesn't collide with the walker's own screencap temp files.

    When multiple devices are attached, `adb exec-out` fails without `-s` —
    the caller (frontend LiveDeviceMirror) passes the serial from the
    user-selected dropdown. Falls back to `_get_first_device()` if unspecified.
    """
    # Validate serial format to prevent argv injection into `adb -s`
    if serial and not re.match(r"^[A-Za-z0-9._\-]{1,64}$", serial):
        raise HTTPException(400, "Invalid serial format")
    chosen = serial or _get_first_device()
    args = ["adb"]
    if chosen:
        args += ["-s", chosen]
    args += ["exec-out", "screencap", "-p"]
    try:
        r = subprocess.run(args, capture_output=True, timeout=4)
        if r.returncode == 0 and r.stdout and r.stdout[:4] == b"\x89PNG":
            return Response(
                content=r.stdout,
                media_type="image/png",
                headers={
                    "Cache-Control": "no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
    except Exception:
        pass
    raise HTTPException(503, "Cannot capture device screen")
