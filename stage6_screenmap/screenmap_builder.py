"""Merge subflows into a single global app flow graph."""

import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_graph(
    subflows: list[dict],
    screen_analyses: list[dict],
    screen_cards: list[dict],
) -> dict[str, Any]:
    """Merge subflows + screen analyses into a unified directed graph.

    Returns dict with:
      - nodes: list of node dicts (schema)
      - edges: list of edge dicts (schema, with trigger_widget)
      - entry_node: the first screen_id
    """
    analysis_map = {a["screen_id"]: a for a in screen_analyses}
    unit_map = {u["screen_id"]: u for u in screen_cards}

    nodes: dict[str, dict] = {}  # screen_id → node
    edges: list[dict] = []
    edge_set: set[tuple] = set()

    # Merge all subflow nodes
    for sg in subflows:
        for node in sg.get("nodes", []):
            sid = node["screen_id"]
            if sid in nodes:
                _merge_node(nodes[sid], node)
            else:
                nodes[sid] = _build_node(node, analysis_map.get(sid, {}), unit_map.get(sid, {}))

    # Add nodes from screen_analyses not yet in graph
    for sa in screen_analyses:
        sid = sa["screen_id"]
        if sid not in nodes:
            nodes[sid] = _build_node({}, sa, unit_map.get(sid, {}))

    # Merge all subflow edges
    for sg in subflows:
        for edge in sg.get("edges", []):
            # FIX #5: Include trigger_action AND trigger_widget in coalesce key
            trigger_elem = edge.get("trigger_widget", "")
            trigger_action = edge.get("trigger_action", "")
            key = (edge.get("from", ""), edge.get("to", ""), trigger_action, trigger_elem)
            if key not in edge_set:
                edge_set.add(key)
                edges.append(_build_edge(edge))

    # Add edges from screen_card navigation
    for unit in screen_cards:
        sid = unit["screen_id"]
        for target in unit.get("navigation_context", {}).get("reachable_screens", []):
            key = (sid, target, "navigate", "")
            if key not in edge_set and target in nodes:
                edge_set.add(key)
                edges.append({
                    "edge_id": _make_edge_id(sid, target, "navigate"),
                    "from": sid,
                    "to": target,
                    "trigger_action": "navigate",
                    "trigger_widget": "",
                    "condition": None,
                    "passed_params": [],
                    "returned_params": [],
                })

    entry_node = _find_entry_node(nodes, screen_cards)

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "entry_node": entry_node,
    }


def _make_edge_id(from_id: str, to_id: str, trigger: str) -> str:
    """Generate a unique edge_id that won't collide for same node pairs with different triggers.

    FIX #5: Include trigger in the hash to avoid collisions.
    """
    raw = f"{from_id}|{to_id}|{trigger}"
    return f"e_{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def _build_node(sg_node: dict, analysis: dict, unit: dict) -> dict:
    """Build a node in schema format."""
    sid = sg_node.get("screen_id") or analysis.get("screen_id", "")
    label_source = sg_node.get("label") or analysis.get("screen_purpose", sid)
    return {
        "screen_id": sid,
        "activity": unit.get("activity_name", analysis.get("activity_name", "")),
        "label": label_source[:50] if label_source else sid,  # code-point safe truncation
        "functional_category": analysis.get("functional_category", "other"),
        "screen_purpose": analysis.get("screen_purpose", sg_node.get("functional_role", "")),
        "params": sg_node.get("screen_params", {"inputs": [], "outputs": [], "displays": []}),
        "widgets": [
            {
                "id": e.get("widget_id", ""),
                "type": e.get("action_type", e.get("action_types", ["click"])[0] if e.get("action_types") else "click"),
                "role": e.get("role", e.get("content_desc", "")),
            }
            for e in analysis.get("key_widgets", unit.get("available_actions", []))
        ],
        "screenshot_ref": unit.get("screenshot", ""),
        # Store structure_str for screenshot lookup (server resolves to actual file)
        "structure_str": unit.get("structure_str", ""),
        "confidence": analysis.get("confidence", "medium"),
    }


def _build_edge(sg_edge: dict) -> dict:
    """Build edge with trigger_widget field (FIX #6)."""
    return {
        "edge_id": _make_edge_id(
            sg_edge.get("from", ""),
            sg_edge.get("to", ""),
            sg_edge.get("trigger_action", ""),
        ),
        "from": sg_edge.get("from", ""),
        "to": sg_edge.get("to", ""),
        "trigger_action": sg_edge.get("trigger_action", ""),
        "trigger_widget": sg_edge.get("trigger_widget", ""),  # FIX #6
        "condition": sg_edge.get("condition"),
        "passed_params": sg_edge.get("passed_params", []),
        "returned_params": sg_edge.get("returned_params", []),
    }


def _merge_node(existing: dict, new: dict) -> None:
    """Merge new node data into existing, preferring non-empty values."""
    for key in ("label", "functional_role", "screen_purpose"):
        if new.get(key) and not existing.get(key):
            existing[key] = new[key]
    if new.get("screen_params"):
        existing.setdefault("params", {}).update(new["screen_params"])


def _find_entry_node(nodes: dict, screen_cards: list[dict]) -> str:
    """Find the entry node — launcher activity or first state."""
    for sid, node in nodes.items():
        activity = node.get("activity", "").lower()
        if any(k in activity for k in ("main", "launcher", "splash", "home")):
            return sid
    if screen_cards:
        return screen_cards[0].get("screen_id", "")
    return list(nodes.keys())[0] if nodes else ""
