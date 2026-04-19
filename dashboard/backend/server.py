"""FastAPI server for the ScreenAtlas dashboard."""

import asyncio
import json
import logging
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

app = FastAPI(title="ScreenAtlas", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WORKSPACE_ROOT = (Path(__file__).parent.parent.parent / "workspace").resolve()
WORKSPACE_ROOT.mkdir(exist_ok=True)

# Track running pipelines
_running_tours: dict[str, str] = {}  # tour_id → status message


# ─── APK Upload ───────────────────────────────────────────

@app.post("/api/upload")
async def upload_apk(file: UploadFile = File(...)):
    """Upload an APK and create a new tour.

    Supports both single APK and split APK (base.apk).
    Streams in chunks for large files.
    """
    if not file.filename:
        raise HTTPException(400, "No file provided")
    if not file.filename.endswith(".apk"):
        raise HTTPException(400, "Only .apk files accepted. For split APKs, upload the base.apk")

    tour_id = uuid.uuid4().hex[:8]
    tour_dir = WORKSPACE_ROOT / tour_id
    for d in ["apk", "static", "dynamic", "analysis", "output"]:
        (tour_dir / d).mkdir(parents=True, exist_ok=True)

    # Stream-write to avoid loading entire APK into memory
    apk_path = tour_dir / "apk" / file.filename
    total_bytes = 0
    try:
        with open(apk_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                out.write(chunk)
                total_bytes += len(chunk)
    except Exception as e:
        # Cleanup on failure
        if apk_path.exists():
            try:
                apk_path.unlink()
            except OSError:
                pass
        raise HTTPException(500, f"Failed to save APK: {e}")
    finally:
        await file.close()

    # Create initial state
    state = {
        "tour_id": tour_id,
        "stage": "UPLOADED",
        "apk_path": str(apk_path.resolve()),
        "package_name": "",
        "apk_filename": file.filename,
        "apk_size_mb": round(total_bytes / 1024 / 1024, 1),
        "started_at": time.time(),
        "updated_at": time.time(),
        "error": None,
    }
    (tour_dir / "pipeline_state.json").write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"tour_id": tour_id, "status": "uploaded", "filename": file.filename}


@app.post("/api/upload-multi")
async def upload_split_apks(files: list[UploadFile] = File(...)):
    """Upload split APKs (base.apk + split_config.*.apk). Analyzes base.apk."""
    if not files:
        raise HTTPException(400, "No files provided")

    # Find base.apk
    base_file = None
    for f in files:
        if f.filename and (f.filename == "base.apk" or f.filename.endswith(".apk")):
            if base_file is None or f.filename == "base.apk":
                base_file = f

    if not base_file:
        raise HTTPException(400, "No base.apk found in uploaded files")

    tour_id = uuid.uuid4().hex[:8]
    tour_dir = WORKSPACE_ROOT / tour_id
    for d in ["apk", "static", "dynamic", "analysis", "output"]:
        (tour_dir / d).mkdir(parents=True, exist_ok=True)

    # Save all APKs
    total_bytes = 0
    for f in files:
        if f.filename and f.filename.endswith(".apk"):
            dest = tour_dir / "apk" / f.filename
            with open(dest, "wb") as out:
                while True:
                    chunk = await f.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    total_bytes += len(chunk)
            await f.close()

    # Point to base.apk for analysis
    base_path = tour_dir / "apk" / "base.apk"
    if not base_path.exists():
        # Use first APK found
        apks = list((tour_dir / "apk").glob("*.apk"))
        base_path = apks[0] if apks else base_path

    state = {
        "tour_id": tour_id,
        "stage": "UPLOADED",
        "apk_path": str(base_path.resolve()),
        "package_name": "",
        "apk_filename": f"{len(files)} split APKs",
        "apk_size_mb": round(total_bytes / 1024 / 1024, 1),
        "started_at": time.time(),
        "updated_at": time.time(),
        "error": None,
    }
    (tour_dir / "pipeline_state.json").write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"tour_id": tour_id, "status": "uploaded", "filename": f"base.apk (+{len(files)-1} splits)"}


# ─── Pipeline Execution ──────────────────────────────────

