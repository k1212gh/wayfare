"""Read-only tour-scoped endpoints — graph, state, reports, traces, screenshots.

Extracted from server.py (refactor Step 2). All endpoints here share the same
contract: they read workspace files and return JSON/bytes. No subprocess, no
device state, no concurrency primitives — that belongs in api/tours.py (for
lifecycle) and api/{emulator,device}.py (for ADB I/O).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from dashboard.backend.paths import (
    WORKSPACE_ROOT,
    _SAFE_ID,
    _safe_tour_dir,
    _within,
)

router = APIRouter()


# ─── Graph + navigator + path ────────────────────────────────────────

@router.get("/api/tours/{tour_id}/graph")
async def get_graph(tour_id: str):
    tour_dir = _safe_tour_dir(tour_id)
    graph_path = tour_dir / "output" / "screen_map.json"
    if not graph_path.exists():
        raise HTTPException(404, "Graph not found")
    return JSONResponse(json.loads(graph_path.read_text(encoding="utf-8")))


@router.post("/api/tours/{tour_id}/plan")
async def plan_task_for_tour(tour_id: str, task: str):
    """Ask Claude to plan a path through the ScreenMap for a natural-language task."""
    tour_dir = _safe_tour_dir(tour_id)
    graph_path = tour_dir / "output" / "screen_map.json"
    if not graph_path.exists():
        raise HTTPException(404, "Graph not found")
    if not task.strip():
        raise HTTPException(400, "task parameter required")
    try:
        screenmap = json.loads(graph_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, f"ScreenMap parse failed: {e}")
    try:
        from stage6_screenmap.journey_planner import plan_task
        plan = plan_task(screenmap, task)
    except Exception as e:
        raise HTTPException(503, f"navigator error: {e}")
    return JSONResponse(plan)


@router.get("/api/tours/{tour_id}/path")
async def find_path(tour_id: str, source: str = "", target: str = ""):
    """Find shortest path between two nodes."""
    tour_dir = _safe_tour_dir(tour_id)
    graph_path = tour_dir / "output" / "screen_map.json"
    if not graph_path.exists():
        raise HTTPException(404, "Graph not found")
    if not source or not target:
        raise HTTPException(400, "source and target query params required")

    screenmap = json.loads(graph_path.read_text(encoding="utf-8"))

    # Ensure project root on sys.path (stage6_screenmap is at repo root, not under dashboard/)
    _ensure_project_on_path()
    from stage6_screenmap.route_finder import find_shortest_path, find_all_paths

    result = find_shortest_path(screenmap, source, target)
    if not result:
        raise HTTPException(404, f"No path from {source} to {target}")
    alternatives = find_all_paths(screenmap, source, target, max_paths=3)
    return JSONResponse({"best": result, "alternatives": alternatives})


@router.get("/api/tours/{tour_id}/plan")
async def plan_task_endpoint(tour_id: str, task: str = ""):
    """Plan a path for a natural language task (PoG-style)."""
    if not task:
        raise HTTPException(400, "task query param required (e.g., ?task=알림 끄기)")
    tour_dir = _safe_tour_dir(tour_id)
    graph_path = tour_dir / "output" / "screen_map.json"
    if not graph_path.exists():
        raise HTTPException(404, "Graph not found")

    screenmap = json.loads(graph_path.read_text(encoding="utf-8"))

    _ensure_project_on_path()
    from navigator import plan_task

    llm = None
    try:
        from stage5_annotate.llm_client import create_client
        llm = create_client()
    except Exception:
        pass

    result = plan_task(screenmap, task, llm_client=llm)
    return JSONResponse(result)


# ─── Live walk progress ─────────────────────────────────────

def _compute_coverage_live(tour_dir: Path, discovered: list[str]) -> dict | None:
    """Compute activity coverage against static analysis. None if static not ready."""
    static_path = tour_dir / "static" / "analysis.json"
    if not static_path.exists():
        return None
    try:
        static_info = json.loads(static_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    expected = [a.get("name", "") for a in static_info.get("activities", []) if a.get("name")]
    if not expected:
        return None
    from stage3_walk.activity_coverage import check_coverage
    cov = check_coverage({"activities_found": discovered}, expected)
    return {
        "covered": cov.get("covered_count", 0),
        "expected": cov.get("expected_count", len(expected)),
        "ratio": cov.get("ratio", 0.0),
        "missed_sample": cov.get("missed", [])[:5],
    }


@router.get("/api/tours/{tour_id}/walk-live")
async def get_walk_live(tour_id: str):
    """Get live walk data (states + transitions so far).

    Frontend polls this during WALKING stage to show real-time graph + coverage.
    """
    tour_dir = _safe_tour_dir(tour_id)
    walk_path = tour_dir / "dynamic" / "walk.json"
    if walk_path.exists():
        data = json.loads(walk_path.read_text(encoding="utf-8"))
        discovered = list(data.get("activities_found", []))
        coverage = _compute_coverage_live(tour_dir, discovered)
        payload = {
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
        }
        if coverage is not None:
            payload["coverage"] = coverage
        return JSONResponse(payload)

    # Walk in progress → scan per-event state files for activities seen so far
    states_dir = tour_dir / "dynamic" / "states"
    if states_dir.exists():
        state_files = sorted(states_dir.glob("state_*.json"))
        screen_files = list(states_dir.glob("screen_*.png"))
        seen_activities: set[str] = set()
        for sf in state_files:
            try:
                s = json.loads(sf.read_text(encoding="utf-8"))
                act = s.get("activity", "")
                if act and act != "unknown":
                    seen_activities.add(act)
            except Exception:
                continue
        coverage = _compute_coverage_live(tour_dir, sorted(seen_activities))
        payload = {
            "status": "running",
            "states": len(state_files),
            "screenshots": len(screen_files),
            "discovered_activities": len(seen_activities),
        }
        if coverage is not None:
            payload["coverage"] = coverage
        return JSONResponse(payload)

    return JSONResponse({"status": "not_started"})


# ─── State / reports / traces ──────────────────────────────────────

@router.get("/api/tours/{tour_id}/state")
async def get_state(tour_id: str):
    tour_dir = _safe_tour_dir(tour_id)
    state_path = tour_dir / "pipeline_state.json"
    if not state_path.exists():
        raise HTTPException(404, "State not found")
    return JSONResponse(json.loads(state_path.read_text(encoding="utf-8")))


@router.get("/api/tours/{tour_id}/report")
async def get_report(tour_id: str):
    tour_dir = _safe_tour_dir(tour_id)
    report_path = tour_dir / "output" / "report.json"
    if not report_path.exists():
        raise HTTPException(404, "Report not found")
    return JSONResponse(json.loads(report_path.read_text(encoding="utf-8")))


@router.get("/api/tours/{tour_id}/quality")
async def get_quality(tour_id: str):
    """ScreenMap quality 지표 — 모든 framework 동일 schema. (P1.2, 2026-04-29)

    Returns:
        {
          "total_nodes": int, "total_edges": int,
          "actionable_nodes": int,   # 사용자가 탭/입력 가능한 노드
          "plannable_nodes": int,    # actionable + 라벨링 완료
          "reachable_count": int,    # entry 에서 BFS 도달 가능
          "orphan_nodes": int,       # 도달 불가
          "dead_end_nodes": int,
          "validation_issues": int,
          "issue_severity": {"high": N, "medium": N, "low": N},
          "activity_coverage": float | null,
          "is_valid": bool
        }
    """
    tour_dir = _safe_tour_dir(tour_id)
    screenmap_path = tour_dir / "output" / "screen_map.json"
    if not screenmap_path.exists():
        raise HTTPException(404, "ScreenMap not found")
    static_path = tour_dir / "static" / "analysis.json"

    screenmap = json.loads(screenmap_path.read_text(encoding="utf-8"))
    static_info = (json.loads(static_path.read_text(encoding="utf-8"))
                   if static_path.exists() else None)

    _ensure_project_on_path()
    from stage6_screenmap.metadata_refresh import refresh_metadata
    md = refresh_metadata(screenmap, static_info=static_info)

    # is_valid 는 screenmap_validator 의 high 0 정의 그대로
    is_valid = (md.get("issue_severity") or {}).get("high", 0) == 0

    # Phase A 1번 (2026-04-29): completeness 분리 후 expose. 기존 alias 도 유지.
    return JSONResponse({
        "total_nodes": md.get("total_nodes", 0),
        "total_edges": md.get("total_edges", 0),
        "actionable_nodes": md.get("actionable_nodes", 0),
        "plannable_nodes": md.get("plannable_nodes", 0),
        "reachable_count": md.get("reachable_count", 0),
        "orphan_nodes": md.get("orphan_nodes", 0),
        "dead_end_nodes": md.get("dead_end_nodes", 0),
        "validation_issues": md.get("validation_issues", 0),
        "issue_severity": md.get("issue_severity", {"high": 0, "medium": 0, "low": 0}),
        "activity_coverage": md.get("activity_coverage"),  # alias — manifest_reachability.launched_ratio
        "completeness": md.get("completeness"),
        "is_valid": is_valid,
    })


@router.get("/api/tours/{tour_id}/diagnostics")
async def get_diagnostics(tour_id: str):
    """Walk diagnostics — '왜 더 깊이 안 갔는지' evidence-based 답.

    8 항목: action_distribution / fragment_distribution / entry_coverage /
    missed_entry_candidates / visit_concentration / stall_events /
    activity_coverage + root_cause_hints (auto-generated).

    파일이 있으면 (Stage 3 가 자동 생성) 읽고, 없으면 즉시 계산.
    """
    tour_dir = _safe_tour_dir(tour_id)
    cached = tour_dir / "dynamic" / "diagnostics.json"
    if cached.exists():
        try:
            return JSONResponse(json.loads(cached.read_text(encoding="utf-8")))
        except Exception:
            pass
    _ensure_project_on_path()
    try:
        from stage3_walk.walk_analyzer import analyze_walk
        return JSONResponse(analyze_walk(tour_dir))
    except Exception as e:
        raise HTTPException(500, f"diagnostics failed: {e}")


@router.get("/api/tours/{tour_id}/traces")
async def get_traces(tour_id: str):
    """Get JSONL execution traces for a tour."""
    tour_dir = _safe_tour_dir(tour_id)
    trace_path = tour_dir / "output" / "traces.jsonl"
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


@router.get("/api/tours/{tour_id}/static")
async def get_static_analysis(tour_id: str):
    tour_dir = _safe_tour_dir(tour_id)
    path = tour_dir / "static" / "analysis.json"
    if not path.exists():
        raise HTTPException(404, "Static analysis not found")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@router.get("/api/tours/{tour_id}/cache-stats")
async def get_cache_stats(tour_id: str):
    """Get UI pattern cache statistics.

    The pattern cache is GLOBAL (cross-tour). `tour_id` in the path is not
    used to locate data — no filesystem access keyed by it — so path traversal
    is not applicable here.
    """
    try:
        _ensure_project_on_path()
        from cache import WidgetCache
        c = WidgetCache()
        return JSONResponse(c.stats())
    except Exception as e:
        return JSONResponse({"enabled": False, "error": str(e)})


# ─── Screenshot serving ────────────────────────────────────────────

@router.get("/api/tours/{tour_id}/screenshot/{screen_id}")
async def get_screenshot(tour_id: str, screen_id: str):
    """Find screenshot for a node. Rejects path-traversal via tour_id/screen_id."""
    if not _SAFE_ID.match(screen_id):
        raise HTTPException(400, "Invalid screen_id")
    tour_dir = _safe_tour_dir(tour_id)

    # Strategy 1: screenshot_ref from the graph, must live inside tour_dir
    graph_path = tour_dir / "output" / "screen_map.json"
    if graph_path.exists():
        try:
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            for node in graph.get("screen_map", {}).get("graph", {}).get("nodes", []):
                if node.get("screen_id") == screen_id:
                    ref = node.get("screenshot_ref", "")
                    if ref:
                        ref_path = Path(ref)
                        if ref_path.exists() and _within(ref_path, tour_dir):
                            return FileResponse(ref_path)
        except Exception:
            pass

    # Strategy 2: search known screenshot locations (each must stay inside its dir)
    for base in ["analysis/screens", "dynamic/states", "dynamic"]:
        search_dir = tour_dir / base
        if not search_dir.exists():
            continue
        search_terms = [screen_id]
        if screen_id.startswith("page_"):
            search_terms.append(screen_id[5:])
        for term in search_terms:
            for f in search_dir.rglob(f"*{term}*"):
                if f.suffix in (".jpg", ".png") and _within(f, search_dir):
                    return FileResponse(f)

    # 2026-05-03: Strategy 3 (첫 PNG fallback) 제거.
    # declared 노드 (manifest wireframe) 클릭 시 다른 노드의 첫 PNG 가 반환되어
    # 사용자가 잘못된 화면 보던 회귀. 진짜 매칭 없으면 404 — frontend 가 onError
    # 또는 screenshot_ref 빈 노드 검사로 깨끗히 처리.
    raise HTTPException(404, "Screenshot not found")


# ─── Internal helpers ──────────────────────────────────────────────

def _ensure_project_on_path() -> None:
    """`stage6_screenmap`, `navigator`, `cache` live at the repo root (not under dashboard/),
    so make sure that root is on sys.path before importing them. Idempotent."""
    root = str(Path(__file__).parent.parent.parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
