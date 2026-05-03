"""Mark ScreenMap nodes that look like infinite-scroll containers.

A node gets ``infinite_scroll = True`` when it shows the structural fingerprint
of a feed/list screen:

  * functional_category is one of {"list", "feed", "media", "search"}, OR
  * the node carries a scrollable container in its UI elements/views, AND
  * the node's activity collected at least N>=2 sibling nodes during walk
    (suggesting different scroll positions of the same activity collapsed
    into multiple screen_ids).

Effect downstream:
  * Coalesce cascade A (semantic_merge) uses a more lenient pHash threshold for
    pairs where both sides have ``infinite_scroll=True`` — see
    ``semantic_merge._is_mergeable`` for the threshold bump.
  * Dashboard / Task Navigator can treat the node as "scrollable feed" — only the
    canonical entry needs a step, item-level interactions are described as
    a separate sub-plan.

This is intentionally heuristic. The runtime signal (Stage 3 scroll counters)
is not yet plumbed into Stage 6; using activity-level node-count instead is a
robust proxy because coalesce-blasted feed screens always cluster under one
activity.
"""

from __future__ import annotations

import logging
from collections import Counter

logger = logging.getLogger(__name__)

_SCROLL_CATEGORIES = {"list", "feed", "media", "search"}
_SCROLL_CLASS_HINTS = (
    "recyclerview", "listview", "scrollview",
    "lazycolumn", "lazyrow", "lazyverticalgrid", "lazyhorizontalgrid",
    "viewpager", "horizontalscroll", "horizontalpager", "pager",
)


def _node_has_scrollable_signal(node: dict) -> bool:
    """True if the node's widgets or views suggest a scrollable container."""
    for el in node.get("widgets", []) or []:
        role = (el.get("role") or "").lower()
        if "scroll" in role:
            return True
    # Some nodes carry the raw view list as 'views' (wireframe/Stage 4 paths).
    for v in node.get("views", []) or []:
        if v.get("scrollable"):
            return True
        cls = (v.get("class") or "").lower()
        if any(kw in cls for kw in _SCROLL_CLASS_HINTS):
            return True
    return False


def _mark_infinite_scroll_nodes(graph: dict, min_siblings: int = 2) -> int:
    """Set ``node['infinite_scroll'] = True`` on candidate feed/list nodes.

    Returns the number of nodes flagged. Modifies ``graph`` in place.
    """
    nodes = graph.get("nodes", []) or []
    if not nodes:
        return 0

    # Bucket nodes by activity so we can compute sibling counts.
    by_activity: Counter[str] = Counter()
    for n in nodes:
        act = n.get("activity") or ""
        if act:
            by_activity[act] += 1

    flagged = 0
    for n in nodes:
        if n.get("infinite_scroll"):
            continue
        cat = (n.get("functional_category") or "").lower()
        category_match = cat in _SCROLL_CATEGORIES
        scroll_signal = _node_has_scrollable_signal(n)
        sibling_count = by_activity.get(n.get("activity") or "", 1)
        # Require either an explicit category or a scroll signal AND multiple
        # siblings (proxy for scroll-position-induced duplication).
        if category_match or (scroll_signal and sibling_count >= min_siblings):
            n["infinite_scroll"] = True
            n.setdefault("scroll_metadata", {}).update({
                "category_match": category_match,
                "scroll_signal": scroll_signal,
                "activity_node_count": sibling_count,
                "marked_by": "stage6_infinite_scroll_heuristic",
            })
            flagged += 1

    if flagged:
        logger.info("[infinite_scroll] flagged %d nodes (of %d)", flagged, len(nodes))
    return flagged
