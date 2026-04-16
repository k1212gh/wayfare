"""Enrich the graph using static analysis — restore missing edges, fix orphans."""

import logging

logger = logging.getLogger(__name__)


def enrich_graph(graph: dict, static_info: dict) -> dict:
    """Enrich the dynamic graph with static analysis data.

    1. Add back-edges for dead-end nodes (press_back) — prefer most-recent incoming source
    2. Restore missing edges from static Intent analysis (when available)
    3. Flag orphan nodes
    """
    nodes = {n["screen_id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])
    edge_set = {(e["from"], e["to"]) for e in edges}

    # 1. Add press_back edges for dead-end nodes
    has_outgoing = {e["from"] for e in edges}

    # FIX #15: Pre-build incoming edges per node for efficient lookup
    incoming_by_node: dict[str, list[dict]] = {}
    for e in edges:
        incoming_by_node.setdefault(e["to"], []).append(e)

    for nid in nodes:
        if nid not in has_outgoing and nid != graph.get("entry_node"):
            # FIX #15: Pick the last incoming source (most likely the previous screen)
            incoming = incoming_by_node.get(nid, [])
            if incoming:
                target = incoming[-1]["from"]  # last = most recently added
                if (nid, target) not in edge_set:
                    edges.append({
                        "edge_id": f"e_back_{nid[:12]}_{target[:12]}",
                        "from": nid,
                        "to": target,
                        "trigger_action": "press_back",
                        "trigger_widget": "",
                        "condition": None,
                        "passed_params": [],
                        "returned_params": [],
                    })
                    edge_set.add((nid, target))
                    logger.info("Added back-edge: %s → %s", nid, target)

    # 2. Connect orphan nodes using static activity mapping
    activity_to_node: dict[str, str] = {}
    for nid, node in nodes.items():
        act = node.get("activity", "")
        if act:
            activity_to_node[act] = nid

    # Add edges from static Intent transitions (if available from stage2 smali analysis)
    intent_transitions = static_info.get("intent_transitions", [])
    for it in intent_transitions:
        from_act = it.get("from_activity", "")
        to_act = it.get("to_activity", "")
        from_node = activity_to_node.get(from_act, "")
        to_node = activity_to_node.get(to_act, "")
        if from_node and to_node and (from_node, to_node) not in edge_set:
            edges.append({
                "edge_id": f"e_static_{from_node[:12]}_{to_node[:12]}",
                "from": from_node,
                "to": to_node,
                "trigger_action": "intent",
                "trigger_widget": "",
                "condition": "static_analysis",
                "passed_params": [],
                "returned_params": [],
            })
            edge_set.add((from_node, to_node))
            logger.info("Added static Intent edge: %s → %s", from_act, to_act)

    graph["edges"] = edges
    return graph
