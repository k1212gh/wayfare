"""Serialize the graph to the screen_map.json schema."""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_GLOBAL_PARAM_MIN_USES = 2  # params appearing in at least this many nodes are "global"
_EDGE_GROUP_MIN_SOURCES = 3


def serialize_screenmap(graph: dict, metadata: dict, validation_report: dict) -> dict:
    """Convert internal graph to the ScreenMap output schema."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    node_map = {n["screen_id"]: n for n in nodes}

    edge_groups = _detect_edge_groups(edges, node_map)
    global_params = _extract_global_params(nodes)

    summary = validation_report.get("summary", {})

    serialized_nodes = sorted(
        (_serialize_node(n) for n in nodes),
        key=lambda n: n["screen_id"],
    )
    serialized_edges = sorted(
        (_serialize_edge(e) for e in edges),
        key=lambda e: (e["from"], e["to"], e["trigger_action"], e["trigger_widget"]),
    )

    return {
        "screen_map": {
            "app_name": metadata.get("package_name", "").split(".")[-1],
            "package_name": metadata.get("package_name", ""),
            "version": metadata.get("version_name", ""),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "graph": {
                "entry_node": graph.get("entry_node", ""),
                "nodes": serialized_nodes,
                "edges": serialized_edges,
                "edge_groups": edge_groups,
                "global_params": global_params,
            },
            "metadata": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "coverage_ratio": 0.0,
                # Kept under the legacy key `orphan_nodes` for dashboard compatibility,
                # but the source metric is `unreachable_count` (nodes unreachable from entry).
                "orphan_nodes": summary.get("unreachable_count", 0),
                "dead_end_nodes": summary.get("dead_end_count", 0),
                "validation_issues": summary.get("issue_count", 0),
            },
        }
    }


def _serialize_node(node: dict) -> dict:
    out = {
        "screen_id": node.get("screen_id", ""),
        "activity": node.get("activity", ""),
        # Fragment hierarchy
        "node_type": node.get("node_type", "activity"),
        "parent_activity_id": node.get("parent_activity_id", ""),
        "host_activity": node.get("host_activity", node.get("activity", "")),
        "fragment_class": node.get("fragment_class", ""),
        "label": node.get("label", ""),
        "functional_category": node.get("functional_category", "other"),
        "screen_purpose": node.get("screen_purpose", ""),
        "params": node.get("params", {"inputs": [], "outputs": [], "displays": []}),
        "widgets": node.get("widgets", []),
        # ScreenMap expressivity extensions (sprint 2026-04-27). Optional/empty by
        # default — clients ignoring these keep working unchanged.
        "chip_groups": node.get("chip_groups", []),
        "state_variables": node.get("state_variables", []),
        "infinite_scroll": node.get("infinite_scroll", False),
        "scroll_metadata": node.get("scroll_metadata", {}),
        "screenshot_ref": node.get("screenshot_ref", ""),
        "structure_str": node.get("structure_str", ""),
        # P0-14: byte-equal screenshot 시그널. semantic_merge L0 override 기록 보존.
        "screenshot_md5": node.get("screenshot_md5", ""),
        "confidence": node.get("confidence", "medium"),
        # Lifecycle status (declared / probed / resolved / partial / unknown / entry / enriched)
        "status": node.get("status", "declared"),
    }
    # Preserve additional session-added fields when present (wireframe flags,
    # classifier outputs, Vision labeler outputs, etc).
    for opt in ("is_launcher", "is_system", "statically_reachable",
                "intent_filters", "intent_actions", "intent_categories", "label_candidates", "title_text", "label_source",
                "parent_activity", "fragment_name",
                "capture_priority", "capture_reason",
                "is_external_lib", "external_lib_source", "is_plumbing",
                "primary_affordances", "fragment", "is_dialog", "blocks_parent", "dynamic"):
        if opt in node:
            out[opt] = node[opt]
    return out


def _serialize_edge(edge: dict) -> dict:
    out = {
        "edge_id": edge.get("edge_id", ""),
        "from": edge.get("from", ""),
        "to": edge.get("to", ""),
        "kind": edge.get("kind", "navigate"),
        "trigger_action": edge.get("trigger_action", ""),
        "trigger_widget": edge.get("trigger_widget", ""),
        "condition": edge.get("condition"),
        "passed_params": edge.get("passed_params", []),
        "returned_params": edge.get("returned_params", []),
    }
    # Preserve edge metadata — drives frontend styling + path planning.
    for opt in ("confidence", "source", "frequency", "weight",
                "outcome", "trigger_bounds", "trigger_label", "selector", "selectors",
                "input_value", "expect", "field", "list_item", "item_text"):
        if opt in edge:
            out[opt] = edge[opt]
    return out


def _detect_edge_groups(edges: list[dict], node_map: dict) -> list[dict]:
    """Detect patterns like global tab navigation (many sources → one target)."""
    target_sources: dict[tuple, list[str]] = {}
    for e in edges:
        key = (e.get("to", ""), e.get("trigger_action", ""))
        target_sources.setdefault(key, []).append(e.get("from", ""))

    groups = []
    for (target, action), sources in sorted(target_sources.items()):
        if len(sources) < _EDGE_GROUP_MIN_SOURCES:
            continue
        target_node = node_map.get(target, {})
        groups.append({
            "group_name": f"Navigation to {target_node.get('label', target)}",
            "description": f"Multiple screens can reach {target_node.get('label', target)} via '{action}'",
            "edges": [
                {"from": src, "to": target, "trigger_action": action}
                for src in sorted(sources)
            ],
        })
    return groups


def _extract_global_params(nodes: list[dict]) -> dict:
    """Params that appear as outputs on two or more nodes are treated as global."""
    param_counts: dict[str, int] = {}
    param_source: dict[str, str] = {}

    for node in nodes:
        for output in node.get("params", {}).get("outputs", []):
            param_counts[output] = param_counts.get(output, 0) + 1
            param_source.setdefault(output, node["screen_id"])

    global_params: dict[str, dict] = {}
    for param, count in sorted(param_counts.items()):
        if count < _GLOBAL_PARAM_MIN_USES:
            continue
        scope = "session" if ("token" in param.lower() or "auth" in param.lower()) else "local"
        global_params[param] = {
            "type": "string",
            "description": "Parameter produced by screen flow",
            "source_node": param_source.get(param, ""),
            "scope": scope,
        }
    return global_params
