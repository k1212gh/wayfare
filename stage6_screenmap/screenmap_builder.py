"""Merge subflows into a single global app flow graph."""

import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

NAVIGATE_ACTION = "navigate"
CONTAINS_ACTION = "contains"
NAVIGATE_EDGE_KIND = "navigate"
FRAGMENT_NAV_EDGE_KIND = "fragment_nav"
CONTAINS_EDGE_KIND = "contains"


def build_graph(
    subflows: list[dict],
    screen_analyses: list[dict],
    screen_cards: list[dict],
    entry_activity: str = "",
) -> dict[str, Any]:
    """Merge subflows + screen analyses into a unified directed graph.

    `entry_activity` is the launcher activity from static analysis; when
    provided it takes precedence over heuristic name matching.
    """
    analysis_map = {a["screen_id"]: a for a in screen_analyses}
    unit_map = {u["screen_id"]: u for u in screen_cards}

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    edge_set: set[tuple] = set()

    for sg in subflows:
        for node in sg.get("nodes", []):
            sid = node["screen_id"]
            if sid in nodes:
                _merge_node(nodes[sid], node)
            else:
                nodes[sid] = _build_node(node, analysis_map.get(sid, {}), unit_map.get(sid, {}))

    for sa in screen_analyses:
        sid = sa["screen_id"]
        if sid not in nodes:
            nodes[sid] = _build_node({}, sa, unit_map.get(sid, {}))

    for unit in screen_cards:
        sid = unit.get("screen_id", "")
        if sid and sid not in nodes:
            nodes[sid] = _build_node({}, {}, unit)

    for sg in subflows:
        for edge in sg.get("edges", []):
            trigger_action = edge.get("trigger_action", "")
            trigger_elem = edge.get("trigger_widget", "")
            key = (edge.get("from", ""), edge.get("to", ""), trigger_action, trigger_elem)
            if key not in edge_set:
                edge_set.add(key)
                edges.append(_build_edge(edge))

    # Synthetic reachability edges from screen_card navigation. Action is
    # deliberately "navigate" (not "click") so downstream validators can skip
    # them when checking trigger-level determinism.
    for unit in screen_cards:
        sid = unit["screen_id"]
        for target in unit.get("navigation_context", {}).get("reachable_screens", []):
            key = (sid, target, NAVIGATE_ACTION, "")
            if key not in edge_set and target in nodes:
                edge_set.add(key)
                edges.append({
                    "edge_id": _make_edge_id(sid, target, NAVIGATE_ACTION, ""),
                    "from": sid,
                    "to": target,
                    "trigger_action": NAVIGATE_ACTION,
                    "trigger_widget": "",
                    "condition": None,
                    "passed_params": [],
                    "returned_params": [],
                })

    _inject_activity_hosts(nodes, edges, edge_set)
    _annotate_edge_kinds(edges, nodes)

    entry_node = _find_entry_node(nodes, screen_cards, entry_activity)

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "entry_node": entry_node,
    }


def _make_edge_id(from_id: str, to_id: str, trigger_action: str, trigger_widget: str) -> str:
    """Hash every field that distinguishes two edges — otherwise two edges that
    share (from, to, action) but have different elements collide on edge_id."""
    raw = f"{from_id}|{to_id}|{trigger_action}|{trigger_widget}"
    return f"e_{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def _build_node(sg_node: dict, analysis: dict, unit: dict) -> dict:
    """Build a node in schema format."""
    sid = sg_node.get("screen_id") or analysis.get("screen_id") or unit.get("screen_id", "")
    label_source = sg_node.get("label") or analysis.get("screen_purpose") or sid
    activity_name = unit.get("activity_name", analysis.get("activity_name", ""))
    node_type = (
        unit.get("node_type")
        or analysis.get("node_type")
        or sg_node.get("node_type")
        or "activity"
    )
    parent_activity_id = (
        unit.get("parent_activity_id")
        or analysis.get("parent_activity_id")
        or sg_node.get("parent_activity_id")
        or ""
    )
    host_activity = (
        unit.get("host_activity")
        or analysis.get("host_activity")
        or sg_node.get("host_activity")
        or activity_name
    )
    fragment_class = (
        unit.get("fragment_class")
        or analysis.get("fragment_class")
        or sg_node.get("fragment_class")
        or ""
    )
    return {
        "screen_id": sid,
        "activity": activity_name,
        "node_type": node_type,
        "parent_activity_id": parent_activity_id,
        "host_activity": host_activity,
        "fragment_class": fragment_class,
        "label": label_source[:50] if label_source else sid,
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
        # Kept so the dashboard can resolve screenshots by structure hash.
        "structure_str": unit.get("structure_str", ""),
        "confidence": analysis.get("confidence", "medium"),
    }


