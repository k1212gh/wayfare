"""Compute edge weights (1 / frequency) from walk transitions.

Extracted from stage6_screenmap/transformations.py (Step 4 세분화).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def _compute_transition_weights(graph: dict, transitions: list[dict],
                          screen_cards: list[dict], walk_screens: list[dict]) -> None:
    """Compute edge weights from walk transition frequency.

    Frequently traversed edges get lower weight (= preferred path).
    weight = 1 / (frequency + 1)
    """
    from collections import Counter

    struct_to_page: dict[str, str] = {}
    for cu in screen_cards:
        struct = cu.get("structure_str", "")
        if struct:
            struct_to_page[struct] = cu["screen_id"]
            struct_to_page[struct[:16]] = cu["screen_id"]

    exp_to_struct: dict[str, str] = {}
    for s in walk_screens:
        ss = s.get("state_str", "")
        struct = s.get("structure_str", "")
        if ss and struct:
            exp_to_struct[ss] = struct

    node_ids = {n["screen_id"] for n in graph.get("nodes", [])}

    def resolve(exp_id):
        if exp_id in node_ids:
            return exp_id
        struct = exp_to_struct.get(exp_id, "")
        if struct:
            return struct_to_page.get(struct) or struct_to_page.get(struct[:16])
        return None

    freq = Counter()
    for t in transitions:
        fn = resolve(t.get("from_screen", ""))
        tn = resolve(t.get("to_screen", ""))
        if fn and tn:
            freq[(fn, tn)] += 1

    for edge in graph.get("edges", []):
        key = (edge["from"], edge["to"])
        # 2026-09-13: walk_transitions 가 접으면서 센 frequency 가 있으면 그 값을 존중 (여기 resolve 는 state_str 키라 0 이 나오기 쉬움)
        count = max(freq.get(key, 0), int(edge.get("frequency") or 0))
        edge["weight"] = round(1.0 / (count + 1), 3)
        edge["frequency"] = count

    weighted = sum(1 for e in graph["edges"] if e.get("frequency", 0) > 0)
    logger.info("Edge weights: %d/%d edges have walk frequency", weighted, len(graph["edges"]))
