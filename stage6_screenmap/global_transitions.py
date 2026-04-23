"""Mark shared (to, trigger) edges reachable from ≥ N sources as "global".

Extracted from stage6_screenmap/transformations.py (Step 4 세분화).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def _mark_global_transitions(graph: dict, min_sources: int = 3) -> None:
    """Promote edges whose (to, trigger_widget) appears from many sources to kind=global.

    Intuition: a bottom-nav button or drawer item shows up in every screen, so the
    ScreenMap will have many edges (*-> target) all with the same trigger. Tag those.
    """
    from collections import defaultdict
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in graph.get("edges", []):
        key = (e.get("to", ""), e.get("trigger_widget", "").lower().strip())
        if not key[0] or not key[1]:
            continue
        buckets[key].append(e)

    promoted = 0
    for (to, trig), edges in buckets.items():
        unique_sources = {e.get("from", "") for e in edges}
        if len(unique_sources) >= min_sources:
            for e in edges:
                if e.get("kind") in (None, "navigate"):
                    e["kind"] = "global"
                    promoted += 1
    if promoted:
        logger.info("Promoted %d edges to kind=global", promoted)


