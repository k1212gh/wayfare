"""ScreenMap-first LLM enrichment.

Reads the wireframe `screen_map.json` produced by Stage 6 and asks the LLM
to fill in label / screen_purpose / functional_category for each node, using
the rest of the graph as cacheable context.

Why this design:
- Graph structure (nodes + edges) is the same across every batch → can be sent
  once with `cache_control: ephemeral` so Claude re-uses the cached prefix.
- Output is bounded per batch (≤10 nodes) so the JSON fits under Claude's output
  token limit.
- Failure isolation: if any batch fails, the wireframe ScreenMap on disk stays intact
  (we only merge after a successful parse).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config import PipelineConfig

logger = logging.getLogger(__name__)

BATCH_SIZE = 10

CATEGORY_ENUM = [
    "home", "list", "detail", "form", "auth", "settings",
    "dialog", "media", "search", "other",
]


def annotate_screenmap(config: PipelineConfig) -> None:
    """Read wireframe ScreenMap → LLM annotates → write back in place."""
    screenmap_path = config.output_dir / config.screenmap_output_filename
    if not screenmap_path.exists():
        logger.warning("No ScreenMap at %s — stage6 must run first; skipping LLM", screenmap_path)
        return

    screenmap = json.loads(screenmap_path.read_text(encoding="utf-8"))
    graph = screenmap.get("screen_map", {}).get("graph", {})
    nodes = graph.get("nodes", [])
    if not nodes:
        logger.warning("ScreenMap has 0 nodes — nothing to annotate")
        return

    # Pull minimal graph summary once, used as cached prefix across every batch
    context_block = _build_kg_context(screenmap, graph)

    # Build client
    from .llm_client import create_client
    client = create_client(
        api_key=config.anthropic_api_key,
        model_screen=config.llm_model_screen,
        model_widget=config.llm_model_widget,
        temperature=config.llm_temperature,
        max_retries=config.llm_max_retries,
    )

    system_prompt = _system_prompt()

    total = len(nodes)
    annotated_count = 0
    for i in range(0, total, BATCH_SIZE):
        batch = nodes[i : i + BATCH_SIZE]
        logger.info("Annotating batch %d-%d / %d", i + 1, i + len(batch), total)
        user_prompt = _build_batch_prompt(context_block, batch)
        try:
            resp = client.query_json(system_prompt, user_prompt, max_tokens=4096)
        except Exception as e:
            logger.warning("Batch %d failed: %s — skipping", i // BATCH_SIZE, e)
            continue

        annotations = resp.get("annotations", []) if isinstance(resp, dict) else []
        applied = _apply_annotations(nodes, annotations)
        annotated_count += applied

        # Atomic save after each successful batch so partial progress persists
        screenmap_path.write_text(
            json.dumps(screenmap, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    logger.info("LLM enrichment done: %d / %d nodes annotated", annotated_count, total)


# ───────────────────────────────────────────────────────────
# Prompt builders
# ───────────────────────────────────────────────────────────

def _system_prompt() -> str:
    return (
        "You label Android screens in an app-flow screen map.\n"
        "Given the graph structure (nodes + edges) and a batch of nodes to label, "
        "return JSON with one entry per requested node.\n\n"
        f"functional_category MUST be one of: {', '.join(CATEGORY_ENUM)}.\n"
        "label: short Korean or English phrase, ≤ 40 chars.\n"
        "screen_purpose: one sentence describing what the user does here.\n"
        "confidence: 'high' | 'medium' | 'low'.\n\n"
        'Respond ONLY with valid JSON: {"annotations": [{"screen_id":...}...]}.\n'
        "Do not invent screen_ids — only label the ones asked."
    )


def _build_kg_context(screenmap: dict, graph: dict) -> str:
    """Compact, cacheable graph summary sent with every batch."""
    app = screenmap.get("screen_map", {})
    meta = app.get("metadata", {}) or {}
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    node_lines = []
    for n in nodes:
        sid = n.get("screen_id", "")
        act = n.get("activity", "") or ""
        short = act.rsplit(".", 1)[-1] if "." in act else act
        node_lines.append(f"- {sid}  [{short}]")

    edge_lines = []
    for e in edges[:300]:  # cap to keep prompt bounded
        edge_lines.append(
            f"- {e.get('from','')} --[{e.get('trigger_action','')}]--> {e.get('to','')}"
        )

    return (
        f"# App: {app.get('app_name','')} ({app.get('package_name','')})\n"
        f"# Entry node: {graph.get('entry_node','')}\n"
        f"# Nodes ({len(nodes)}):\n"
        + "\n".join(node_lines)
        + f"\n\n# Edges ({len(edges)}):\n"
        + "\n".join(edge_lines)
        + "\n"
    )


def _build_batch_prompt(context_block: str, batch: list[dict]) -> str:
    """Batch-specific prompt: the context is cacheable, batch detail is not."""
    batch_detail = []
    for n in batch:
        sid = n.get("screen_id", "")
        act = n.get("activity", "") or ""
        short = act.rsplit(".", 1)[-1] if "." in act else act
        elems = n.get("widgets", []) or []
        elem_lines = []
        for el in elems[:8]:
            eid = el.get("id", "")
            role = el.get("role", "") or el.get("type", "")
            if eid or role:
                elem_lines.append(f"    - {eid}: {role}")
        batch_detail.append(
            f"* {sid}\n"
            f"  activity: {short}\n"
            f"  widgets:\n" + ("\n".join(elem_lines) if elem_lines else "    (none)")
        )

    return (
        context_block
        + "\n\n---\nLabel the following nodes:\n"
        + "\n".join(batch_detail)
        + "\n\nRespond in JSON: "
        '{"annotations": [{"screen_id": "...", "label": "...", '
        '"screen_purpose": "...", "functional_category": "...", "confidence": "..."}, ...]}'
    )


# ───────────────────────────────────────────────────────────
# Merge
# ───────────────────────────────────────────────────────────

def _apply_annotations(nodes: list[dict], annotations: list[dict]) -> int:
    """Merge LLM output into nodes. Only updates if screen_id matches a real node."""
    by_id = {n.get("screen_id", ""): n for n in nodes}
    applied = 0
    for ann in annotations:
        if not isinstance(ann, dict):
            continue
        sid = ann.get("screen_id", "")
        n = by_id.get(sid)
        if n is None:
            continue
        label = (ann.get("label") or "").strip()
        purpose = (ann.get("screen_purpose") or "").strip()
        cat = (ann.get("functional_category") or "").strip()
        conf = (ann.get("confidence") or "").strip()

        if label:
            n["label"] = label[:50]
        if purpose:
            n["screen_purpose"] = purpose
        if cat in CATEGORY_ENUM:
            n["functional_category"] = cat
        if conf in ("high", "medium", "low"):
            n["confidence"] = conf
        applied += 1
    return applied
