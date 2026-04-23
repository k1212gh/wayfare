"""End-to-end pipeline orchestration — stage 1~6 + LLM enrichment.

Extracted from server.py (refactor Step 2.5). `run_pipeline_sync` is the
background-thread entry point called by `/api/tours/{id}/run`. It drives
every stage, writes per-stage status into `pipeline_state.json`, and — on
abnormal exit — reconciles the shared `_active_tour` slot in tour_store.

`build_static_screenmap` is the fallback path used when no device is connected;
it emits a nodes-only ScreenMap directly from Stage 2 output so the dashboard has
something to render without a dynamic walk.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dashboard.backend.paths import (
    WORKSPACE_ROOT,
    _cancel_path,
    _pause_path,
)
from dashboard.backend.services import tour_store
from dashboard.backend.services.adb_service import (
    _check_adb_device,
    _get_first_device,
)

logger = logging.getLogger(__name__)


def run_pipeline_sync(tour_id: str, device_serial: str = "") -> None:
    """Run the 6-stage pipeline in the current (background) thread.

    `device_serial` (optional) pins walk to a specific ADB device.
    When empty, falls back to `_get_first_device()` which picks whichever ADB
    lists first — problematic when multiple devices are attached (e.g. real
    phone + emulator). Callers should prefer passing an explicit serial.
    """
    project_root = Path(__file__).parent.parent.parent.parent
    state_path = WORKSPACE_ROOT / tour_id / "pipeline_state.json"

    # Register cancel event for this tour
    tour_store.register_cancel_event(tour_id)
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

        if str(project_root) not in sys.path:
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
        meta: dict = {}
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
        static_info: dict = {}
        if static_path.exists():
            static_info = json.loads(static_path.read_text(encoding="utf-8"))
            act_count = len(static_info.get("activities", []))
        update_stage("STATIC_DONE", detail=f"{act_count} activities")

        # Stage 2.5: Build static wireframe ScreenMap immediately so the dashboard has
        # *something* to render before dynamic walk is done.
        try:
            from stage6_screenmap.wireframe_builder import write_wireframe
            write_wireframe(config, static_info, meta)
            logger.info("Wrote static wireframe ScreenMap (%d nodes)", act_count)
        except Exception as e:
            logger.warning("Wireframe ScreenMap build failed: %s", e)

        # Stage 3: Walk
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
                    # Approach D: merge near-duplicate nodes after LLM labels
                    # are in. Env flag SEMANTIC_COALESCE=0 disables.
                    if os.environ.get("SEMANTIC_COALESCE", "1") != "0":
                        try:
                            from stage6_screenmap.semantic_merge import coalesce_file
                            screenmap_path = config.output_dir / config.screenmap_output_filename
                            threshold = float(os.environ.get("SEMANTIC_COALESCE_THRESHOLD", "0.85"))
                            phash_thresh = int(os.environ.get("SEMANTIC_COALESCE_PHASH", "4"))
                            coalesce_file(screenmap_path, threshold=threshold,
                                       phash_threshold=phash_thresh)
                        except Exception as e:
                            logger.warning("Semantic coalesce failed (non-fatal): %s", str(e)[:200])
                    # B approach: LLM Vision tiebreaker for borderline pairs.
                    # Opt-in via SEMANTIC_COALESCE_LLM=1. Gracefully skipped if
                    # the key is missing.
                    if os.environ.get("SEMANTIC_COALESCE_LLM", "0") == "1":
                        try:
                            from stage6_screenmap.visual_merge_llm import merge_borderline_via_llm
                            screenmap_path = config.output_dir / config.screenmap_output_filename
                            screenmap_data = json.loads(screenmap_path.read_text(encoding="utf-8"))
                            merge_borderline_via_llm(screenmap_data)
                            screenmap_path.write_text(
                                json.dumps(screenmap_data, indent=2, ensure_ascii=False),
                                encoding="utf-8",
                            )
                        except Exception as e:
                            logger.warning("LLM visual coalesce failed (non-fatal): %s", str(e)[:200])
                    update_stage("ANNOTATED")
                except Exception as e:
                    # Split auth vs runtime so the operator can tell them apart.
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
                logger.info(
                    "LLM enrichment skipped (no valid ANTHROPIC_API_KEY) — "
                    "using wireframe ScreenMap as final output",
                )
        else:
            # No device/walk → static-only ScreenMap from manifest activities
            update_stage("BUILDING_SCREENMAP")
            build_static_screenmap(config)
            update_stage("SCREENMAP_GENERATED")

    except Exception as e:
        logger.exception("Pipeline failed for tour %s", tour_id)
        if tour_store.is_cancelled(tour_id):
            update_stage("CANCELLED", error="Cancelled by user")
        else:
            update_stage("FAILED", error=str(e)[:500])
    finally:
        # Release global lock + cleanup cancel state
        with tour_store.get_lock():
            if tour_store.get_active_tour() == tour_id:
                tour_store.set_active_tour(None)
        tour_store.discard_cancel_event(tour_id)
        for p in (_cancel_path(tour_id), _pause_path(tour_id)):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


def build_static_screenmap(config) -> None:
    """Build a basic ScreenMap from static analysis only (no DroidBot walk).

    Edges come from the DEX-extracted transition graph (stage2's
    `transition_graph.json`). If that file is absent we emit zero edges —
    previously we fell back to an entry→everyone star topology which
    polluted the dashboard with meaningless edges and confused downstream
    validators. Better to show honest "nodes only" than fake structure.
    """
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
    for t in transition_info.get("transitions", []):
        src = t.get("source", "")
        dst = t.get("target", "")
        trigger = t.get("trigger", "startActivity")
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
