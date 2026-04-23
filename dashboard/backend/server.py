"""FastAPI server for the ScreenAtlas dashboard."""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# uvicorn sets up its own loggers but doesn't configure the root logger, so
# `logger.info(...)` from our application code (stage3/5/6 etc) stays silent
# unless we add a handler. We install one that writes to stderr at INFO level,
# which uvicorn captures into its stdout pipe → our backend.log.
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=False,  # don't clobber uvicorn's access/error handlers if re-imported
    )
    # Silence noisy libraries
    for noisy in ("androguard", "androguard.core", "PIL", "httpx", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

app = FastAPI(title="ScreenAtlas", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Path + filesystem helpers moved to paths.py (refactor Step 2). Re-exported
# here so existing imports `from dashboard.backend.server import _safe_tour_dir`
# etc. keep working without churn across the codebase/tests.
from dashboard.backend.paths import (  # noqa: E402  (re-export shim)
    WORKSPACE_ROOT,
    _SAFE_ID,
    _safe_tour_dir,
    _within,
    _safe_rmtree,
    _cancel_path,
    _pause_path,
    _read_pause_info,
)

# ─── Concurrency + cancellation state ─────────────────────
# Only one pipeline runs at a time (single ADB device assumption).
# These remain here (not in paths.py) because they're mutable process-level
# state; paths.py is pure functions. Will move to services/tour_store.py in
# a later refactor increment.
_tour_lock = threading.Lock()
_active_tour: str | None = None
_cancel_events: dict[str, threading.Event] = {}


def _is_cancelled(tour_id: str) -> bool:
    """Check both in-memory event and on-disk flag. Kept here because it
    depends on `_cancel_events` (process-level state)."""
    ev = _cancel_events.get(tour_id)
    if ev is not None and ev.is_set():
        return True
    return _cancel_path(tour_id).exists()


# ─── APK Upload ───────────────────────────────────────────
# Routes moved to dashboard/backend/api/upload.py — included on `app` below.

from dashboard.backend.api.upload import router as _upload_router  # noqa: E402
app.include_router(_upload_router)


# ─── Pipeline Execution ──────────────────────────────────

def _run_pipeline_sync(tour_id: str, device_serial: str = ""):
    """Run pipeline in background thread. Acquires global lock; releases on exit.

    `device_serial` (optional) pins walk to a specific ADB device.
    When empty, falls back to `_get_first_device()` which picks whichever ADB
    lists first — problematic when multiple devices are attached (e.g. real
    phone + emulator). Callers should prefer passing an explicit serial.
    """
    global _active_tour
    import sys
    project_root = Path(__file__).parent.parent.parent
    state_path = WORKSPACE_ROOT / tour_id / "pipeline_state.json"

    # Register cancel event for this tour
    _cancel_events[tour_id] = threading.Event()
    # Clear any prior cancel/pause flag files from an aborted run
    for p in (_cancel_path(tour_id), _pause_path(tour_id)):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass

    stage_timers: dict[str, float] = {}  # stage_name → start time

    def update_stage(stage: str, error: str | None = None, **extra):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = stage
        state["error"] = error
        state["updated_at"] = time.time()
        state.update(extra)

        # Per-stage tracking
        stages = state.setdefault("stages", {})
        STAGE_NAMES = ["stage1", "stage2", "stage3", "stage4", "stage5", "stage6"]
        STAGE_MAP = {
            "PREPROCESSING": "stage1", "STATIC_ANALYZING": "stage2", "STATIC_DONE": "stage2",
            "WALKING": "stage3", "WALK_DONE": "stage3",
            "PREPROCESSING_DATA": "stage4", "CARDS_READY": "stage4",
            "BUILDING_SCREENMAP": "stage6", "SCREENMAP_GENERATED": "stage6",
            # ScreenMap-first: stage5 runs AFTER stage6 and only if API key is valid
            "LLM_ANNOTATING": "stage5", "ANNOTATED": "stage5",
            # Legacy names retained for back-compat
            "LLM_ANALYZING": "stage5", "ANALYSIS_DONE": "stage5",
        }
        current = STAGE_MAP.get(stage, "")

        for sn in STAGE_NAMES:
            if sn not in stages:
                stages[sn] = {"status": "pending", "detail": ""}

        if current:
            # Start timer
            if current not in stage_timers:
                stage_timers[current] = time.time()
            stages[current]["status"] = "running"
            stages[current]["detail"] = extra.get("detail", stages[current].get("detail", ""))

        # Mark completed stages
        if stage in ("STATIC_DONE", "STATIC_ANALYZING"):
            if "stage1" in stage_timers:
                stages["stage1"]["status"] = "done"
                stages["stage1"]["duration_ms"] = round((time.time() - stage_timers["stage1"]) * 1000)
        if stage in ("WALK_DONE", "WALKING"):
            stages["stage2"]["status"] = "done"
            if "stage2" in stage_timers:
                stages["stage2"]["duration_ms"] = round((time.time() - stage_timers["stage2"]) * 1000)
        if stage == "CARDS_READY":
            stages["stage3"]["status"] = "done"
            stages["stage4"]["status"] = "done"
        if stage == "SCREENMAP_GENERATED":
            # Wireframe ScreenMap is ready — stage6 done. stage5 may still be pending/running (enrichment after).
            stages["stage6"]["status"] = "done"
            if "stage6" in stage_timers and "duration_ms" not in stages["stage6"]:
                stages["stage6"]["duration_ms"] = round((time.time() - stage_timers["stage6"]) * 1000)
        if stage in ("ANALYSIS_DONE", "ANNOTATED"):
            stages["stage5"]["status"] = "done"
            if "stage5" in stage_timers and "duration_ms" not in stages["stage5"]:
                stages["stage5"]["duration_ms"] = round((time.time() - stage_timers["stage5"]) * 1000)
            # Mark every still-running stage done at final terminal
            for sn in STAGE_NAMES:
                if stages[sn]["status"] == "running":
                    stages[sn]["status"] = "done"
        if stage == "FAILED":
            for sn in STAGE_NAMES:
                if stages[sn]["status"] == "running":
                    stages[sn]["status"] = "failed"

        state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        apk_path = state["apk_path"]

        sys.path.insert(0, str(project_root))

        # Initialize tracing (optional — don't block pipeline if it fails)
        tracer = None
        try:
            from tracing import TraceLogger
            tracer = TraceLogger(
                trace_file=WORKSPACE_ROOT / tour_id / "output" / "traces.jsonl",
                trace_id=tour_id,
            )
        except Exception:
            pass

        # Stage 1: Preprocess
        update_stage("PREPROCESSING")
        from config import PipelineConfig
        # Prefer the caller-specified serial (from /run?device_serial=...),
        # fallback to the first ADB-listed device if unspecified.
        device = device_serial or _get_first_device()
        is_emu = device.startswith("emulator") if device else True
        config = PipelineConfig(
            apk_path=apk_path, tour_id=tour_id,
            workspace_root=str(WORKSPACE_ROOT),
            device_serial=device or "emulator-5554",
            is_emulator=is_emu,
        )
        config.ensure_dirs()

        # Retry-aware: skip stages whose output file already exists.
        # FAILED/CANCELLED runs can be re-triggered and pick up from failure point.
        force_rerun = os.environ.get("PIPELINE_FORCE_RERUN", "").lower() in ("1", "true", "yes")

        meta_path = config.apk_dir / "metadata.json"
        if not force_rerun and meta_path.exists():
            logger.info("Stage1 output exists — skipping (resume)")
        else:
            from stage1_install import run_stage1
            run_stage1(config)
        pkg = ""
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            pkg = meta.get("package_name", "")
        update_stage("STATIC_ANALYZING", package_name=pkg, detail=f"Package: {pkg}")

        # Stage 2: Static analysis
        static_path = config.static_dir / "analysis.json"
        if not force_rerun and static_path.exists():
            logger.info("Stage2 output exists — skipping (resume)")
        else:
            from stage2_manifest import run_stage2
            run_stage2(config)
        act_count = 0
        static_info = {}
        if static_path.exists():
            static_info = json.loads(static_path.read_text(encoding="utf-8"))
            act_count = len(static_info.get("activities", []))
        update_stage("STATIC_DONE", detail=f"{act_count} activities")

        # Stage 2.5: Build static wireframe ScreenMap immediately so the dashboard has
        # *something* to render before dynamic walk is done.
        # We do NOT update_stage here — the pipeline is still in STATIC_DONE → WALKING.
        # The dashboard simply sees screen_map.json appear earlier.
        try:
            from stage6_screenmap.wireframe_builder import write_wireframe
            write_wireframe(config, static_info, meta)
            logger.info("Wrote static wireframe ScreenMap (%d nodes)", act_count)
        except Exception as e:
            logger.warning("Wireframe ScreenMap build failed: %s", e)

        # Stage 3: Walk (TapWalker or DroidBot)
        has_walk = False
        has_device = _check_adb_device()

        if has_device:
            update_stage("WALKING", detail="TapWalker running...")
            from stage3_walk import run_stage3
            run_stage3(config)
            has_walk = (config.dynamic_dir / "walk.json").exists()
            if has_walk:
                exp = json.loads((config.dynamic_dir / "walk.json").read_text(encoding="utf-8"))
                n_screens = len(exp.get("states", []))
                n_trans = len(exp.get("transitions", []))
                update_stage("WALK_DONE", detail=f"{n_screens} states, {n_trans} transitions")
            else:
                update_stage("WALK_DONE", detail="No states found")
        else:
            update_stage("STATIC_DONE", error="No device connected — walk skipped")

        if has_walk:
            # ScreenMap-first: walk → preprocessing → wireframe ScreenMap → (optional) LLM enrichment
            update_stage("PREPROCESSING_DATA")
            from stage4_screens import run_stage4
            run_stage4(config)
            update_stage("CARDS_READY")

            # Stage 6: build wireframe ScreenMap from screen_cards + walk (no LLM needed)
            update_stage("BUILDING_SCREENMAP")
            from stage6_screenmap import run_stage6
            run_stage6(config)
            update_stage("SCREENMAP_GENERATED")

            # Stage 5: LLM enrichment (reads wireframe ScreenMap, annotates in place)
            llm_mode = os.environ.get("LLM_MODE", "api")
            stage5_mode = os.environ.get("LLM_STAGE5_MODE", "screenmap_annotate")
            api_key = config.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            has_valid_key = api_key and "PLACEHOLDER" not in api_key

            if has_valid_key or llm_mode == "cli":
                try:
                    update_stage(
                        "LLM_ANNOTATING",
                        detail=f"Anthropic {llm_mode} ({stage5_mode})...",
                    )
                    from stage5_annotate import run_stage5
                    run_stage5(config, mode=stage5_mode)
                    update_stage("ANNOTATED")
                except Exception as e:
                    # LLM failure must NOT invalidate the wireframe ScreenMap.
                    # Distinguish auth rejection (key present but invalid) from
                    # other runtime failures (quota, model bug, JSON parse, etc.)
                    # so the operator can tell them apart without grepping.
                    msg = str(e)
                    if "authentication" in msg.lower():
                        logger.warning(
                            "LLM auth rejected — key is present but Anthropic "
                            "returned 401. Keeping wireframe ScreenMap.",
                        )
                        err_label = "LLM auth rejected — verify ANTHROPIC_API_KEY"
                    else:
                        logger.warning(
                            "LLM enrichment failed, keeping wireframe ScreenMap: %s",
                            msg[:300],
                        )
                        err_label = f"LLM enrich failed: {msg[:200]}"
                    update_stage("SCREENMAP_GENERATED", error=err_label)
            else:
                # By-design skip: no key configured. Not an error.
                logger.info(
                    "LLM enrichment skipped (no valid ANTHROPIC_API_KEY) — "
                    "using wireframe ScreenMap as final output",
                )
        else:
            # No device/walk → static-only ScreenMap from manifest activities
            update_stage("BUILDING_SCREENMAP")
            _build_static_screenmap(config)
            update_stage("SCREENMAP_GENERATED")

    except Exception as e:
        logger.exception("Pipeline failed for tour %s", tour_id)
        if _is_cancelled(tour_id):
            update_stage("CANCELLED", error="Cancelled by user")
        else:
            update_stage("FAILED", error=str(e)[:500])
    finally:
        # Release global lock + cleanup cancel state
        with _tour_lock:
            if _active_tour == tour_id:
                _active_tour = None
        _cancel_events.pop(tour_id, None)
        for p in (_cancel_path(tour_id), _pause_path(tour_id)):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


def _build_static_screenmap(config):
    """Build a basic ScreenMap from static analysis only (no DroidBot walk).

    Edges come from the DEX-extracted transition graph (stage2's
    `transition_graph.json`). If that file is absent we emit zero edges —
    previously we fell back to an entry→everyone star topology which
    polluted the dashboard with meaningless edges and confused downstream
    validators. Better to show honest "nodes only" than fake structure.
    """
    from datetime import datetime, timezone

    # Use absolute paths to avoid CWD issues
    static_dir = Path(config.static_dir).resolve()
    static_path = static_dir / "analysis.json"
    transition_path = static_dir / "transition_graph.json"
    meta_path = Path(config.apk_dir).resolve() / "metadata.json"

    static_info = json.loads(static_path.read_text(encoding="utf-8")) if static_path.exists() else {}
    transition_info = json.loads(transition_path.read_text(encoding="utf-8")) if transition_path.exists() else {}
    metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    activities = static_info.get("activities", [])
    entry = static_info.get("entry_activity", "")
    activity_names = {a.get("name", "") for a in activities if a.get("name")}

    nodes = []
    for act in activities:
        name = act.get("name", "")
        short = name.rsplit(".", 1)[-1] if "." in name else name
        is_entry = act.get("is_launcher", False)
        nodes.append({
            "screen_id": name,
            "activity": name,
            "label": short,
            "functional_category": "home" if is_entry else "other",
            "screen_purpose": f"{'App entry point' if is_entry else 'Activity'}: {short}",
            "params": {"inputs": [], "outputs": [], "displays": []},
            "widgets": [],
            "screenshot_ref": "",
            "confidence": "low",
        })

    # Edges: prefer evidence-backed static transitions; coalescee by (from,to,trigger).
    edges = []
    seen_keys: set[tuple] = set()
    for i, t in enumerate(transition_info.get("transitions", [])):
        src = t.get("source", "")
        dst = t.get("target", "")
        trigger = t.get("trigger", "startActivity")
        # Only emit edges between known activities — transitions can point to
        # helper classes or non-Activity targets after multi-hop resolution.
        if not src or not dst or src == dst:
            continue
        if src not in activity_names or dst not in activity_names:
            continue
        key = (src, dst, trigger)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        edges.append({
            "edge_id": f"e_static_{len(edges):03d}",
            "from": src,
            "to": dst,
            "trigger_action": "intent",
            "trigger_widget": "",
            "condition": f"static:{t.get('kind', 'direct')}",
            "passed_params": [],
            "returned_params": [],
        })

    screenmap = {
        "screen_map": {
            "app_name": metadata.get("package_name", "").split(".")[-1],
            "package_name": metadata.get("package_name", ""),
            "version": metadata.get("version_name", ""),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "graph": {
                "entry_node": entry,
                "nodes": nodes,
                "edges": edges,
                "edge_groups": [],
                "global_params": {},
            },
            "metadata": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "coverage_ratio": 0.0,
                "orphan_nodes": 0,
                "dead_end_nodes": 0,
                "validation_issues": 0,
            },
        }
    }

    output_path = config.output_dir / "screen_map.json"
    output_path.write_text(json.dumps(screenmap, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Built static-only ScreenMap: %d nodes, %d edges", len(nodes), len(edges))


def _get_first_device() -> str:
    """Get the serial of the first connected ADB device."""
    try:
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                return parts[0]
    except Exception:
        pass
    return ""


def _check_adb_device() -> bool:
    try:
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().split("\n")[1:]
        return any("device" in line for line in lines)
    except Exception:
        return False


@app.post("/api/tours/{tour_id}/run")
async def run_pipeline(tour_id: str, device_serial: str = ""):
    """Start pipeline execution for a tour. Enforces a single active tour globally.

    `device_serial` pins the pipeline to a specific ADB device (e.g.
    `emulator-5554` or a real phone serial). When omitted, the first device
    listed by `adb devices` is used — OK if only one device is attached,
    but ambiguous otherwise (real phone + emulator), so the UI should always
    pass this param when it sees multiple devices.
    """
    global _active_tour
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

    with _tour_lock:
        if _active_tour is not None and _active_tour != tour_id:
            raise HTTPException(
                409,
                f"Another tour is already running (tour_id={_active_tour}). "
                "Stop it first or wait for completion.",
            )
        _active_tour = tour_id

    # Run in a real thread so we can track + allow cancellation.
    t = threading.Thread(
        target=_run_pipeline_sync,
        args=(tour_id, device_serial),
        daemon=True,
    )
    t.start()
    return {"status": "started", "tour_id": tour_id, "device_serial": device_serial or "(auto)"}


@app.post("/api/tours/{tour_id}/pause")
async def pause_pipeline(tour_id: str, reason: str = "user-paused"):
    """Manual pause: walker loop will wait until /resume."""
    tour_dir = _safe_tour_dir(tour_id)
    if not (tour_dir / "pipeline_state.json").exists():
        raise HTTPException(404, "Tour state not found")
    payload = {"reason": reason, "auto": False, "since": time.time()}
    _pause_path(tour_id).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {"status": "paused", "tour_id": tour_id, **payload}


@app.post("/api/tours/{tour_id}/resume")
async def resume_pipeline(tour_id: str):
    """Resume a paused tour (manual or auto-detected)."""
    tour_dir = _safe_tour_dir(tour_id)
    p = _pause_path(tour_id)
    if not p.exists():
        raise HTTPException(409, "Tour is not paused")
    try:
        p.unlink()
    except Exception:
        pass
    return {"status": "resumed", "tour_id": tour_id}


@app.post("/api/tours/{tour_id}/stop")
async def stop_pipeline(tour_id: str):
    """Cancel a running tour: set flag, force-stop app on device, mark state."""
    tour_dir = _safe_tour_dir(tour_id)
    state_path = tour_dir / "pipeline_state.json"
    if not state_path.exists():
        raise HTTPException(404, "Tour state not found")

    # Signal cancellation (both in-memory + file flag so walker loops pick it up)
    ev = _cancel_events.get(tour_id)
    if ev is not None:
        ev.set()
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


@app.delete("/api/tours/{tour_id}")
async def delete_tour(tour_id: str):
    """Delete a tour and its workspace."""
    tour_dir = _safe_tour_dir(tour_id)
    # Do not delete while tour is active
    with _tour_lock:
        if _active_tour == tour_id:
            raise HTTPException(409, "Tour is running; stop it first")
    _safe_rmtree(tour_dir, must_be_under=WORKSPACE_ROOT)
    return {"status": "deleted", "tour_id": tour_id}


# ─── Emulator Control ─────────────────────────────────────

def _find_emulator_bin() -> str | None:
    """Resolve the `emulator` binary path."""
    found = shutil.which("emulator")
    if found:
        return found
    # Windows default SDK location
    candidate = Path(os.path.expanduser("~")) / "AppData" / "Local" / "Android" / "Sdk" / "emulator" / "emulator.exe"
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


@app.get("/api/emulator/avds")
async def list_avds():
    """List available AVDs."""
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
    avds = [line.strip() for line in result.stdout.splitlines() if line.strip() and not line.startswith("INFO")]
    return {"available": True, "avds": avds, "emulator_path": emu}


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


_EMU_LOG_DIR = WORKSPACE_ROOT / "_emulator_logs"


@app.post("/api/emulator/start")
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


@app.get("/api/emulator/status")
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
            e["state"] = "online_boot_complete" if _boot_completed(e["serial"]) else "online_booting"
    return {"emulators": emus}


@app.post("/api/emulator/kill")
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


# ─── Device Info ──────────────────────────────────────────

@app.get("/api/device")
async def get_device_info():
    """Get connected ADB device info."""
    try:
        result = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True, timeout=5)
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


def _adb_prefix(serial: str) -> list[str]:
    """Build `adb -s <serial>` prefix, validating the serial to block
    argv injection via `?serial=-foo`. Falls back to bare `adb` when empty."""
    if serial:
        if not re.match(r"^[A-Za-z0-9._\-]{1,64}$", serial):
            raise HTTPException(400, "Invalid serial format")
        return ["adb", "-s", serial]
    return ["adb"]


@app.post("/api/device/tap")
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


@app.post("/api/device/key")
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


@app.post("/api/device/text")
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


@app.post("/api/device/swipe")
async def device_swipe(x1: int, y1: int, x2: int, y2: int, ms: int = 300, serial: str = ""):
    """Swipe from (x1,y1) to (x2,y2) over ms milliseconds."""
    try:
        subprocess.run(
            _adb_prefix(serial) + ["shell", "input", "swipe",
             str(x1), str(y1), str(x2), str(y2), str(ms)],
            capture_output=True, timeout=6,
        )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(503, f"swipe failed: {e}")


@app.get("/api/device/screenshot")
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
            from fastapi.responses import Response
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


# ─── Existing endpoints ──────────────────────────────────

@app.get("/api/tours")
async def list_tours():
    """List all pipeline tours."""
    tours = []
    for tour_dir in sorted(WORKSPACE_ROOT.iterdir()):
        if not tour_dir.is_dir():
            continue
        state_file = tour_dir / "pipeline_state.json"
        if state_file.exists():
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


# Read-only tour-scoped endpoints (graph / plan / path / state / report /
# traces / static / cache-stats / walk-live / screenshot) moved to
# dashboard/backend/api/graph.py (refactor Step 2.3).
from dashboard.backend.api.graph import router as _graph_router  # noqa: E402
app.include_router(_graph_router)


# Serve frontend static files — only in production (not dev mode with Vite)
# To enable: set SERVE_STATIC=1
import os as _os
if _os.environ.get("SERVE_STATIC") == "1":
    FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"
    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
