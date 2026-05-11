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
from dashboard.backend.services.adb_service import _get_first_device
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
        # P1.3 (2026-04-29): ScreenMap quality 요약 — ANNOTATED 잡에만 의미 있음.
        # 매번 validate_graph 돌리면 N 잡 × 매 폴링 → CPU 낭비. 따라서
        # screen_map.json.metadata 에 이미 저장된 값(P0.1 refresh 결과)을 읽음.
        quality = None
        screenmap_path = tour_dir / "output" / "screen_map.json"
        if screenmap_path.exists():
            try:
                screenmap = json.loads(screenmap_path.read_text(encoding="utf-8"))
                md = screenmap.get("screen_map", {}).get("metadata", {}) or {}
                quality = {
                    "total_nodes": md.get("total_nodes", 0),
                    "total_edges": md.get("total_edges", 0),
                    "actionable_nodes": md.get("actionable_nodes", 0),
                    "plannable_nodes": md.get("plannable_nodes", 0),
                    # P1 (2026-05-03): user-facing 분모 + actionable
                    "relevant_total": md.get("relevant_total", 0),
                    "relevant_actionable": md.get("relevant_actionable", 0),
                    "reachable_count": md.get("reachable_count", 0),
                    "validation_issues": md.get("validation_issues", 0),
                    "high_issues": (md.get("issue_severity") or {}).get("high", 0),
                    "activity_coverage": md.get("activity_coverage"),
                    "completeness": md.get("completeness"),
                }
            except Exception:
                pass

        # Device the tour is bound to — prefer the in-memory slot (definitely
        # alive right now) and fall back to whatever was last persisted in
        # state.json (idle / completed tours).
        active_serial = tour_store.find_device_for_tour(tour_dir.name)
        device_serial = active_serial or state.get("device_serial", "")

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
            "device_serial": device_serial,
            "device_active": active_serial is not None,
            "quality": quality,  # null 또는 8 필드 객체 (additive)
        })
    tours.sort(
        key=lambda j: (j.get("started_at") or 0, j.get("tour_id") or ""),
        reverse=True,
    )
    return {"tours": tours}


# ─── Lifecycle ─────────────────────────────────────────────────────

@router.post("/api/tours/{tour_id}/run")
async def run_pipeline(tour_id: str, device_serial: str = "", from_stage: int = 0):
    """Start pipeline execution. One tour per ADB device serial.

    Two tours targeting different devices may run in parallel; two tours
    targeting the same serial are rejected with HTTP 409.

    Args:
        device_serial: ADB serial. 비우면 첫 번째 attached 디바이스로 즉시
            resolve — 그래야 두 클라이언트가 동시에 빈 값으로 들어와도
            같은 디바이스 두 번 점유 시도가 잡힘.
        from_stage: 0=처음부터(default), 1=stage1, 2=stage2, 3=stage3, 4=stage4,
            5=stage5(LLM only — 자동 .bak 백업), 6=stage6(ScreenMap 빌드만).
            ANNOTATED 인 tour 도 from_stage 지정하면 재실행 가능.
    """
    tour_dir = _safe_tour_dir(tour_id)
    state_path = tour_dir / "pipeline_state.json"
    if not state_path.exists():
        raise HTTPException(404, "Tour state not found")

    if device_serial and not re.match(r"^[A-Za-z0-9._\-]{1,64}$", device_serial):
        raise HTTPException(400, "Invalid device_serial format")

    if from_stage < 0 or from_stage > 6:
        raise HTTPException(400, "from_stage must be 0..6")

    state = json.loads(state_path.read_text(encoding="utf-8"))
    # ANNOTATED 라도 from_stage 지정하면 재실행 허용 (resume from specific stage)
    allowed_screens = ("UPLOADED", "FAILED", "SCREENMAP_GENERATED", "CANCELLED")
    if state["stage"] not in allowed_screens and from_stage == 0:
        raise HTTPException(409, f"Tour already running (stage: {state['stage']})")
    if from_stage > 0 and state["stage"] not in allowed_screens + ("ANNOTATED",):
        raise HTTPException(409, f"Cannot resume — tour currently {state['stage']}")

    # from_stage validation: prerequisite stage outputs 존재해야
    if from_stage >= 2 and not (tour_dir / "apk" / "metadata.json").exists():
        raise HTTPException(400, "from_stage>=2 requires Stage 1 output (apk/metadata.json)")
    if from_stage >= 3 and not (tour_dir / "static" / "analysis.json").exists():
        raise HTTPException(400, "from_stage>=3 requires Stage 2 output (static/analysis.json)")
    if from_stage >= 4 and not (tour_dir / "dynamic" / "walk.json").exists():
        raise HTTPException(400, "from_stage>=4 requires Stage 3 output (dynamic/walk.json)")
    if from_stage >= 5 and not (tour_dir / "output" / "screen_map.json").exists():
        raise HTTPException(400, "from_stage=5 requires existing ScreenMap (Stage 6 output)")

    # Resolve "auto" → real serial BEFORE locking, otherwise two concurrent
    # /run calls with empty serial would each lock the empty-string bucket
    # and both proceed against the same first-attached device.
    resolved_serial = device_serial
    if not resolved_serial:
        resolved_serial = _get_first_device()
        if not resolved_serial:
            raise HTTPException(503, "No ADB device attached")

    with tour_store.get_lock():
        current_on_device = tour_store.get_active_tour_on(resolved_serial)
        if current_on_device is not None and current_on_device != tour_id:
            raise HTTPException(
                409,
                f"Device {resolved_serial} is busy with tour {current_on_device}. "
                "Stop it first, or pick a different device.",
            )
        # Same tour_id can also be running on a *different* device — refuse.
        existing_serial = tour_store.find_device_for_tour(tour_id)
        if existing_serial is not None and existing_serial != resolved_serial:
            raise HTTPException(
                409,
                f"Tour {tour_id} is already running on device {existing_serial}.",
            )
        tour_store.set_active_tour_on(resolved_serial, tour_id)

    # Persist resolved serial to pipeline_state.json so the dashboard can
    # show "which device" even after the tour is no longer in the in-memory
    # slot map (idle / completed tours).
    try:
        state["device_serial"] = resolved_serial
        state_path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8",
        )
    except Exception:
        pass

    t = threading.Thread(
        target=run_pipeline_sync,
        args=(tour_id, resolved_serial, from_stage),
        daemon=True,
    )
    t.start()
    return {
        "status": "started",
        "tour_id": tour_id,
        "device_serial": resolved_serial,
        "from_stage": from_stage,
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
    # Refuse delete while tour is active on ANY device.
    with tour_store.get_lock():
        if tour_store.find_device_for_tour(tour_id) is not None:
            raise HTTPException(409, "Tour is running; stop it first")
    _safe_rmtree(tour_dir, must_be_under=WORKSPACE_ROOT)
    return {"status": "deleted", "tour_id": tour_id}