def _build_edge(sg_edge: dict) -> dict:
    return {
        "edge_id": _make_edge_id(
            sg_edge.get("from", ""),
            sg_edge.get("to", ""),
            sg_edge.get("trigger_action", ""),
            sg_edge.get("trigger_widget", ""),
        ),
        "from": sg_edge.get("from", ""),
        "to": sg_edge.get("to", ""),
        "trigger_action": sg_edge.get("trigger_action", ""),
        "trigger_widget": sg_edge.get("trigger_widget", ""),
        "condition": sg_edge.get("condition"),
        "passed_params": sg_edge.get("passed_params", []),
        "returned_params": sg_edge.get("returned_params", []),
        "kind": sg_edge.get("kind", ""),
    }


def _merge_node(existing: dict, new: dict) -> None:
    for key in (
        "label",
        "functional_role",
        "screen_purpose",
        "node_type",
        "parent_activity_id",
        "host_activity",
        "fragment_class",
    ):
        if new.get(key) and not existing.get(key):
            existing[key] = new[key]
    if new.get("screen_params"):
        existing.setdefault("params", {}).update(new["screen_params"])


def _find_entry_node(
    nodes: dict[str, dict],
    screen_cards: list[dict],
    entry_activity: str,
) -> str:
    """Pick an entry node in order of decreasing trust:
    1. The manifest's launcher activity (when it maps to an existing node).
    2. A node whose functional_category is 'home'.
    3. A node whose activity string contains main/launcher/splash/home.
    4. The first context unit / node as a last resort.
    """
    def _is_real_screen(node: dict) -> bool:
        return node.get("node_type", "activity") != "activity" or not any(
            child.get("parent_activity_id") == node.get("screen_id", "")
            for child in nodes.values()
        )

    if entry_activity:
        for sid, node in nodes.items():
            if node.get("activity") == entry_activity and _is_real_screen(node):
                return sid
        # Try a relative-name match (".MainActivity" vs "com.x.MainActivity").
        short = entry_activity.rsplit(".", 1)[-1]
        for sid, node in nodes.items():
            act = node.get("activity", "")
            if (act.endswith("." + short) or act == short) and _is_real_screen(node):
                return sid

    for sid, node in nodes.items():
        if node.get("functional_category") == "home" and _is_real_screen(node):
            return sid

    for sid, node in nodes.items():
        if not _is_real_screen(node):
            continue
        activity = node.get("activity", "").lower()
        if any(k in activity for k in ("main", "launcher", "splash", "home")):
            return sid

    if screen_cards:
        for unit in screen_cards:
            sid = unit.get("screen_id", "")
            if sid and sid in nodes and _is_real_screen(nodes[sid]):
                return sid
    for sid, node in nodes.items():
        if _is_real_screen(node):
            return sid
    return next(iter(nodes), "")


def _inject_activity_hosts(
    nodes: dict[str, dict],
    edges: list[dict],
    edge_set: set[tuple],
) -> None:
    fragments = [
        node for node in nodes.values()
        if node.get("node_type") == "fragment" and node.get("parent_activity_id")
    ]

    for fragment in fragments:
        parent_id = fragment.get("parent_activity_id", "")
        host_activity = fragment.get("host_activity") or fragment.get("activity", "")
        if not parent_id or not host_activity:
            continue

        if parent_id not in nodes:
            nodes[parent_id] = _build_activity_host_node(parent_id, host_activity)

        key = (parent_id, fragment["screen_id"], CONTAINS_ACTION, "")
        if key in edge_set:
            continue
        edge_set.add(key)
        edges.append({
            "edge_id": _make_edge_id(parent_id, fragment["screen_id"], CONTAINS_ACTION, ""),
            "from": parent_id,
            "to": fragment["screen_id"],
            "trigger_action": CONTAINS_ACTION,
            "trigger_widget": "",
            "condition": None,
            "passed_params": [],
            "returned_params": [],
            "kind": CONTAINS_EDGE_KIND,
        })


def _build_activity_host_node(screen_id: str, activity: str) -> dict:
    short = activity.rsplit(".", 1)[-1] if activity else screen_id
    return {
        "screen_id": screen_id,
        "activity": activity,
        "node_type": "activity",
        "parent_activity_id": "",
        "host_activity": activity,
        "fragment_class": "",
        "label": short[:50],
        "functional_category": "navigation",
        "screen_purpose": f"Host activity for {short}",
        "params": {"inputs": [], "outputs": [], "displays": []},
        "widgets": [],
        "screenshot_ref": "",
        "structure_str": "",
        "confidence": "low",
    }


def _annotate_edge_kinds(edges: list[dict], nodes: dict[str, dict]) -> None:
    for edge in edges:
        if edge.get("kind") == CONTAINS_EDGE_KIND:
            continue
        edge["kind"] = _infer_edge_kind(edge, nodes)


def _infer_edge_kind(edge: dict, nodes: dict[str, dict]) -> str:
    src = nodes.get(edge.get("from", ""), {})
    dst = nodes.get(edge.get("to", ""), {})

    if (
        src.get("node_type") == "fragment"
        and dst.get("node_type") == "fragment"
        and src.get("parent_activity_id")
        and src.get("parent_activity_id") == dst.get("parent_activity_id")
    ):
        return FRAGMENT_NAV_EDGE_KIND
    return NAVIGATE_EDGE_KIND
