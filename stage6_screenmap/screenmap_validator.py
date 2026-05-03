"""Validate the app flow graph — reachability, dead ends, param consistency, cycles."""

import logging
from collections import deque

logger = logging.getLogger(__name__)


def validate_graph(graph: dict) -> dict:
    """Run all spec validations + ScreenAtlas transition consistency.

    Returns a validation report with issues and summary.
    """
    nodes = {n["screen_id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])
    entry_node = graph.get("entry_node", "")

    issues = []

    # 1. Reachability: all nodes reachable from entry via BFS
    unreachable = _check_reachability(nodes, edges, entry_node)
    for nid in unreachable:
        issues.append({
            "type": "unreachable",
            "severity": "high",
            "node": nid,
            "message": f"Node {nid} is not reachable from entry node",
        })

    # 2. Dead ends: non-exit nodes with no outgoing edges
    dead_ends = _check_dead_ends(nodes, edges)
    for nid in dead_ends:
        issues.append({
            "type": "dead_end",
            "severity": "medium",
            "node": nid,
            "message": f"Node {nid} has no outgoing edges (dead end)",
        })

    # 3. Parameter consistency: edge.passed_params ⊆ target.inputs
    param_issues = _check_param_consistency(nodes, edges)
    issues.extend(param_issues)

    # 4. Cycle detection
    cycles = _detect_cycles(nodes, edges)
    for cycle in cycles:
        issues.append({
            "type": "cycle",
            "severity": "low",
            "nodes": cycle,
            "message": f"Cycle detected: {' → '.join(cycle)}",
        })

    # 5. Transition consistency: same (source, element, action) → same target
    transition_issues = _check_transition_consistency(edges)
    issues.extend(transition_issues)

    # P1.1 (2026-04-29): 새 quality 필드 추가 — actionable / plannable / reachable / severity.
    # `node.primitives` 같은 Phase 2 schema 가 들어와도 동일 필드 그대로 — additive.
    actionable = sum(
        1 for n in nodes.values()
        if n.get("widgets") or n.get("primary_affordances") or n.get("chip_groups")
    )
    plannable = sum(
        1 for n in nodes.values()
        if (n.get("widgets") or n.get("primary_affordances") or n.get("chip_groups"))
        and n.get("status") in ("enriched", "probed")
        and (n.get("label") or n.get("screen_purpose"))
    )
    sev_counts = {"high": 0, "medium": 0, "low": 0}
    for issue in issues:
        sev = issue.get("severity", "medium")
        if sev in sev_counts:
            sev_counts[sev] += 1

    summary = {
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "unreachable_count": len(unreachable),
        "reachable_count": len(nodes) - len(unreachable),
        "dead_end_count": len(dead_ends),
        "cycle_count": len(cycles),
        "issue_count": len(issues),
        "issue_severity": sev_counts,
        "actionable_nodes": actionable,
        "plannable_nodes": plannable,
        "is_valid": sev_counts["high"] == 0,
    }

    return {"issues": issues, "summary": summary}


def _check_reachability(nodes: dict, edges: list, entry: str) -> list[str]:
    if not entry or entry not in nodes:
        return list(nodes.keys())

    adj: dict[str, list[str]] = {nid: [] for nid in nodes}
    for e in edges:
        src = e.get("from", "")
        if src in adj:
            adj[src].append(e.get("to", ""))

    visited = set()
    queue = deque([entry])
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        for neighbor in adj.get(node, []):
            if neighbor not in visited and neighbor in nodes:
                queue.append(neighbor)

    return [nid for nid in nodes if nid not in visited]


def _check_dead_ends(nodes: dict, edges: list) -> list[str]:
    has_outgoing = {e.get("from", "") for e in edges}
    return [nid for nid in nodes if nid not in has_outgoing]


def _check_param_consistency(nodes: dict, edges: list) -> list[dict]:
    issues = []
    for edge in edges:
        passed = set(edge.get("passed_params", []))
        if not passed:
            continue
        target_id = edge.get("to", "")
        target = nodes.get(target_id, {})
        target_inputs = set(target.get("params", {}).get("inputs", []))
        if target_inputs and not passed.issubset(target_inputs):
            extra = passed - target_inputs
            issues.append({
                "type": "param_mismatch",
                "severity": "low",
                "edge": edge.get("edge_id", ""),
                "message": f"Edge passes params {extra} not in target inputs",
            })
    return issues


def _detect_cycles(nodes: dict, edges: list) -> list[list[str]]:
    """Detect cycles using DFS. Returns list of cycle paths."""
    adj: dict[str, list[str]] = {nid: [] for nid in nodes}
    for e in edges:
        src = e.get("from", "")
        if src in adj:
            adj[src].append(e.get("to", ""))

    cycles = []
    visited = set()
    rec_stack: set[str] = set()

    def dfs(node: str, path: list[str]) -> None:
        if len(cycles) >= 10:  # Limit cycle detection
            return
        visited.add(node)
        rec_stack.add(node)
        path.append(node)

        for neighbor in adj.get(node, []):
            if neighbor not in visited:
                dfs(neighbor, path)
            elif neighbor in rec_stack:
                # Found cycle
                idx = path.index(neighbor) if neighbor in path else -1
                if idx >= 0:
                    cycles.append(path[idx:] + [neighbor])

        path.pop()
        rec_stack.discard(node)

    for nid in nodes:
        if nid not in visited:
            dfs(nid, [])

    return cycles[:10]


def _check_transition_consistency(edges: list) -> list[dict]:
    """Check that same (source, trigger) doesn't lead to multiple targets."""
    transition_map: dict[tuple, list[str]] = {}
    for e in edges:
        key = (e.get("from", ""), e.get("trigger_action", ""))
        transition_map.setdefault(key, []).append(e.get("to", ""))

    issues = []
    for (src, trigger), targets in transition_map.items():
        unique_targets = set(targets)
        if len(unique_targets) > 1:
            issues.append({
                "type": "ambiguous_transition",
                "severity": "medium",
                "source": src,
                "trigger": trigger,
                "targets": sorted(unique_targets),
                "message": f"Same action '{trigger}' from {src} leads to {len(unique_targets)} different targets",
            })
    return issues
