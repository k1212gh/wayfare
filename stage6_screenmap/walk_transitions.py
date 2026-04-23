"""Inject walk transitions as ScreenMap edges with coverage-driven remapping.

Extracted from stage6_screenmap/transformations.py (Step 4 세분화).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def _inject_walk_transitions(graph: dict, transitions: list[dict],
                              screen_cards: list[dict], walk_screens: list[dict]) -> None:
    """Inject observed walk transitions into the graph.

    Maps walk IDs to page_ids by matching structure_str.
    """
    # Build structure_str → page_id mapping from context units
    struct_to_page: dict[str, str] = {}
    for cu in screen_cards:
        struct = cu.get("structure_str", "")
        if struct:
            struct_to_page[struct] = cu["screen_id"]
            struct_to_page[struct[:16]] = cu["screen_id"]

    # Build walk state_str → structure_str mapping
    exp_to_struct: dict[str, str] = {}
    for s in walk_screens:
        ss = s.get("state_str", "")
        struct = s.get("structure_str", "")
        if ss and struct:
            exp_to_struct[ss] = struct

    # Combined: walk_id → page_id
    def resolve(exp_id: str) -> str | None:
        # Direct page match
        if exp_id in {n["screen_id"] for n in graph.get("nodes", [])}:
            return exp_id
        # Via structure_str
        struct = exp_to_struct.get(exp_id, "")
        if struct:
            page = struct_to_page.get(struct) or struct_to_page.get(struct[:16])
            if page:
                return page
        # By canonical index
        if exp_id.startswith("screen_"):
            try:
                idx = int(exp_id.split("_")[1])
                pages = sorted({n["screen_id"] for n in graph.get("nodes", [])})
                if idx < len(pages):
                    return pages[idx]
            except (ValueError, IndexError):
                pass
        return None

    node_ids = {n["screen_id"] for n in graph.get("nodes", [])}
    existing_edges = {(e["from"], e["to"]) for e in graph.get("edges", [])}
    added = 0

    for t in transitions:
        from_id = t.get("from_screen", "")
        to_id = t.get("to_screen", "")

        from_node = resolve(from_id)
        to_node = resolve(to_id)

        # Check if nodes exist in graph, or find closest match
        if from_node not in node_ids:
            from_node = _find_matching_node(from_id, node_ids)
        if to_node not in node_ids:
            to_node = _find_matching_node(to_id, node_ids)

        if from_node and to_node and from_node != to_node and (from_node, to_node) not in existing_edges:
            import hashlib
            edge_id = f"e_exp_{hashlib.sha256(f'{from_node}|{to_node}'.encode()).hexdigest()[:12]}"
            event_type = t.get("event_type", "click")
            event_str = t.get("event_str", "").replace("click ", "")

            # Determine edge kind: back > overlay > contains(same activity) > navigate
            kind = "navigate"
            if "press_back" in event_type.lower() or "keycode_back" in event_str.lower():
                kind = "back"
            else:
                # Same-activity fragment transition → contains
                from_node_obj = next((n for n in graph.get("nodes", []) if n.get("screen_id") == from_node), {})
                to_node_obj = next((n for n in graph.get("nodes", []) if n.get("screen_id") == to_node), {})
                if from_node_obj.get("activity") and from_node_obj.get("activity") == to_node_obj.get("activity"):
                    kind = "contains"
                elif to_node_obj.get("functional_category") == "dialog":
                    kind = "overlay"

            graph["edges"].append({
                "edge_id": edge_id,
                "from": from_node,
                "to": to_node,
                "trigger_action": event_type,
                "trigger_widget": event_str,
                "kind": kind,
                "confidence": "observed",  # directly seen during walk
                "source": "walk",
                "condition": None,
                "passed_params": [],
                "returned_params": [],
            })
            existing_edges.add((from_node, to_node))
            added += 1

    if added:
        logger.info("Injected %d walk edges into graph", added)


def _find_matching_node(candidate: str, node_ids: set[str]) -> str | None:
    """Try to find a matching node ID by substring or index."""
    # Direct match
    if candidate in node_ids:
        return candidate

    # screen_XXX → try matching by index to page list
    if candidate.startswith("screen_"):
        try:
            idx = int(candidate.split("_")[1])
            sorted_nodes = sorted(node_ids)
            if idx < len(sorted_nodes):
                return sorted_nodes[idx]
        except (ValueError, IndexError):
            pass

    # Substring match
    for nid in node_ids:
        if candidate[:12] in nid or nid[:12] in candidate:
            return nid

    return None


