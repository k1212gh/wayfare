"""Quick post-run analyzer — breaks down a ScreenMap's node semantic richness.

Usage: python scripts/analyze_screenmap.py <tour_id>
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def analyze(tour_id: str, workspace_root: str = "workspace") -> None:
    screenmap_path = Path(workspace_root) / tour_id / "output" / "screen_map.json"
    if not screenmap_path.exists():
        sys.exit(f"ScreenMap not found: {screenmap_path}")

    screenmap = json.loads(screenmap_path.read_text(encoding="utf-8"))
    g = screenmap["screen_map"]["graph"]
    nodes = g["nodes"]
    edges = g["edges"]

    print(f"=== {screenmap['screen_map'].get('package_name','?')} ===")
    print(f"nodes={len(nodes)}  edges={len(edges)}  entry={g.get('entry_node','')}")

    # Priority / status breakdown
    prio = Counter(n.get("capture_priority", "-") for n in nodes)
    status = Counter(n.get("status", "-") for n in nodes)

    print(f"\ncapture_priority: {dict(prio)}")
    print(f"status          : {dict(status)}")

    # Actionable vs probed vs declared
    actionable = 0
    has_screenshot = 0
    has_purpose = 0
    has_ui = 0
    for n in nodes:
        if n.get("screenshot_ref"):
            has_screenshot += 1
        if n.get("widgets"):
            has_ui += 1
        if n.get("screenshot_ref") and n.get("widgets"):
            actionable += 1
        if n.get("screen_purpose"):
            has_purpose += 1

    print(f"\nactionable (ss+ui): {actionable}")
    print(f"has_screenshot    : {has_screenshot}")
    print(f"has_widgets   : {has_ui}")
    print(f"has_screen_purpose: {has_purpose}")

    # Priority-A capture rate
    a_total = sum(1 for n in nodes if n.get("capture_priority") == "A")
    a_captured = sum(
        1 for n in nodes
        if n.get("capture_priority") == "A"
        and n.get("screenshot_ref")
        and n.get("widgets")
    )
    if a_total:
        print(f"\n'A' priority capture rate: {a_captured}/{a_total} "
              f"({a_captured*100//a_total}%)")

    # Top 10 most-connected nodes
    in_deg = Counter()
    out_deg = Counter()
    for e in edges:
        in_deg[e.get("to","")] += 1
        out_deg[e.get("from","")] += 1
    hubs = sorted(
        ((n.get("screen_id",""), in_deg[n["screen_id"]], out_deg[n["screen_id"]], n)
         for n in nodes),
        key=lambda x: -(x[1] + x[2]),
    )[:10]
    print("\n=== Top 10 hubs (in+out degree) ===")
    for sid, i, o, n in hubs:
        act = (n.get("activity","") or "").rsplit(".",1)[-1]
        prio = n.get("capture_priority","-")
        st = n.get("status","-")
        print(f"  [{prio}:{st:9s}] in={i:2d} out={o:2d} {act:30s} ({sid[:20]})")

    # Activity-type diversity in captured set (actionable only)
    cap_acts = set()
    for n in nodes:
        if n.get("screenshot_ref") and n.get("widgets"):
            a = (n.get("activity","") or "").rsplit(".",1)[-1]
            if a:
                cap_acts.add(a)
    print(f"\ncaptured distinct activities: {len(cap_acts)}")
    for a in sorted(cap_acts):
        print(f"  {a}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python scripts/analyze_screenmap.py <tour_id>")
    analyze(sys.argv[1])
