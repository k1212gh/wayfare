"""Stage 6: Screen Map generation — merge, validate, serialize."""

import json
import logging
from pathlib import Path
from config import PipelineConfig
from .screenmap_builder import build_graph
from .screenmap_validator import validate_graph
from .screenmap_enricher import enrich_graph
from .screenmap_serializer import serialize_screenmap

logger = logging.getLogger(__name__)


def run_stage6(config: PipelineConfig) -> None:
    """Build the final screen_map.json."""
    sa_path = config.analysis_dir / "screen_analyses.json"
    sg_path = config.analysis_dir / "subflows.json"
    cu_path = config.analysis_dir / "screen_cards.json"
    meta_path = config.apk_dir / "metadata.json"
    static_path = config.static_dir / "analysis.json"
    walk_path = config.dynamic_dir / "walk.json"

    screen_analyses = json.loads(sa_path.read_text(encoding="utf-8")) if sa_path.exists() else []
    subflows = json.loads(sg_path.read_text(encoding="utf-8")) if sg_path.exists() else []
    screen_cards = json.loads(cu_path.read_text(encoding="utf-8")) if cu_path.exists() else []
    metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    static_info = json.loads(static_path.read_text(encoding="utf-8")) if static_path.exists() else {}

    # Load walk data (TapWalker/DroidBot)
    walk_transitions = []
    walk_screens = []
    if walk_path.exists():
        walk = json.loads(walk_path.read_text(encoding="utf-8"))
        walk_transitions = walk.get("transitions", [])
        walk_screens = walk.get("states", [])
        logger.info("Loaded %d walk transitions, %d states", len(walk_transitions), len(walk_screens))

    # 1. Build graph from subflows + screen analyses (fresh)
    graph = build_graph(subflows, screen_analyses, screen_cards)

    # 1b. If a wireframe ScreenMap was built at Stage 2.5, merge it in — preserves
    #     declared activities we never visited during walk, plus static
    #     entry edges (launcher / intent-filter).
    try:
        _merge_wireframe(config, graph, static_info, metadata)
    except Exception as e:
        logger.warning("Wireframe merge failed: %s", e)

    # 1b2. Classify each activity as A (user screen) / B (plumbing) / C (deep-link)
    #      BEFORE manifest scan so downstream stages can read the priority.
    #      Scan doesn't read this yet (scan runs in Stage 3), but the frontend
    #      and task navigator use it to interpret node roles.
    try:
        from .activity_classifier import classify_activities
        # Wrap graph as ScreenMap-shaped dict for the classifier
        classify_activities({"screen_map": {"graph": graph}})
    except Exception as e:
        logger.warning("Activity classification failed: %s", e)

    # 1c. Apply manifest scan results — activities successfully launched by
    #     `am start -W` get status='probed'. Coverage bar counts them as
    #     reachable without requiring UI-walked navigation.
    try:
        _apply_manifest_scan(config, graph)
    except Exception as e:
        logger.warning("Manifest scan merge failed: %s", e)

    # 1d. Fragment hierarchy: link each fragment node to its host activity
    #     (parent_activity_id) + emit `contains` edges Activity → Fragment.
    try:
        _link_fragment_hierarchy(graph)
    except Exception as e:
        logger.warning("Fragment hierarchy linking failed: %s", e)

    # 2. Inject walk transitions + compute edge weights from frequency
    # framework 전달 — RN/Flutter 면 미해결 transition 노드 합성 (D 옵션)
    framework = (metadata or {}).get("framework", "")
    _inject_walk_transitions(graph, walk_transitions, screen_cards, walk_screens,
                              framework=framework)

    # 2b. Compute edge weights from transition frequency
    _compute_transition_weights(graph, walk_transitions, screen_cards, walk_screens)

    # 2b+. Detect global edges — same (to, trigger) appearing from ≥3 sources
    try:
        _mark_global_transitions(graph)
    except Exception as e:
        logger.warning("Global edge detection failed: %s", e)

    # 2b++. Mark infinite-scroll feed nodes (sprint 2026-04-27). Heuristic
    # based on functional_category + scrollable view signal + activity-level
    # sibling count. Downstream (semantic_merge) uses the flag to apply a
    # more lenient pHash threshold for feed-screen coalesce.
    try:
        _mark_infinite_scroll_nodes(graph)
    except Exception as e:
        logger.warning("Infinite-scroll marking failed: %s", e)

    # 2c. Heuristic classification — assign functional_category for every node
    # that the LLM hasn't already labeled. Uses UI element / activity hints.
    try:
        from .heuristic_classifier import classify_all_nodes
        classify_all_nodes(
            graph.get("nodes", []),
            screen_cards,
            graph.get("entry_node", ""),
        )
        cats = {}
        for n in graph.get("nodes", []):
            c = n.get("functional_category", "other")
            cats[c] = cats.get(c, 0) + 1
        logger.info("Heuristic classification: %s", cats)
    except Exception as e:
        logger.warning("Heuristic classification failed: %s", e)

    # 3. Enrich with static analysis
    graph = enrich_graph(graph, static_info)

    # 4. Validate
    report = validate_graph(graph)
    logger.info("Validation: %s", json.dumps(report["summary"], indent=2))

    # 5. Serialize
    screenmap = serialize_screenmap(graph, metadata, report)

    # 5b. Detect 9 universal primitives BEFORE writing — so when Stage 5
    # LLM runs next (screenmap_annotator), each node already has `primitives` and
    # the LLM can produce `primitive_outcomes` for each. Phase 2 P2.2
    # depends on this ordering. Re-runs after coalesce in pipeline_service.
    try:
        from .primitive_detector import detect_primitives_for_screenmap
        summary = detect_primitives_for_screenmap(screenmap, tour_dir=config.tour_dir)
        logger.info("[primitives early] %s", summary)
    except Exception as e:
        logger.warning("Early primitive detection failed: %s", e)

    output_path = config.output_dir / config.screenmap_output_filename
    output_path.write_text(json.dumps(screenmap, indent=2, ensure_ascii=False), encoding="utf-8")

    report_path = config.output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("ScreenMap generated: %d nodes, %d edges", len(graph.get("nodes", [])), len(graph.get("edges", [])))



# Transformations (wireframe merge, walk edges, edge weights,
# fragment hierarchy, manifest scan, global-edge marking) moved to
# stage6_screenmap/transformations.py — refactor Step 4. Imported here so
# run_stage6 can call them unqualified.
from .transformations import (  # noqa: E402
    _inject_walk_transitions,
    _find_matching_node,
    _merge_wireframe,
    _apply_manifest_scan,
    _link_fragment_hierarchy,
    _mark_global_transitions,
    _compute_transition_weights,
    _mark_infinite_scroll_nodes,
)
