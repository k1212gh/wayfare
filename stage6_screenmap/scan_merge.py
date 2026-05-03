"""Flip declared activities to `probed` when manifest scan launched them.

Extracted from stage6_screenmap/transformations.py (Step 4 세분화).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def _apply_manifest_scan(config, graph: dict) -> None:
    """Flip status to 'probed' for every declared activity the scan could launch.

    Input: dynamic/manifest_scan.json — {fqn: {launched: bool, foreground: str}}
    Effect: matching declared nodes get status='probed'. Entry/resolved/unknown
    are left alone (they were touched naturally).
    """
    scan_path = Path(config.dynamic_dir) / "manifest_scan.json"
    if not scan_path.exists():
        return
    try:
        scan = json.loads(scan_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("manifest_scan.json parse failed: %s", e)
        return

    by_activity: dict[str, dict] = {}
    for n in graph.get("nodes", []):
        act = n.get("activity", "")
        if act:
            by_activity[act] = n

    promoted = 0
    fake_launched = 0
    for act, result in scan.items():
        if not result.get("launched"):
            continue
        # 2026-05-03 (P3): launched=True 만으론 부족. 메가커피 같은 redirect
        # 패턴 앱은 17/18 가 focus_mismatch (am start 됐지만 즉시 MainActivity 로
        # 튕김). 진짜 capture 한 노드만 probed 로 마킹.
        if not result.get("captured") or result.get("focus_mismatch"):
            fake_launched += 1
            continue
        node = by_activity.get(act)
        if not node:
            continue
        if node.get("status") == "declared":
            node["status"] = "probed"
            promoted += 1
    if promoted or fake_launched:
        logger.info(
            "Manifest scan: %d nodes promoted to 'probed' (fake/redirect launches skipped: %d)",
            promoted, fake_launched,
        )

    # Also promote classifier-B (plumbing) and classifier-C (deep-link) nodes
    # to `probed` status — they're reachable-by-design (manifest declared +
    # scan intentionally skipped to save time). Without this they stay as
    # `declared` which undercounts true reachability.
    by_prio_promoted = 0
    for n in graph.get("nodes", []):
        if n.get("screen_id") == "system:external_entry":
            continue
        if n.get("status") != "declared":
            continue
        prio = n.get("capture_priority", "")
        if prio in ("B", "C"):
            n["status"] = "probed"
            by_prio_promoted += 1
    if by_prio_promoted:
        logger.info("Classifier promoted %d B/C nodes to status='probed'", by_prio_promoted)


