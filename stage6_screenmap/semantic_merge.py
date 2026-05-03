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


# ─── A approach: pHash cache + visual merge ─────────────────
# Computed lazily per-process, so multiple merges on the same ScreenMap share cache.
_phash_cache: dict[str, str] = {}


def _compute_phash(screenshot_path: str) -> str | None:
    """Return 8x8 pHash hex of a screenshot, or None if unavailable.

    Crops status/nav bars (top 5%, bottom 8%) so time-of-day in the status
    bar doesn't perturb the hash. Cached by path.
    """
    if not screenshot_path:
        return None
    if screenshot_path in _phash_cache:
        return _phash_cache[screenshot_path]
    try:
        import imagehash
        from PIL import Image
        p = Path(screenshot_path)
        if not p.exists():
            _phash_cache[screenshot_path] = ""
            return None
        img = Image.open(p)
        w, h = img.size
        img = img.crop((0, int(h * 0.05), w, int(h * 0.92)))
        ph = str(imagehash.phash(img, hash_size=8))
        _phash_cache[screenshot_path] = ph
        return ph
    except Exception as e:
        logger.debug("pHash failed for %s: %s", screenshot_path, e)
        _phash_cache[screenshot_path] = ""
        return None


def _phash_distance(h1: str | None, h2: str | None) -> int:
    """Hamming distance between two pHash hex strings. Returns 999 if either is empty."""
    if not h1 or not h2:
        return 999
    try:
        import imagehash
        return imagehash.hex_to_hash(h1) - imagehash.hex_to_hash(h2)
    except Exception:
        return 999


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
    phash_threshold: int = 4,
) -> bool:
    """Return True if `a` and `b` are near-duplicates.

    Three-tier rule set (priority: A > 1 > 2):
      Tier A — **visually identical** (pHash distance ≤ phash_threshold):
               merge regardless of labels. Safest signal — the screenshots
               are pixel-near-identical, so whatever the LLM labeled them
               as, they're the same screen. Catches the LLM-label-drift
               case that Tiers 1/2 miss.
      Tier 1 — **identical normalized labels**: merge regardless of edge
               kinds. High confidence the LLM saw the same screen twice.
      Tier 2 — **similar labels** (ratio ≥ threshold): require outgoing edge
               kinds to overlap (Jaccard > 0). Prevents merging
               "Home with full content" against "Home (empty state)".
    """
    # Never merge system / entry stubs with other nodes
    if a.get("screen_id", "").startswith("system:") or b.get("screen_id", "").startswith("system:"):
        return False

    # Tier A: pHash visual merge (strongest evidence).
    # Lenient threshold for infinite-scroll feed pairs: Instagram-like screens
    # show different items per scroll position, so identical screens still
    # diverge in pHash. Bump threshold to ~2× when both nodes are flagged as
    # infinite_scroll (Stage 6 _mark_infinite_scroll_nodes).
    #
    # 2026-04-30 (Bug 2 fix — screenatlas_phash_coalesce_caveats 메모리 적용):
    # pHash 단독 coalesce 은 form/list/counter screens 의 false positive 위험.
    # 메가커피 35cbb9a4: 24 unique structure_str → 14 ScreenMap 노드 (10 손실).
    # 이벤트/공지/콘서트 화면이 pHash 거리 4 이내인데 structure 가 다름.
    # → structure_str 다르면 dist > 0 일 때 merge 거부. byte-identical (dist=0)
    # 경우만 라벨 안 보고도 merge.
    ss_a = a.get("screenshot_ref")
    ss_b = b.get("screenshot_ref")
    if ss_a and ss_b and ss_a != ss_b:
        ph_a = _compute_phash(ss_a)
        ph_b = _compute_phash(ss_b)
        if ph_a and ph_b:
            dist = _phash_distance(ph_a, ph_b)
            effective_threshold = phash_threshold
            if a.get("infinite_scroll") and b.get("infinite_scroll"):
                effective_threshold = max(phash_threshold, 8)
            if dist <= effective_threshold:
                # structure_str 가드 — 다른 structure 면 phash 가까워도 다른 화면
                sa = a.get("structure_str", "") or ""
                sb = b.get("structure_str", "") or ""
                if dist > 0 and sa and sb and sa != sb:
                    pass  # 라벨 / 엣지 검사로 진행 (Tier 1 / 2)
                else:
                    return True

    la = _normalize_label(a.get("label", ""))
    lb = _normalize_label(b.get("label", ""))

    # Tier 1: identical normalized labels → strongest label signal
    if la and lb and la == lb:
        return True

    sim = _label_similarity(a.get("label", ""), b.get("label", ""))
    if sim < threshold:
        return False

    # Tier 2: similar labels → require overlap in outgoing edge kinds.
    kinds_a = _edge_kind_set(edges, a.get("screen_id", ""))
    kinds_b = _edge_kind_set(edges, b.get("screen_id", ""))
    if not kinds_a or not kinds_b:
        return True
    if kinds_a & kinds_b:
        return True
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


