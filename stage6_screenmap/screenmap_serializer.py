"""Serialize the graph to the screen_map.json schema."""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def serialize_screenmap(graph: dict, metadata: dict, validation_report: dict) -> dict:
    """Convert internal graph to the ScreenMap output schema."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # Pre-index nodes for O(1) lookup
    node_map = {n["screen_id"]: n for n in nodes}

    edge_groups = _detect_edge_groups(edges, node_map)
    global_params = _extract_global_params(nodes)

    summary = validation_report.get("summary", {})

    screenmap = {
        "screen_map": {
            "app_name": metadata.get("package_name", "").split(".")[-1],
            "package_name": metadata.get("package_name", ""),
            "version": metadata.get("version_name", ""),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "graph": {
                "entry_node": graph.get("entry_node", ""),
                "nodes": [_serialize_node(n) for n in nodes],
                "edges": [_serialize_edge(e) for e in edges],
                "edge_groups": edge_groups,
                "global_params": global_params,
            },
            "metadata": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "coverage_ratio": 0.0,
                "orphan_nodes": summary.get("unreachable_count", 0),
                "dead_end_nodes": summary.get("dead_end_count", 0),
                "validation_issues": summary.get("issue_count", 0),
            },
        }
    }

    return screenmap


def _serialize_node(node: dict) -> dict:
    return {
        "screen_id": node.get("screen_id", ""),
        "activity": node.get("activity", ""),
        "label": node.get("label", ""),
        "functional_category": node.get("functional_category", "other"),
        "screen_purpose": node.get("screen_purpose", ""),
        "params": node.get("params", {"inputs": [], "outputs": [], "displays": []}),
        "widgets": node.get("widgets", []),
        "screenshot_ref": node.get("screenshot_ref", ""),
        "confidence": node.get("confidence", "medium"),
    }


def _serialize_edge(edge: dict) -> dict:
    return {
        "edge_id": edge.get("edge_id", ""),
        "from": edge.get("from", ""),
        "to": edge.get("to", ""),
        "trigger_action": edge.get("trigger_action", ""),
        "trigger_widget": edge.get("trigger_widget", ""),  # FIX #6: included
        "condition": edge.get("condition"),
        "passed_params": edge.get("passed_params", []),
        "returned_params": edge.get("returned_params", []),
    }


def _detect_edge_groups(edges: list[dict], node_map: dict) -> list[dict]:
    """Detect patterns like global tab navigation (same target from many sources)."""
    target_sources: dict[tuple, list[str]] = {}
    for e in edges:
        key = (e.get("to", ""), e.get("trigger_action", ""))
        target_sources.setdefault(key, []).append(e.get("from", ""))

    groups = []
    for (target, action), sources in target_sources.items():
        if len(sources) >= 3:
            # FIX #14: Use pre-indexed node_map for O(1) lookup
            target_node = node_map.get(target, {})
            groups.append({
                "group_name": f"Navigation to {target_node.get('label', target)}",
                "description": f"Multiple screens can reach {target_node.get('label', target)} via '{action}'",
                "edges": [
                    {"from": src, "to": target, "trigger_action": action}
                    for src in sources
                ],
            })

    return groups


def _extract_global_params(nodes: list[dict]) -> dict:
    """Extract params that appear across multiple nodes.

    FIX #14: Changed threshold from count>=1 to count>=2 so only truly
    cross-screen params are considered "global".
    """
    param_counts: dict[str, int] = {}
    param_source: dict[str, str] = {}

    for node in nodes:
        params = node.get("params", {})
        for output in params.get("outputs", []):
            param_counts[output] = param_counts.get(output, 0) + 1
            if output not in param_source:
                param_source[output] = node["screen_id"]

    global_params = {}
    for param, count in param_counts.items():
        if count >= 2:  # FIX: only params referenced by 2+ nodes are "global"
            global_params[param] = {
                "type": "string",
                "description": "Parameter produced by screen flow",
                "source_node": param_source.get(param, ""),
                "scope": "session" if "token" in param.lower() or "auth" in param.lower() else "local",
            }

    return global_params
