"""Path finding on the app flow graph — Dijkstra + A* with edge weights."""

import logging
from typing import Optional
import networkx as nx

logger = logging.getLogger(__name__)


def build_nx_graph(screenmap: dict) -> nx.DiGraph:
    """Convert screen_map JSON to NetworkX DiGraph with edge weights."""
    G = nx.DiGraph()
    graph = screenmap.get("screen_map", {}).get("graph", {})

    for node in graph.get("nodes", []):
        G.add_node(node["screen_id"], **{
            k: v for k, v in node.items() if k != "widgets"
        })

    for edge in graph.get("edges", []):
        weight = edge.get("weight", 1.0)
        G.add_edge(edge["from"], edge["to"], weight=weight, **{
            k: v for k, v in edge.items() if k not in ("from", "to")
        })

    return G


def find_shortest_path(screenmap: dict, source: str, target: str) -> Optional[dict]:
    """Find shortest path between two nodes using Dijkstra."""
    G = build_nx_graph(screenmap)

    if source not in G or target not in G:
        return None

    try:
        path_nodes = nx.dijkstra_path(G, source, target, weight="weight")
        path_cost = nx.dijkstra_path_length(G, source, target, weight="weight")
    except nx.NetworkXNoPath:
        return None

    # Build path with edge details
    steps = []
    for i in range(len(path_nodes) - 1):
        src, dst = path_nodes[i], path_nodes[i + 1]
        edge_data = G.edges[src, dst]
        steps.append({
            "from": src,
            "to": dst,
            "action": edge_data.get("trigger_action", ""),
            "element": edge_data.get("trigger_widget", ""),
            "weight": edge_data.get("weight", 1.0),
        })

    return {
        "source": source,
        "target": target,
        "path": path_nodes,
        "steps": steps,
        "total_cost": path_cost,
        "hop_count": len(path_nodes) - 1,
    }


def find_all_paths(screenmap: dict, source: str, target: str, max_paths: int = 5) -> list[dict]:
    """Find top-K shortest paths."""
    G = build_nx_graph(screenmap)

    if source not in G or target not in G:
        return []

    paths = []
    try:
        for path in nx.shortest_simple_paths(G, source, target, weight="weight"):
            cost = sum(G.edges[path[i], path[i+1]].get("weight", 1.0) for i in range(len(path)-1))
            paths.append({"path": path, "cost": cost, "hops": len(path) - 1})
            if len(paths) >= max_paths:
                break
    except nx.NetworkXNoPath:
        pass

    return paths
