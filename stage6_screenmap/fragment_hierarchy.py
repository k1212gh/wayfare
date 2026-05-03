"""Link Fragment nodes to their host Activity via `contains` edges.

Extracted from stage6_screenmap/transformations.py (Step 4 세분화).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def _link_fragment_hierarchy(graph: dict) -> None:
    """Tag fragment nodes with parent_activity_id and emit `contains` edges.

    For each node where `node_type == 'fragment'`, find a sibling Activity
    node (same `host_activity` / `activity`) and:
      - set fragment.parent_activity_id = activity.screen_id
      - add an edge `activity -- contains --> fragment` (if not present)

    If no host activity node exists yet (walk captured a fragment for
    an activity whose wireframe stub was dropped), create a wireframe-like
    host node so the fragment isn't orphaned.
    """
    import hashlib as _hl
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # Index: activity FQN → activity-type node
    activity_hosts: dict[str, dict] = {}
    for n in nodes:
        if n.get("node_type") == "activity" and n.get("activity"):
            activity_hosts[n["activity"]] = n

    existing_edge_keys = {(e.get("from"), e.get("to"), e.get("kind"))
                          for e in edges}
    new_edges = 0
    new_hosts = 0
    linked = 0

    for n in list(nodes):
        if n.get("node_type") != "fragment":
            continue
        host_fqn = n.get("host_activity") or n.get("activity") or ""
        if not host_fqn:
            continue
        host = activity_hosts.get(host_fqn)
        if not host:
            # Synthesize a host node so the fragment has somewhere to hang.
            sid = f"act_{_hl.sha256(host_fqn.encode()).hexdigest()[:12]}"
            short = host_fqn.rsplit(".", 1)[-1] if "." in host_fqn else host_fqn
            host = {
                "screen_id": sid,
                "activity": host_fqn,
                "label": short,
                "functional_category": "other",
                "screen_purpose": "",
                "params": {"inputs": [], "outputs": [], "displays": []},
                "widgets": [],
                # ScreenMap expressivity extensions (sprint 2026-04-27)
                "chip_groups": [],
                "state_variables": [],
                "infinite_scroll": False,
                "scroll_metadata": {},
                "screenshot_ref": "",
                "structure_str": "",
                "confidence": "low",
                "status": "declared",
                "is_launcher": False,
                "is_system": False,
                "intent_filters": [],
                "node_type": "activity",
            }
            nodes.append(host)
            activity_hosts[host_fqn] = host
            new_hosts += 1

        # Link fragment → parent activity id (don't overwrite if already set)
        if not n.get("parent_activity_id"):
            n["parent_activity_id"] = host["screen_id"]
        linked += 1

        # Emit `contains` edge host → fragment (coalesceed)
        edge_key = (host["screen_id"], n["screen_id"], "contains")
        if edge_key not in existing_edge_keys:
            edges.append({
                "edge_id": f"e_contains_{host['screen_id'][:10]}_{n['screen_id'][:10]}",
                "from": host["screen_id"],
                "to": n["screen_id"],
                "trigger_action": "contains",
                "trigger_widget": "fragment_transaction",
                "kind": "contains",
                "confidence": "static_intent",
                "source": "hierarchy",
                "condition": None,
                "passed_params": [],
                "returned_params": [],
            })
            existing_edge_keys.add(edge_key)
            new_edges += 1

    if linked:
        logger.info("Fragment hierarchy: %d fragments linked, %d new hosts, %d contains edges",
                    linked, new_hosts, new_edges)


