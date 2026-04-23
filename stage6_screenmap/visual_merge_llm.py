"""B approach — LLM Vision tiebreaker for borderline node pairs.

Runs AFTER semantic_merge (A + D + A+) has handled the clear-cut cases.
Collects remaining pairs that are "visually similar but not identical"
(pHash distance 5~12) or "labels mildly similar" and sends them ALL in
a single batched Claude Sonnet 4.6 vision call to decide merge vs keep.

- Single API call per tour (cheap, ~$0.01~0.02 for ~10 pairs)
- Graceful degrade: if API key missing / network failure, just skip B
  and leave the A+D-merged ScreenMap intact.
- Verdict parsed from JSON; each applied merge tagged with
  ``via_llm=True`` and the LLM's ``reason`` in metadata.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── Pair selection ───────────────────────────────────────────

def _phash(ss_path: str) -> str | None:
    """Reuse semantic_merge's cache."""
    from .semantic_merge import _compute_phash
    return _compute_phash(ss_path)


def _phash_dist(h1: str | None, h2: str | None) -> int:
    from .semantic_merge import _phash_distance
    return _phash_distance(h1, h2)


def collect_borderline_pairs(
    nodes: list[dict],
    low: int = 5,
    high: int = 12,
    max_pairs: int = 20,
) -> list[tuple[dict, dict, int]]:
    """Collect pairs of nodes whose screenshots are visually SIMILAR but not
    identical — the range where A's hard-threshold is unsafe but ignoring
    them leaves real duplicates.

    Returns a list of ``(node_a, node_b, phash_distance)`` tuples, capped
    at ``max_pairs`` to keep the LLM prompt bounded.

    Filter rules:
    - Both nodes have screenshot_ref that exists
    - Same activity (cross-fragment OK — fragment detection can lag)
    - pHash distance in [low, high]
    - Neither is system:* entry
    """
    candidates: list[tuple[dict, dict, int]] = []
    ok_nodes = [
        n for n in nodes
        if n.get("screenshot_ref")
        and not (n.get("screen_id", "") or "").startswith("system:")
        and Path(n["screenshot_ref"]).exists()
    ]
    # Pre-compute pHash for each
    ph: dict[str, str] = {}
    for n in ok_nodes:
        h = _phash(n["screenshot_ref"])
        if h:
            ph[n["screen_id"]] = h
    for a, b in combinations(ok_nodes, 2):
        if (a.get("activity", "") or "") != (b.get("activity", "") or ""):
            continue
        h_a = ph.get(a["screen_id"])
        h_b = ph.get(b["screen_id"])
        if not h_a or not h_b:
            continue
        dist = _phash_dist(h_a, h_b)
        if low <= dist <= high:
            candidates.append((a, b, dist))
    candidates.sort(key=lambda t: t[2])   # nearest first
    return candidates[:max_pairs]


# ─── Image encoding for Claude vision ─────────────────────────

def _encode_image_for_llm(path: str, max_dim: int = 400) -> tuple[bytes, str]:
    """Read and downscale an image for the LLM call. Returns (bytes, media_type).

    400px cap on the longer dimension keeps per-image token cost ~0.001.
    Status/nav bar crop matches what semantic_merge's pHash uses.
    """
    from PIL import Image
    import io
    img = Image.open(path).convert("RGB")
    w, h = img.size
    img = img.crop((0, int(h * 0.05), w, int(h * 0.92)))
    # Downscale longest edge to max_dim
    w, h = img.size
    scale = min(1.0, max_dim / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return buf.getvalue(), "image/jpeg"


# ─── Batched LLM call ─────────────────────────────────────────

_SYSTEM = """You judge whether pairs of Android screen captures are the SAME logical screen.

Guidelines:
- "same": the user would consider them the same screen, even if minor state differs
         (e.g. different scroll position, different data in a list, stopwatch ticking).
- "different": genuinely distinct screens (e.g. different tabs of the app,
               different settings sub-pages, login vs home).
- "uncertain": too close to call — prefer this over a wrong "same".

Return ONLY a JSON array, one object per pair:
[{"pair": 1, "decision": "same|different|uncertain", "reason": "<=12 words"}, ...]

No prose, no explanations outside the JSON."""


def _build_pair_block(i: int, pair: tuple[dict, dict, int]) -> list[dict[str, Any]]:
    """Build Anthropic message content blocks for one pair: [text, img_a, img_b]."""
    a, b, dist = pair
    bytes_a, mt_a = _encode_image_for_llm(a["screenshot_ref"])
    bytes_b, mt_b = _encode_image_for_llm(b["screenshot_ref"])
    ctx = (
        f"\nPair {i}: activity={(a.get('activity') or '').rsplit('.', 1)[-1]}, "
        f"pHash_dist={dist}\n"
        f"  A: label={a.get('label', '')!r} fragment={a.get('fragment_class') or '-'}\n"
        f"  B: label={b.get('label', '')!r} fragment={b.get('fragment_class') or '-'}\n"
    )
    return [
        {"type": "text", "text": ctx},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mt_a,
                "data": base64.standard_b64encode(bytes_a).decode("ascii"),
            },
        },
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mt_b,
                "data": base64.standard_b64encode(bytes_b).decode("ascii"),
            },
        },
    ]


