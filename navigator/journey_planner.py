"""PoG-style task navigator: natural language task → optimal ScreenMap path.

3-stage approach (from Paths-over-Graph paper):
1. Topic Entity Search: find nodes matching task keywords
2. Graph Path Search: BFS/Dijkstra between topic entities
3. LLM Ranking: Claude scores candidate paths by task relevance
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def plan_task(screenmap: dict, task: str, llm_client=None) -> dict:
    """Plan a path through the ScreenMap for a natural language task.

    Args:
        screenmap: screen_map.json content
        task: natural language task (e.g., "알림 설정 끄기")
        llm_client: optional LLM for path ranking

    Returns:
        dict with planned path, steps, and reasoning
    """
    graph = screenmap.get("screen_map", {}).get("graph", {})
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # Stage 1: Topic entity search — find nodes related to task
    topic_nodes = _find_topic_nodes(nodes, task)
    if not topic_nodes:
        return {"error": "No relevant screens found for task", "task": task}

    # Stage 2: Graph path search — find paths between topic nodes
    candidate_paths = _find_candidate_paths(nodes, edges, topic_nodes, graph.get("entry_node", ""))

    if not candidate_paths:
        return {"error": "No path found between relevant screens", "task": task,
                "topic_nodes": [n["screen_id"] for n in topic_nodes]}

    # Stage 3: LLM ranking (if client available)
    if llm_client:
        best = _llm_rank_paths(llm_client, candidate_paths, task, nodes)
    else:
        best = candidate_paths[0]  # Shortest path as default

    return {
        "task": task,
        "planned_path": best["path"],
        "steps": best.get("steps", []),
        "cost": best.get("cost", 0),
        "reasoning": best.get("reasoning", "Shortest path selected"),
        "topic_nodes": [n["screen_id"] for n in topic_nodes],
        "alternatives": len(candidate_paths),
    }


def _find_topic_nodes(nodes: list[dict], task: str) -> list[dict]:
    """Find nodes whose labels/purposes match the task keywords."""
    task_lower = task.lower()
    keywords = task_lower.split()

    scored = []
    for node in nodes:
        score = 0
        searchable = (
            (node.get("label", "") + " " +
             node.get("screen_purpose", "") + " " +
             node.get("activity", "") + " " +
             node.get("functional_category", "")).lower()
        )
        for kw in keywords:
            if kw in searchable:
                score += 1
            # Partial match
            if any(kw in word for word in searchable.split()):
                score += 0.5

        if score > 0:
            scored.append((score, node))

    scored.sort(key=lambda x: -x[0])
    return [n for _, n in scored[:5]]  # Top 5 relevant nodes


def _find_candidate_paths(nodes: list[dict], edges: list[dict],
                          topic_nodes: list[dict], entry_node: str) -> list[dict]:
    """Find paths from entry to topic nodes using BFS."""
    import networkx as nx

    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n["screen_id"])
    for e in edges:
        G.add_edge(e["from"], e["to"], weight=1,
                    action=e.get("trigger_action", ""),
                    element=e.get("trigger_widget", ""))

    paths = []
    # Find paths from entry to each topic node
    start = entry_node or (nodes[0]["screen_id"] if nodes else "")

    for topic in topic_nodes:
        target = topic["screen_id"]
        if start == target:
            continue
        try:
            for path in nx.shortest_simple_paths(G, start, target, weight="weight"):
                steps = []
                for i in range(len(path) - 1):
                    edge_data = G.edges.get((path[i], path[i + 1]), {})
                    steps.append({
                        "from": path[i],
                        "to": path[i + 1],
                        "action": edge_data.get("action", ""),
                        "element": edge_data.get("element", ""),
                    })
                cost = len(path) - 1
                paths.append({
                    "path": path,
                    "steps": steps,
                    "cost": cost,
                    "target_label": topic.get("label", target),
                })
                if len(paths) >= 3:  # Max 3 paths per topic
                    break
        except nx.NetworkXNoPath:
            continue

    # Sort by cost
    paths.sort(key=lambda p: p["cost"])
    return paths[:5]


def _llm_rank_paths(llm_client, paths: list[dict], task: str, nodes: list[dict]) -> dict:
    """Use LLM to rank candidate paths by task relevance."""
    node_map = {n["screen_id"]: n for n in nodes}

    paths_desc = []
    for i, p in enumerate(paths[:3]):
        steps_desc = " → ".join(
            f"{node_map.get(s['from'], {}).get('label', s['from'])} --[{s['action']}]--> {node_map.get(s['to'], {}).get('label', s['to'])}"
            for s in p["steps"]
        )
        paths_desc.append(f"Path {i+1} ({p['cost']} steps): {steps_desc}")

    prompt = f"""Task: {task}

Candidate paths through the app:
{chr(10).join(paths_desc)}

Which path best accomplishes the task? Reply with JSON:
{{"best_path": 1, "reasoning": "why this path"}}"""

    try:
        result = llm_client.query_json(
            "You are a mobile app navigation expert. Pick the best path for the task.",
            prompt,
        )
        idx = result.get("best_path", 1) - 1
        best = paths[min(idx, len(paths) - 1)]
        best["reasoning"] = result.get("reasoning", "")
        return best
    except Exception as e:
        logger.warning("LLM ranking failed: %s", e)
        return paths[0]