def _run_pipeline_sync(tour_id: str):
    """Run pipeline in background thread."""
    import sys, os
    project_root = Path(__file__).parent.parent.parent
    state_path = WORKSPACE_ROOT / tour_id / "pipeline_state.json"

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
            "LLM_ANALYZING": "stage5", "ANALYSIS_DONE": "stage5",
            "BUILDING_SCREENMAP": "stage6", "SCREENMAP_GENERATED": "stage6",
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
        if stage == "ANALYSIS_DONE":
            stages["stage5"]["status"] = "done"
        if stage == "SCREENMAP_GENERATED":
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
        device = _get_first_device()
        is_emu = device.startswith("emulator") if device else True
        config = PipelineConfig(
            apk_path=apk_path, tour_id=tour_id,
            workspace_root=str(WORKSPACE_ROOT),
            device_serial=device or "emulator-5554",
            is_emulator=is_emu,
        )
        config.ensure_dirs()

        from stage1_install import run_stage1
        run_stage1(config)
        meta_path = config.apk_dir / "metadata.json"
        pkg = ""
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            pkg = meta.get("package_name", "")
        update_stage("STATIC_ANALYZING", package_name=pkg, detail=f"Package: {pkg}")

        # Stage 2: Static analysis
        from stage2_manifest import run_stage2
        run_stage2(config)
        static_path = config.static_dir / "analysis.json"
        act_count = 0
        if static_path.exists():
            sa = json.loads(static_path.read_text(encoding="utf-8"))
            act_count = len(sa.get("activities", []))
        update_stage("STATIC_DONE", detail=f"{act_count} activities")

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
            # Full pipeline: walk → preprocessing → LLM → ScreenMap
            update_stage("PREPROCESSING_DATA")
            from stage4_screens import run_stage4
            run_stage4(config)
            update_stage("CARDS_READY")

            # LLM analysis
            llm_mode = os.environ.get("LLM_MODE", "api")
            api_key = config.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            has_valid_key = api_key and "PLACEHOLDER" not in api_key

            if has_valid_key:
                update_stage("LLM_ANALYZING", detail=f"Anthropic API ({llm_mode})...")
                from stage5_annotate import run_stage5
                run_stage5(config)
                update_stage("ANALYSIS_DONE")
            elif llm_mode == "cli":
                update_stage("LLM_ANALYZING", detail="Claude CLI analyzing...")
                from stage5_annotate import run_stage5
                run_stage5(config)
                update_stage("ANALYSIS_DONE")
            else:
                logger.info("Skipping LLM: API key is placeholder, will analyze when real key is set")
                update_stage("ANALYSIS_DONE", detail="LLM skipped (API key pending)")

            update_stage("BUILDING_SCREENMAP")
            from stage6_screenmap import run_stage6
            run_stage6(config)
            update_stage("SCREENMAP_GENERATED")
        else:
            # No DroidBot → build a static-only ScreenMap from manifest activities
            update_stage("BUILDING_SCREENMAP")
            _build_static_screenmap(config)
            update_stage("SCREENMAP_GENERATED")

    except Exception as e:
        logger.exception("Pipeline failed for tour %s", tour_id)
        update_stage("FAILED", error=str(e)[:500])