def query_llm_batch(pairs: list[tuple[dict, dict, int]]) -> list[dict]:
    """Single Claude Sonnet 4.6 vision call for all pairs. Returns list of
    {pair, decision, reason} dicts. Raises on auth/network failure."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or "PLACEHOLDER" in api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set (B approach requires it)")
    client = anthropic.Anthropic(api_key=api_key)
    model = os.environ.get("LLM_MODEL_SCREEN", "claude-sonnet-4-6")

    content: list[dict[str, Any]] = [
        {"type": "text", "text": f"Judge these {len(pairs)} pairs:"},
    ]
    for i, pair in enumerate(pairs, 1):
        content.extend(_build_pair_block(i, pair))
    content.append({"type": "text", "text": "\nRespond with the JSON array now."})

    logger.info("[B] LLM batch vision call for %d pairs (model=%s)", len(pairs), model)
    message = client.messages.create(
        model=model,
        max_tokens=1024,
        system=_SYSTEM,
        messages=[{"role": "user", "content": content}],
    )
    raw = message.content[0].text if message.content else ""
    return _parse_json_array(raw)


def _parse_json_array(text: str) -> list[dict]:
    """Extract JSON array from the LLM response, tolerating surrounding prose."""
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return []
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, list) else []
    except json.JSONDecodeError as e:
        logger.warning("[B] JSON parse failed: %s; raw=%s", e, text[:200])
        return []


# ─── Orchestration — call from semantic_merge or pipeline_service ──

def merge_borderline_via_llm(
    screenmap: dict,
    phash_low: int = 5,
    phash_high: int = 12,
    max_pairs: int = 20,
) -> dict:
    """Final cascade stage: Vision LLM tiebreaker over borderline pairs.

    In-place mutation. Adds a ``llm_visual_merge`` entry to metadata with
    full verdict log. Silently skips (logs warning) on auth/network failure.
    """
    graph = screenmap.get("screen_map", {}).get("graph") or screenmap.get("graph") or screenmap
    nodes: list[dict] = graph.get("nodes", []) or []
    edges: list[dict] = graph.get("edges", []) or []

    pairs = collect_borderline_pairs(nodes, low=phash_low, high=phash_high,
                                     max_pairs=max_pairs)
    if not pairs:
        logger.info("[B] no borderline pairs to judge")
        return screenmap

    try:
        verdicts = query_llm_batch(pairs)
    except Exception as e:
        logger.warning("[B] LLM call failed, skipping: %s", str(e)[:200])
        meta_host = screenmap if "metadata" in screenmap or "screen_map" in screenmap else graph
        md = meta_host.setdefault("metadata", {})
        md["llm_visual_merge"] = {"error": str(e)[:200], "pairs_considered": len(pairs)}
        return screenmap

    # Apply "same" verdicts
    from .semantic_merge import _rewrite_edges, _prefer_primary
    absorbed: set[str] = set()
    applied_merges: list[dict] = []

    # Map verdict pair index (1-based in prompt) to decision
    verdict_by_idx: dict[int, dict] = {int(v.get("pair", 0)): v for v in verdicts if v.get("pair")}

    for idx, (a, b, dist) in enumerate(pairs, 1):
        verdict = verdict_by_idx.get(idx)
        if not verdict:
            continue
        decision = (verdict.get("decision") or "").lower()
        reason = verdict.get("reason", "")
        if decision != "same":
            continue   # different / uncertain → keep separate
        if a.get("screen_id") in absorbed or b.get("screen_id") in absorbed:
            continue   # already merged in a previous verdict this round
        keeper, goner = _prefer_primary(a, b)
        keeper_id = keeper.get("screen_id", "")
        goner_id = goner.get("screen_id", "")
        applied_merges.append({
            "kept": keeper_id,
            "removed": goner_id,
            "activity": a.get("activity", ""),
            "label_kept": keeper.get("label", ""),
            "label_removed": goner.get("label", ""),
            "phash_distance": dist,
            "via_llm": True,
            "llm_reason": reason,
        })
        absorbed.add(goner_id)
        keeper.setdefault("merged_from", []).append(goner_id)
        if (not keeper.get("screenshot_ref")) and goner.get("screenshot_ref"):
            keeper["screenshot_ref"] = goner["screenshot_ref"]
        edges = _rewrite_edges(edges, goner_id, keeper_id)

    new_nodes = [n for n in nodes if n.get("screen_id") not in absorbed]
    graph["nodes"] = new_nodes
    graph["edges"] = edges

    meta_host = screenmap if "metadata" in screenmap or "screen_map" in screenmap else graph
    md = meta_host.setdefault("metadata", {})
    md["llm_visual_merge"] = {
        "pairs_considered": len(pairs),
        "phash_range": [phash_low, phash_high],
        "merges_applied": len(applied_merges),
        "nodes_before": len(nodes),
        "nodes_after": len(new_nodes),
        "merges": applied_merges,
        "verdicts": verdicts,
    }
    logger.info("[B] %d pairs → %d merges applied (%d → %d nodes)",
                len(pairs), len(applied_merges), len(nodes), len(new_nodes))
    return screenmap
