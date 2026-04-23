"""AVD / emulator lifecycle endpoints — list / start / status / kill.

Extracted from server.py (refactor Step 2.4). Device discovery + status
probing lives in `services/adb_service.py`; this file is just the HTTP
routing + launch-mode argument building.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

from dashboard.backend.paths import WORKSPACE_ROOT
from dashboard.backend.services.adb_service import (
    _boot_completed,
    _find_emulator_bin,
    _list_running_emulators,
)

router = APIRouter()

# Per-AVD log files live under the workspace root so they're easy to tail.
_EMU_LOG_DIR = WORKSPACE_ROOT / "_emulator_logs"


@router.get("/api/emulator/avds")
async def list_avds():
    """List available AVDs installed on the host."""
    emu = _find_emulator_bin()
    if not emu:
        return {"available": False, "avds": [], "error": "emulator binary not found"}
    try:
        result = subprocess.run(
            [emu, "-list-avds"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        return {"available": False, "avds": [], "error": str(e)}
    avds = [
        line.strip() for line in result.stdout.splitlines()
        if line.strip() and not line.startswith("INFO")
    ]
    return {"available": True, "avds": avds, "emulator_path": emu}


@router.post("/api/emulator/start")
async def start_emulator(avd: str, cold: bool = False, safe: bool = False):
    """Launch an AVD.

    Args:
        avd:  AVD name (e.g., Medium_Phone_API_36.1).
        cold: cold boot (ignore snapshot) — slower but recovers corrupt state.
        safe: safe mode (software renderer + no snapshot) — use when GPU hangs.
    """
    if not re.match(r"^[A-Za-z0-9_.\- ]{1,128}$", avd):
        raise HTTPException(400, "Invalid AVD name")
    emu = _find_emulator_bin()
    if not emu:
        raise HTTPException(500, "emulator binary not found — install Android SDK")

    # Duplicate guard: same AVD already online → 409
    for e in _list_running_emulators():
        if e["avd"] and e["avd"] == avd:
            raise HTTPException(
                409,
                f"AVD '{avd}' is already running on {e['serial']} ({e['state']}). "
                "Stop it first with POST /api/emulator/kill, or pick a different AVD.",
            )

    if safe:
        args = [emu, "-avd", avd, "-gpu", "swiftshader_indirect", "-no-snapshot"]
    elif cold:
        args = [emu, "-avd", avd, "-gpu", "host", "-no-snapshot"]
    else:
        args = [emu, "-avd", avd, "-gpu", "host"]

    # Write emulator stdout/stderr to a log file so failures are diagnosable
    _EMU_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _EMU_LOG_DIR / f"{avd}.log"
    try:
        log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
        log_handle.write(f"\n========== {time.strftime('%Y-%m-%d %H:%M:%S')} ==========\n")
        log_handle.flush()

        creationflags = 0
        if os.name == "nt":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — survive server restart
            creationflags = 0x00000008 | 0x00000200
        subprocess.Popen(
            args,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to start emulator: {e}")

    return {
        "status": "starting",
        "avd": avd,
        "cold_boot": cold,
        "safe_mode": safe,
        "log_file": str(log_path),
    }


@router.get("/api/emulator/status")
async def emulator_status():
    """Detailed status of every emulator-* device.

    state:
      - "online_boot_complete": fully booted, ready for ADB commands
      - "online_booting": ADB connected but Android still loading
      - "offline": adb sees the emulator but can't talk (common hang state)
      - "unauthorized": waiting for user to accept RSA fingerprint
    """
    emus = _list_running_emulators()
    for e in emus:
        if e["state"] == "device":
            e["state"] = (
                "online_boot_complete" if _boot_completed(e["serial"])
                else "online_booting"
            )
    return {"emulators": emus}


@router.post("/api/emulator/kill")
async def kill_emulator(serial: str = "", reset_adb: bool = True):
    """Stop an emulator, then (optionally) reset the ADB server to clear stall sessions.

    Resets ADB by default — solves the "stall offline" problem caused by
    duplicate adb installations fighting over port 5037.
    """
    if serial and not re.match(r"^emulator-\d{4,5}$", serial):
        raise HTTPException(400, "Invalid serial (must be like emulator-5554)")
    targets = [serial] if serial else [e["serial"] for e in _list_running_emulators()]

    killed = []
    for s in targets:
        try:
            subprocess.run(["adb", "-s", s, "emu", "kill"], capture_output=True, timeout=5)
            killed.append(s)
        except Exception:
            pass

    # Force-kill any surviving qemu/emulator processes (offline ones won't accept
    # `emu kill`). ONLY run the blanket `/IM` kill when the caller did NOT
    # specify a serial — otherwise we'd nuke every other running AVD too.
    # When a specific serial was given and `emu kill` already ran, that's all
    # the scope we're allowed; surviving processes for that serial are rare.
    if os.name == "nt" and not serial:
        for imagename in ("qemu-system-x86_64.exe", "qemu-system-aarch64.exe", "emulator.exe"):
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", imagename],
                    capture_output=True, timeout=5,
                )
            except Exception:
                pass

    adb_reset = False
    if reset_adb:
        try:
            subprocess.run(["adb", "kill-server"], capture_output=True, timeout=5)
            time.sleep(1)
            subprocess.run(["adb", "start-server"], capture_output=True, timeout=10)
            adb_reset = True
        except Exception:
            pass

    return {"killed": killed, "adb_reset": adb_reset}
