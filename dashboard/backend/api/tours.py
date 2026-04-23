"""Tour list_view + lifecycle endpoints.

Extracted from server.py (refactor Step 2.5). Every route here either
mutates process-wide concurrency state (via services/tour_store) or kicks
off a background thread running services/pipeline_service. No inline
subprocess calls — the only external I/O is one `adb shell am force-stop`
in /stop, which goes through subprocess directly (tests patch
`subprocess.run` globally so that still works).
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time

from fastapi import APIRouter, HTTPException

from dashboard.backend.paths import (
    WORKSPACE_ROOT,
    _cancel_path,
    _pause_path,
    _read_pause_info,
    _safe_tour_dir,
    _safe_rmtree,
)
from dashboard.backend.services import tour_store
from dashboard.backend.services.pipeline_service import run_pipeline_sync

router = APIRouter()


# ─── ListView ───────────────────────────────────────────────────────

@router.get("/api/tours")
async def list_tours():
    """List all pipeline tours — minimal projection for dashboard polling."""
    tours = []
    for tour_dir in sorted(WORKSPACE_ROOT.iterdir()):
        if not tour_dir.is_dir():
            continue
        state_file = tour_dir / "pipeline_state.json"
        if not state_file.exists():
            continue
        state = json.loads(state_file.read_text(encoding="utf-8"))

        framework = ""
        app_label = ""
        meta_file = tour_dir / "apk" / "metadata.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                framework = meta.get("framework", "")
                app_label = meta.get("app_label", "")
            except Exception:
                pass

        pause_info = _read_pause_info(tour_dir.name)
        tours.append({
            "tour_id": tour_dir.name,
            "stage": state.get("stage", "UNKNOWN"),
            "package_name": state.get("package_name", ""),
            "apk_filename": state.get("apk_filename", ""),
            "apk_size_mb": state.get("apk_size_mb", 0),
            "started_at": state.get("started_at", 0),
            "error": state.get("error"),
            "stages": state.get("stages", {}),
            "framework": framework,
            "paused": pause_info is not None,
            "pause_reason": (pause_info or {}).get("reason", ""),
            "pause_auto": (pause_info or {}).get("auto", False),
            "pause_since": (pause_info or {}).get("since", 0),
            "app_label": app_label,
        })
    return {"tours": tours}


# ─── Lifecycle ─────────────────────────────────────────────────────

@router.post("/api/tours/{tour_id}/run")
async def run_pipeline(tour_id: str, device_serial: str = ""):
    """Start pipeline execution for a tour. Enforces a single active tour globally.

    `device_serial` pins the pipeline to a specific ADB device (e.g.
    `emulator-5554` or a real phone serial). When omitted, the first device
    listed by `adb devices` is used — OK if only one device is attached,
    but ambiguous otherwise (real phone + emulator), so the UI should always
    pass this param when it sees multiple devices.
    """
    tour_dir = _safe_tour_dir(tour_id)
    state_path = tour_dir / "pipeline_state.json"
    if not state_path.exists():
        raise HTTPException(404, "Tour state not found")

    # Defense: serial format sanity check. Emulator: `emulator-NNNN`, real
    # device: alphanumeric + optional . and -.  Reject anything else so the
    # string can't smuggle into `adb -s <serial>` as an extra flag.
    if device_serial and not re.match(r"^[A-Za-z0-9._\-]{1,64}$", device_serial):
        raise HTTPException(400, "Invalid device_serial format")

    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state["stage"] not in ("UPLOADED", "FAILED", "SCREENMAP_GENERATED", "CANCELLED"):
        raise HTTPException(409, f"Tour already running (stage: {state['stage']})")

    with tour_store.get_lock():
        current = tour_store.get_active_tour()
        if current is not None and current != tour_id:
            raise HTTPException(
                409,
                f"Another tour is already running (tour_id={current}). "
                "Stop it first or wait for completion.",
            )
        tour_store.set_active_tour(tour_id)

    # Run in a real thread so we can track + allow cancellation.
    t = threading.Thread(
        target=run_pipeline_sync,
        args=(tour_id, device_serial),
        daemon=True,
    )
    t.start()
    return {
        "status": "started",
        "tour_id": tour_id,
        "device_serial": device_serial or "(auto)",
    }


@router.post("/api/tours/{tour_id}/pause")
async def pause_pipeline(tour_id: str, reason: str = "user-paused"):
    """Manual pause: walker loop will wait until /resume."""
    tour_dir = _safe_tour_dir(tour_id)
    if not (tour_dir / "pipeline_state.json").exists():
        raise HTTPException(404, "Tour state not found")
    payload = {"reason": reason, "auto": False, "since": time.time()}
    _pause_path(tour_id).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    return {"status": "paused", "tour_id": tour_id, **payload}


@router.post("/api/tours/{tour_id}/resume")
async def resume_pipeline(tour_id: str):
    """Resume a paused tour (manual or auto-detected)."""
    _safe_tour_dir(tour_id)  # existence check (raises 404 if missing)
    p = _pause_path(tour_id)
    if not p.exists():
        raise HTTPException(409, "Tour is not paused")
    try:
        p.unlink()
    except Exception:
        pass
    return {"status": "resumed", "tour_id": tour_id}


@router.post("/api/tours/{tour_id}/stop")
async def stop_pipeline(tour_id: str):
    """Cancel a running tour: set flag, force-stop app on device, mark state."""
    tour_dir = _safe_tour_dir(tour_id)
    state_path = tour_dir / "pipeline_state.json"
    if not state_path.exists():
        raise HTTPException(404, "Tour state not found")

    # Signal cancellation (both in-memory + file flag so walker loops pick it up)
    tour_store.signal_cancel(tour_id)
    try:
        _cancel_path(tour_id).write_text("1", encoding="utf-8")
    except Exception:
        pass

    # Force-stop target app on device (best effort)
    pkg = ""
    meta_file = tour_dir / "apk" / "metadata.json"
    if meta_file.exists():
        try:
            pkg = json.loads(meta_file.read_text(encoding="utf-8")).get("package_name", "")
        except Exception:
            pass
    if pkg:
        try:
            subprocess.run(
                ["adb", "shell", "am", "force-stop", pkg],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

    return {"status": "cancel_requested", "tour_id": tour_id, "package_stopped": pkg}


@router.delete("/api/tours/{tour_id}")
async def delete_tour(tour_id: str):
    """Delete a tour and its workspace."""
    tour_dir = _safe_tour_dir(tour_id)
    # Do not delete while tour is active
    with tour_store.get_lock():
        if tour_store.get_active_tour() == tour_id:
            raise HTTPException(409, "Tour is running; stop it first")
    _safe_rmtree(tour_dir, must_be_under=WORKSPACE_ROOT)
    return {"status": "deleted", "tour_id": tour_id}