def semantic_merge(screenmap: dict, threshold: float = 0.85,
                   phash_threshold: int = 4) -> dict:
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
                if not _is_mergeable(a, b, edges, threshold, phash_threshold):
                    continue
                keeper, goner = _prefer_primary(a, b)
                keeper_id = keeper.get("screen_id", "")
                goner_id = goner.get("screen_id", "")
                # Compute pHash distance for traceability — shows WHY the
                # merge fired (visual vs label).
                ph_dist = None
                ss_k = keeper.get("screenshot_ref")
                ss_g = goner.get("screenshot_ref")
                if ss_k and ss_g and ss_k != ss_g:
                    ph_dist = _phash_distance(_compute_phash(ss_k), _compute_phash(ss_g))
                merges.append({
                    "kept": keeper_id,
                    "removed": goner_id,
                    "activity": a.get("activity", ""),
                    "label_kept": keeper.get("label", ""),
                    "label_removed": goner.get("label", ""),
                    "similarity": _label_similarity(
                        keeper.get("label", ""), goner.get("label", ""),
                    ),
                    "phash_distance": ph_dist,
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

    # ─── Cross-bucket A+ pass — pHash-identical merge across buckets ───
    # Fragment detection can lag the UI transition: tapping a new tab
    # generates a screenshot of the new screen but dumpsys may still
    # report the old Fragment tag. Those nodes land in different buckets
    # above but reference visually-identical screenshots. Allow merging
    # them across buckets when pHash distance == 0 (byte-level identical)
    # AND same activity — safer than a general cross-bucket merge.
    remaining = [n for n in nodes if n.get("screen_id") not in absorbed]
    if phash_threshold >= 0:
        for i, a in enumerate(remaining):
            if a.get("screen_id") in absorbed:
                continue
            ss_a = a.get("screenshot_ref")
            if not ss_a:
                continue
            ph_a = _compute_phash(ss_a)
            if not ph_a:
                continue
            for j in range(i + 1, len(remaining)):
                b = remaining[j]
                if b.get("screen_id") in absorbed:
                    continue
                if (a.get("activity", "") or "") != (b.get("activity", "") or ""):
                    continue
                ss_b = b.get("screenshot_ref")
                if not ss_b or ss_a == ss_b:
                    continue
                ph_b = _compute_phash(ss_b)
                if not ph_b:
                    continue
                if _phash_distance(ph_a, ph_b) != 0:
                    continue
                # Cross-bucket pixel-identical merge
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
                    "phash_distance": 0,
                    "cross_bucket": True,   # traceability tag
                })
                absorbed.add(goner_id)
                keeper.setdefault("merged_from", []).append(goner_id)
                if (not keeper.get("screenshot_ref")) and goner.get("screenshot_ref"):
                    keeper["screenshot_ref"] = goner["screenshot_ref"]
                edges = _rewrite_edges(edges, goner_id, keeper_id)

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
        "phash_threshold": phash_threshold,
        "merges_applied": len(merges),
        "nodes_before": len(nodes),
        "nodes_after": len(new_nodes),
        "merges": merges,
    }

    # P0.1 (2026-04-29): nodes/edges 가 줄었으니 metadata.total_* 도 재계산.
    # 이전 버그: metadata.total_nodes 가 coalesce 전 값(예: 100) 그대로 남아 actual 46 과 불일치.
    try:
        from .metadata_refresh import refresh_metadata
        refresh_metadata(screenmap)
    except Exception as e:
        logger.warning("metadata_refresh after semantic_merge failed: %s", e)

    logger.info(
        "[semantic_merge] %d → %d nodes (%d merges, threshold=%.2f)",
        len(nodes), len(new_nodes), len(merges), threshold,
    )

    return screenmap


def coalesce_file(in_path: str | Path, out_path: str | Path | None = None,
               threshold: float = 0.85, phash_threshold: int = 4) -> dict:
    """File-level convenience wrapper. Reads JSON, runs semantic_merge,
    writes output (defaulting to in-place)."""
    in_p = Path(in_path)
    out_p = Path(out_path) if out_path else in_p

    screenmap = json.loads(in_p.read_text(encoding="utf-8"))
    semantic_merge(screenmap, threshold=threshold, phash_threshold=phash_threshold)
    out_p.write_text(json.dumps(screenmap, indent=2, ensure_ascii=False), encoding="utf-8")
    return screenmap
