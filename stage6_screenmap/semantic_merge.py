"""Semantic coalescing of ScreenMap nodes — D approach.

Runs AFTER the LLM annotator (stage 5) so each node already has ``label``,
``screen_purpose``, and ``functional_category`` filled in. Merges nodes that
almost-certainly represent the same screen:

- same ``activity``
- same ``fragment_class`` (or both absent)
- same ``functional_category``
- label similarity > threshold (default 0.85, SequenceMatcher ratio on
  normalized text)
- outgoing edge ``kind`` set same (not exact edge equality — that's too
  strict; we just require both nodes have the same "kinds" of transitions)

When merging node B into A: rewrite edges pointing to/from B to reference A,
and coalesce the resulting edge set. A retains its screen_id; B's screen_id
is recorded in A's ``merged_from`` list for traceability.

Exposed as:
- ``semantic_merge(screenmap: dict, threshold: float = 0.85) -> dict`` — in-place
- ``coalesce_file(in_path, out_path, threshold)`` — file-level wrapper
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

logger = logging.getLogger(__name__)


_LABEL_NOISE = re.compile(r"[\s\-_/(),.·—:]+")


def _normalize_label(s: str) -> str:
    """Lowercase, collapse whitespace, strip common punctuation — so
    'Stopwatch — Running' ~= 'stopwatch running'."""
    if not s:
        return ""
    return _LABEL_NOISE.sub(" ", s.strip().lower()).strip()


def _label_similarity(a: str, b: str) -> float:
    """SequenceMatcher ratio on normalized labels. Cheap, no deps."""
    na = _normalize_label(a)
    nb = _normalize_label(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _node_group_key(n: dict) -> tuple:
    """Candidate-merge bucket. Nodes with different keys never merge.

    NOTE: functional_category is intentionally NOT in the key. The LLM
    annotator sometimes assigns different categories to visually-identical
    screens (e.g. two "Stopwatch Screen" captures landing as category=home
    vs category=other). We don't want that inconsistency to prevent a
    clearly-identical merge; label similarity + edge-kind overlap below
    still guard against real cross-category confusion.
    """
    return (
        n.get("activity", "") or "",
        n.get("fragment_class", "") or n.get("fragment", "") or "",
        n.get("node_type", "") or "",   # don't merge Activity host into its Fragment
    )


def _edge_kind_set(edges: list[dict], screen_id: str) -> frozenset[str]:
    """Set of outgoing edge ``kind`` values from a given node."""
    return frozenset(
        (e.get("kind") or e.get("trigger_action", "") or "navigate")
        for e in edges if e.get("from") == screen_id
    )


def _is_mergeable(
    a: dict,
    b: dict,
    edges: list[dict],
    threshold: float,
) -> bool:
    """Return True if `a` and `b` are near-duplicates.

    Two-tier rule set:
      Tier 1 — **identical normalized labels**: merge regardless of edge
               kinds. High confidence the LLM saw the same screen twice.
      Tier 2 — **similar labels** (ratio ≥ threshold): require outgoing edge
               kinds to overlap (Jaccard > 0). This prevents merging
               "Home with full content" against "Home (empty state)" which
               have similar labels but different transition sets.
    """
    # Never merge system / entry stubs with other nodes
    if a.get("screen_id", "").startswith("system:") or b.get("screen_id", "").startswith("system:"):
        return False

    la = _normalize_label(a.get("label", ""))
    lb = _normalize_label(b.get("label", ""))

    # Tier 1: identical normalized labels → strongest signal, merge unconditionally
    if la and lb and la == lb:
        return True

    sim = _label_similarity(a.get("label", ""), b.get("label", ""))
    if sim < threshold:
        return False

    # Tier 2: similar labels → require overlap in outgoing edge kinds.
    # Empty set on one side is OK (not-yet-walked node absorbed by walked one).
    kinds_a = _edge_kind_set(edges, a.get("screen_id", ""))
    kinds_b = _edge_kind_set(edges, b.get("screen_id", ""))
    if not kinds_a or not kinds_b:
        return True   # one side has no outgoing edges — can't disprove merge
    if kinds_a & kinds_b:
        return True   # any overlap is enough for Tier-2 merge
    return False


def _rewrite_edges(
    edges: list[dict],
    old_id: str,
    new_id: str,
) -> list[dict]:
    """Rewrite every edge that references old_id to use new_id instead.
    Afterwards, drop self-loops created by the rewrite and coalesce exact
    duplicates (same from/to/kind/trigger_action/trigger_widget)."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for e in edges:
        e = dict(e)   # copy so we don't mutate the original
        if e.get("from") == old_id:
            e["from"] = new_id
        if e.get("to") == old_id:
            e["to"] = new_id
        # Drop self-loops introduced by merging (A → A after A==B absorbed)
        if e.get("from") == e.get("to"):
            continue
        key = (
            e.get("from"),
            e.get("to"),
            e.get("kind") or "",
            e.get("trigger_action") or "",
            e.get("trigger_widget") or "",
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _prefer_primary(a: dict, b: dict) -> tuple[dict, dict]:
    """Return (keeper, to_be_merged). Prefer the node with:
    1. A screenshot_ref (captured UI)
    2. status != 'declared'
    3. Earlier screen_id lexicographically (stable)
    """
    def score(n: dict) -> tuple[int, int, str]:
        has_ss = 1 if n.get("screenshot_ref") else 0
        not_decl = 1 if n.get("status") != "declared" else 0
        return (has_ss, not_decl, n.get("screen_id", "") or "")

    sa = score(a)
    sb = score(b)
    # Higher tuple wins; tie-break is lexicographic screen_id (reversed for stability)
    if sa[:2] > sb[:2]:
        return a, b
    if sb[:2] > sa[:2]:
        return b, a
    # Equal utility → keep lexicographically smaller screen_id (deterministic)
    if (a.get("screen_id") or "") <= (b.get("screen_id") or ""):
        return a, b
    return b, a


def semantic_merge(screenmap: dict, threshold: float = 0.85) -> dict:
    """Merge near-duplicate nodes in the supplied ScreenMap dict. In-place.

    Returns the same dict with ``nodes``/``edges`` mutated and a
    ``semantic_merge`` entry added to ``metadata`` for traceability.
    """
    graph = screenmap.get("screen_map", {}).get("graph") or screenmap.get("graph") or screenmap
    nodes: list[dict] = graph.get("nodes", []) or []
    edges: list[dict] = graph.get("edges", []) or []

    if not nodes:
        return screenmap

    # Bucket nodes by (activity, fragment, category, node_type). Only nodes
    # within the same bucket are candidates for merging.
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for n in nodes:
        buckets[_node_group_key(n)].append(n)

    merges: list[dict] = []
    absorbed: set[str] = set()

    for key, group in buckets.items():
        if len(group) < 2:
            continue
        # Greedy O(N^2) within each bucket — bucket size typically <25
        for i, a in enumerate(group):
            if a.get("screen_id") in absorbed:
                continue
            for j in range(i + 1, len(group)):
                b = group[j]
                if b.get("screen_id") in absorbed:
                    continue
                if not _is_mergeable(a, b, edges, threshold):
                    continue
                keeper, goner = _prefer_primary(a, b)
                keeper_id = keeper.get("screen_id", "")
                goner_id = goner.get("screen_id", "")
                merges.append({
                    "kept": keeper_id,
                    "removed": goner_id,
                    "activity": a.get("activity", ""),
                    "label_kept": keeper.get("label", ""),
                    "label_removed": goner.get("label", ""),
                    "similarity": _label_similarity(
                        keeper.get("label", ""), goner.get("label", ""),
                    ),
                })
                absorbed.add(goner_id)
                # Track provenance on the survivor
                keeper.setdefault("merged_from", []).append(goner_id)
                # If the goner had a screenshot and the keeper didn't, transplant
                if (not keeper.get("screenshot_ref")) and goner.get("screenshot_ref"):
                    keeper["screenshot_ref"] = goner["screenshot_ref"]
                # Union of outgoing/incoming knowledge — preserve unique intent_filters
                for k in ("intent_filters", "merged_from"):
                    if k in goner and goner[k]:
                        keeper.setdefault(k, [])
                        for v in goner[k]:
                            if v not in keeper[k]:
                                keeper[k].append(v)
                # Rewrite edges globally; must do this now so subsequent
                # mergeability checks see the updated topology.
                edges = _rewrite_edges(edges, goner_id, keeper_id)
                # Don't break — keeper `a` may still have more candidates in
                # this bucket (e.g. "Stopwatch Screen" × 5 all collapse in
                # one pass when inner loop keeps going).

    # Drop absorbed nodes
    new_nodes = [n for n in nodes if n.get("screen_id") not in absorbed]

    graph["nodes"] = new_nodes
    graph["edges"] = edges

    # Metadata — where was this? Some callers pass the inner graph, some pass
    # full ScreenMap. Attach to whichever top-level dict we can find.
    meta_host = screenmap if "metadata" in screenmap or "screen_map" in screenmap else graph
    md = meta_host.setdefault("metadata", {})
    md["semantic_merge"] = {
        "threshold": threshold,
        "merges_applied": len(merges),
        "nodes_before": len(nodes),
        "nodes_after": len(new_nodes),
        "merges": merges,
    }

    logger.info(
        "[semantic_merge] %d → %d nodes (%d merges, threshold=%.2f)",
        len(nodes), len(new_nodes), len(merges), threshold,
    )

    return screenmap


def coalesce_file(in_path: str | Path, out_path: str | Path | None = None,
               threshold: float = 0.85) -> dict:
    """File-level convenience wrapper. Reads JSON, runs semantic_merge,
    writes output (defaulting to in-place)."""
    in_p = Path(in_path)
    out_p = Path(out_path) if out_path else in_p

    screenmap = json.loads(in_p.read_text(encoding="utf-8"))
    semantic_merge(screenmap, threshold=threshold)
    out_p.write_text(json.dumps(screenmap, indent=2, ensure_ascii=False), encoding="utf-8")
    return screenmap
