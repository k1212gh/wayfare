"""Task-driven path planning over the ScreenMap.

Given a natural-language task ("play a song", "view lyrics", "open settings"),
Claude reads the ScreenMap (compact summary) and returns a step-by-step plan:
  [ (from_node, trigger_action, to_node, reasoning), ... ]

Design:
- ScreenMap summary is cacheable across many task queries for the same ScreenMap (prompt cache).
- The model picks from *existing* edges; if no direct edge exists, it explains
  what probed/stub node must be reached and via which deep link.
- For probed nodes with no UI data, plan returns a "JIT capture needed" marker
  so the runtime agent knows to uiautomator-dump that screen when visited.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def plan_task(screenmap: dict, task: str, api_key: str = "",
              model: str = "") -> dict:
    """Return a plan dict with `steps`, `path_nodes`, `notes`.

    Args:
        screenmap: parsed screen_map.json contents
        task: natural language user request ("재생 중 곡 가사 보여줘")
        api_key: ANTHROPIC_API_KEY (falls back to env)
        model: model id (default: Sonnet 4.6)
    """
    from stage5_annotate.llm_client import create_client
    client = create_client(
        api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
        model_screen=model or os.environ.get("LLM_MODEL_SCREEN", "claude-sonnet-4-6"),
        temperature=0.1,
    )
    graph = screenmap.get("screen_map", {}).get("graph", {})
    context = _build_plan_context(screenmap, graph)
    sys_prompt = _planner_system_prompt()
    user_prompt = (
        f"User task: \"{task.strip()}\"\n\n"
        "Use the ScreenMap above to plan a minimal sequence of steps that reaches a "
        "screen fulfilling the task.\n"
        "Return JSON: {\n"
        '  "task": "...",\n'
        '  "steps": [{"from": "screen_id", "trigger": "click/desc", "to": "screen_id", "why": "..."} ],\n'
        '  "path_nodes": ["screen_id", ...],\n'
        '  "notes": "..."\n'
        "}\n"
        "If an intermediate node is only 'probed' (stub), still include it — "
        "the agent will JIT-capture it at runtime."
    )
    try:
        resp = client.query_json(sys_prompt + "\n\n" + context, user_prompt,
                                 max_tokens=2048)
    except Exception as e:
        logger.error("Task navigator failed: %s", e)
        return {"task": task, "error": str(e), "steps": [], "path_nodes": []}
    if not isinstance(resp, dict):
        return {"task": task, "error": "invalid response shape", "steps": [], "path_nodes": []}
    resp.setdefault("task", task)
    resp.setdefault("steps", [])
    resp.setdefault("path_nodes", [])
    return resp


def _planner_system_prompt() -> str:
    return (
        "You are a mobile-UI task navigator working with a screen map of an "
        "Android app. The graph has nodes (screens) and edges (transitions with "
        "trigger_action). Your tour: given a user's natural-language task, find "
        "the shortest plausible path of edges that ends at a screen satisfying "
        "the task.\n\n"
        "Constraints:\n"
        "- Use ONLY edges that exist in the ScreenMap (given in the context block).\n"
        "- Prefer edges with confidence='observed' over 'static_intent'.\n"
        "- Entry point is the `entry_node` — start from there unless context "
        "suggests the user is already elsewhere.\n"
        "- If the task requires a screen that exists as 'probed' (stub with no "
        "UI data), include it — mark note 'jit_needed'.\n"
        "- If an edge doesn't exist but the target has an intent_filter, "
        "suggest a deep-link: trigger='deep_link:spotify://...'.\n"
        "- Keep output JSON only, no markdown."
    )


def _build_plan_context(screenmap: dict, graph: dict) -> str:
    app = screenmap.get("screen_map", {})
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    entry = graph.get("entry_node", "")

    # Compact per-node summary
    node_lines = []
    for n in nodes:
        sid = n.get("screen_id", "")
        act = n.get("activity", "") or ""
        short = act.rsplit(".", 1)[-1] if "." in act else act
        label = n.get("label") or short or sid
        cat = n.get("functional_category", "other")
        status = n.get("status", "")
        purpose = n.get("screen_purpose", "")
        line = f"- {sid} [{status}|{cat}] {label}"
        if purpose:
            line += f" — {purpose[:80]}"
        node_lines.append(line)

    edge_lines = []
    for e in edges[:400]:
        kind = e.get("kind", "")
        conf = e.get("confidence", "")
        trig = e.get("trigger_action", "") or e.get("trigger_widget", "") or ""
        edge_lines.append(
            f"- {e.get('from','')} --[{kind}|{conf}|{trig[:30]}]--> {e.get('to','')}"
        )

    return (
        f"# App: {app.get('app_name','')} ({app.get('package_name','')})\n"
        f"# Entry: {entry}\n"
        f"# Nodes ({len(nodes)}):\n"
        + "\n".join(node_lines)
        + f"\n\n# Edges ({len(edges)}):\n"
        + "\n".join(edge_lines)
    )