def _build_static_screenmap(config):
    """Build a basic ScreenMap from static analysis only (no DroidBot walk)."""
    from datetime import datetime, timezone

    # Use absolute paths to avoid CWD issues
    static_path = Path(config.static_dir).resolve() / "analysis.json"
    meta_path = Path(config.apk_dir).resolve() / "metadata.json"

    static_info = json.loads(static_path.read_text(encoding="utf-8")) if static_path.exists() else {}
    metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    activities = static_info.get("activities", [])
    entry = static_info.get("entry_activity", "")

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

    # Create edges from entry to all other activities (basic star topology)
    edges = []
    if entry:
        for i, act in enumerate(activities):
            name = act.get("name", "")
            if name != entry:
                edges.append({
                    "edge_id": f"e_static_{i:03d}",
                    "from": entry,
                    "to": name,
                    "trigger_action": "intent",
                    "trigger_widget": "",
                    "condition": "static_analysis",
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
async def run_pipeline(tour_id: str, background_tasks: BackgroundTasks):
    """Start pipeline execution for a tour."""
    state_path = WORKSPACE_ROOT / tour_id / "pipeline_state.json"
    if not state_path.exists():
        raise HTTPException(404, "Tour not found")

    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state["stage"] not in ("UPLOADED", "FAILED", "SCREENMAP_GENERATED"):
        raise HTTPException(409, f"Tour already running (stage: {state['stage']})")

    background_tasks.add_task(_run_pipeline_sync, tour_id)
    return {"status": "started", "tour_id": tour_id}


@app.delete("/api/tours/{tour_id}")
async def delete_tour(tour_id: str):
    """Delete a tour and its workspace."""
    tour_dir = WORKSPACE_ROOT / tour_id
    if not tour_dir.exists():
        raise HTTPException(404, "Tour not found")
    shutil.rmtree(tour_dir)
    return {"status": "deleted", "tour_id": tour_id}


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


@app.get("/api/device/screenshot")
async def get_device_screenshot():
    """Capture current device screen."""
    try:
        subprocess.run(["adb", "shell", "screencap", "-p", "/sdcard/sa_screen.png"],
                       capture_output=True, timeout=10)
        tmp = Path("/tmp/sa_device_screen.png")
        subprocess.run(["adb", "pull", "/sdcard/sa_screen.png", str(tmp)],
                       capture_output=True, timeout=10)
        if tmp.exists():
            return FileResponse(tmp, media_type="image/png")
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
            tours.append({
                "tour_id": tour_dir.name,
                "stage": state.get("stage", "UNKNOWN"),
                "package_name": state.get("package_name", ""),
                "apk_filename": state.get("apk_filename", ""),
                "apk_size_mb": state.get("apk_size_mb", 0),
                "started_at": state.get("started_at", 0),
                "error": state.get("error"),
                "stages": state.get("stages", {}),
            })
    return {"tours": tours}


@app.get("/api/tours/{tour_id}/graph")
async def get_graph(tour_id: str):
    graph_path = WORKSPACE_ROOT / tour_id / "output" / "screen_map.json"
    if not graph_path.exists():
        raise HTTPException(404, "Graph not found")
    return JSONResponse(json.loads(graph_path.read_text(encoding="utf-8")))


@app.get("/api/tours/{tour_id}/walk-live")
async def get_walk_live(tour_id: str):
    """Get live walk data (states + transitions so far).

    Frontend polls this during WALKING stage to show real-time graph.
    """
    walk_path = WORKSPACE_ROOT / tour_id / "dynamic" / "walk.json"
    if walk_path.exists():
        data = json.loads(walk_path.read_text(encoding="utf-8"))
        return JSONResponse({
            "status": "complete",
            "states": len(data.get("states", [])),
            "transitions": len(data.get("transitions", [])),
            "screens": data.get("stats", {}).get("unique_screens", 0),
            "nodes": [
                {"id": s.get("canonical_id", s.get("state_str", "")),
                 "activity": s.get("activity", ""),
                 "screenshot": s.get("screenshot_path", "")}
                for s in data.get("states", [])[:50]
            ],
            "edges": data.get("transitions", [])[:100],
        })

    # Check if states dir is being populated (walk in progress)
    states_dir = WORKSPACE_ROOT / tour_id / "dynamic" / "states"
    if states_dir.exists():
        state_files = sorted(states_dir.glob("state_*.json"))
        screen_files = sorted(states_dir.glob("screen_*.png"))
        return JSONResponse({
            "status": "running",
            "states": len(state_files),
            "screenshots": len(screen_files),
        })

    return JSONResponse({"status": "not_started"})


@app.get("/api/tours/{tour_id}/path")
async def find_path(tour_id: str, source: str = "", target: str = ""):
    """Find shortest path between two nodes."""
    graph_path = WORKSPACE_ROOT / tour_id / "output" / "screen_map.json"
    if not graph_path.exists():
        raise HTTPException(404, "Graph not found")
    if not source or not target:
        raise HTTPException(400, "source and target query params required")

    screenmap = json.loads(graph_path.read_text(encoding="utf-8"))

    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from stage6_screenmap.route_finder import find_shortest_path, find_all_paths

    result = find_shortest_path(screenmap, source, target)
    if not result:
        raise HTTPException(404, f"No path from {source} to {target}")

    # Also include alternatives
    alternatives = find_all_paths(screenmap, source, target, max_paths=3)

    return JSONResponse({"best": result, "alternatives": alternatives})


@app.get("/api/tours/{tour_id}/plan")
async def plan_task_endpoint(tour_id: str, task: str = ""):
    """Plan a path for a natural language task (PoG-style)."""
    if not task:
        raise HTTPException(400, "task query param required (e.g., ?task=알림 끄기)")
    graph_path = WORKSPACE_ROOT / tour_id / "output" / "screen_map.json"
    if not graph_path.exists():
        raise HTTPException(404, "Graph not found")

    screenmap = json.loads(graph_path.read_text(encoding="utf-8"))

    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from navigator import plan_task

    # Try with LLM client
    llm = None
    try:
        from stage5_annotate.llm_client import create_client
        llm = create_client()
    except Exception:
        pass

    result = plan_task(screenmap, task, llm_client=llm)
    return JSONResponse(result)


@app.get("/api/tours/{tour_id}/traces")
async def get_traces(tour_id: str):
    """Get JSONL execution traces for a tour."""
    trace_path = WORKSPACE_ROOT / tour_id / "output" / "traces.jsonl"
    if not trace_path.exists():
        return JSONResponse({"traces": []})
    traces = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                traces.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return JSONResponse({"traces": traces})


@app.get("/api/tours/{tour_id}/cache-stats")
async def get_cache_stats(tour_id: str):
    """Get UI pattern cache statistics."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from cache import WidgetCache
        cache = WidgetCache()
        return JSONResponse(cache.stats())
    except Exception as e:
        return JSONResponse({"enabled": False, "error": str(e)})


@app.get("/api/tours/{tour_id}/state")
async def get_state(tour_id: str):
    state_path = WORKSPACE_ROOT / tour_id / "pipeline_state.json"
    if not state_path.exists():
        raise HTTPException(404, "State not found")
    return JSONResponse(json.loads(state_path.read_text(encoding="utf-8")))


@app.get("/api/tours/{tour_id}/report")
async def get_report(tour_id: str):
    report_path = WORKSPACE_ROOT / tour_id / "output" / "report.json"
    if not report_path.exists():
        raise HTTPException(404, "Report not found")
    return JSONResponse(json.loads(report_path.read_text(encoding="utf-8")))


@app.get("/api/tours/{tour_id}/screenshot/{screen_id}")
async def get_screenshot(tour_id: str, screen_id: str):
    """Find screenshot for a node. Searches by:
    1. Direct file path from graph's screenshot_ref
    2. screen_id substring match in analysis/screens and dynamic/states
    3. Any PNG/JPG in dynamic/ matching the screen_id
    """
    tour_dir = WORKSPACE_ROOT / tour_id

    # Strategy 1: Look up screenshot_ref from the graph (absolute path)
    graph_path = tour_dir / "output" / "screen_map.json"
    if graph_path.exists():
        try:
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            for node in graph.get("screen_map", {}).get("graph", {}).get("nodes", []):
                if node.get("screen_id") == screen_id:
                    ref = node.get("screenshot_ref", "")
                    if ref and Path(ref).exists():
                        return FileResponse(Path(ref))
        except Exception:
            pass

    # Strategy 2: Search in known directories
    for base in ["analysis/screens", "dynamic/states", "dynamic"]:
        search_dir = tour_dir / base
        if not search_dir.exists():
            continue
        # Try screen_id directly, also try without page_ prefix
        search_terms = [screen_id]
        if screen_id.startswith("page_"):
            search_terms.append(screen_id[5:])  # strip page_ prefix
        for term in search_terms:
            for f in search_dir.rglob(f"*{term}*"):
                if f.suffix in (".jpg", ".png"):
                    return FileResponse(f)

    # Strategy 3: Match by index (first screenshot = first node, etc.)
    # DroidBot names: screen_YYYY-MM-DD_HHMMSS.png
    states_dir = tour_dir / "dynamic" / "states"
    if states_dir.exists():
        for png in sorted(states_dir.glob("screen_*.png")):
            return FileResponse(png)  # At least return something

    raise HTTPException(404, "Screenshot not found")


@app.get("/api/tours/{tour_id}/static")
async def get_static_analysis(tour_id: str):
    path = WORKSPACE_ROOT / tour_id / "static" / "analysis.json"
    if not path.exists():
        raise HTTPException(404, "Static analysis not found")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


# Serve frontend static files — only in production (not dev mode with Vite)
# To enable: set SERVE_STATIC=1
import os as _os
if _os.environ.get("SERVE_STATIC") == "1":
    FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"
    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
