"""Export demo data for interactive flow page from a completed tour.

Usage: python scripts/export_demo_data.py <tour_id> > demo_data.json
"""
import base64
import json
import sys
from pathlib import Path


def b64_image(p: Path, max_kb: int = 200) -> str:
    """Read image and return base64 data URL. Returns empty if too large/missing."""
    if not p.exists():
        return ""
    data = p.read_bytes()
    if len(data) > max_kb * 1024:
        return ""
    ext = p.suffix.lower().lstrip(".")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def main(tour_id: str) -> None:
    root = Path(__file__).parent.parent / "workspace" / tour_id
    if not root.exists():
        print(f"Tour not found: {root}", file=sys.stderr)
        sys.exit(1)

    out: dict = {"tour_id": tour_id}

    # Stage 1
    out["metadata"] = json.loads((root / "apk" / "metadata.json").read_text(encoding="utf-8"))
    out["pipeline_state"] = json.loads((root / "pipeline_state.json").read_text(encoding="utf-8"))

    # Stage 2
    static = json.loads((root / "static" / "analysis.json").read_text(encoding="utf-8"))
    out["static"] = {
        "package_name": static.get("package_name"),
        "entry_activity": static.get("entry_activity"),
        "activities": [a.get("name") for a in static.get("activities", [])],
        "services_count": len(static.get("services", [])),
        "receivers_count": len(static.get("receivers", [])),
        "providers_count": len(static.get("providers", [])),
        "permissions_count": len(static.get("permissions", [])),
    }

    # Stage 3 — walk (sample states with screenshots)
    expl = json.loads((root / "dynamic" / "walk.json").read_text(encoding="utf-8"))
    states = expl.get("states", [])
    transitions = expl.get("transitions", [])

    # Pick up to 6 unique-screenshot sample states
    sample_screens = []
    seen_screens = set()
    for s in states:
        ssp = s.get("screenshot_path", "")
        if not ssp:
            continue
        screen_name = Path(ssp).name
        if screen_name in seen_screens:
            continue
        seen_screens.add(screen_name)
        # Try multiple locations
        candidates = [
            root / "dynamic" / "screenshots" / screen_name,
            Path(ssp),
        ]
        img_data = ""
        for c in candidates:
            img_data = b64_image(c, max_kb=300)
            if img_data:
                break
        if not img_data:
            # try analysis/screens (already resized, smaller)
            structure = s.get("structure_str", "")[:16]
            if structure:
                resized = root / "analysis" / "screens" / f"{structure}.jpg"
                img_data = b64_image(resized, max_kb=150)
        if not img_data:
            continue
        sample_screens.append({
            "state_str": s.get("state_str", "")[:16],
            "activity": s.get("activity", ""),
            "fragment_class": s.get("fragment_class", ""),
            "view_count": len(s.get("views", [])),
            "screenshot_b64": img_data,
        })
        if len(sample_screens) >= 6:
            break

    out["walk"] = {
        "total_screens": len(states),
        "total_transitions": len(transitions),
        "coverage": expl.get("coverage", {}),
        "sample_screens": sample_screens,
        "sample_transitions": [
            {
                "from_activity": t.get("from_activity", ""),
                "to_activity": t.get("to_activity", ""),
                "trigger": t.get("trigger", {}),
            }
            for t in transitions[:5]
        ],
    }

    # Stage 4 — context units
    cu = json.loads((root / "analysis" / "screen_cards.json").read_text(encoding="utf-8"))
    out["screen_cards"] = {
        "total": len(cu),
        "sample": [
            {
                "screen_id": u.get("screen_id"),
                "activity_name": u.get("activity_name"),
                "fragment": u.get("fragment"),
                "node_type": u.get("node_type"),
                "widget_count": u.get("widget_count"),
                "available_actions": (u.get("available_actions") or [])[:5],
                "label_hint": u.get("label_hint"),
                "cleaned_xml_preview": (u.get("cleaned_xml") or "")[:400],
            }
            for u in cu[:3]
        ],
    }

    # Stage 6 — final ScreenMap
    screenmap = json.loads((root / "output" / "screen_map.json").read_text(encoding="utf-8"))
    afg = screenmap["screen_map"]
    g = afg["graph"]
    nodes = g.get("nodes", [])
    edges = g.get("edges", [])

    # Slim nodes for the graph viz
    slim_nodes = []
    for n in nodes:
        slim_nodes.append({
            "id": n.get("screen_id"),
            "label": n.get("label") or n.get("activity", "").split(".")[-1],
            "activity": n.get("activity"),
            "node_type": n.get("node_type"),
            "category": n.get("functional_category", "other"),
            "purpose": n.get("screen_purpose", ""),
            "description": (n.get("description") or "")[:300],
            "capture_priority": n.get("capture_priority", ""),
            "status": n.get("status", ""),
            "confidence": n.get("confidence", ""),
            "data_displayed": n.get("data_displayed", []),
        })

    slim_edges = []
    for e in edges:
        slim_edges.append({
            "from": e.get("from_screen_id") or e.get("from"),
            "to": e.get("to_screen_id") or e.get("to"),
            "trigger": e.get("trigger_label") or e.get("trigger", ""),
            "edge_type": e.get("edge_type", ""),
            "weight": e.get("weight", 1),
        })

    out["screenmap"] = {
        "app_name": afg.get("app_name"),
        "package_name": afg.get("package_name"),
        "version": afg.get("version"),
        "generated_at": afg.get("generated_at"),
        "metadata": afg.get("metadata", {}),
        "entry_node": g.get("entry_node"),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": slim_nodes,
        "edges": slim_edges,
    }

    # Pick a richer sample node (with primitives + screen_purpose)
    rich_node = None
    for n in nodes:
        if n.get("screen_purpose") and n.get("primitives") and n.get("data_displayed"):
            rich_node = n
            break
    if rich_node:
        out["screenmap"]["rich_sample_node"] = {
            "screen_id": rich_node.get("screen_id"),
            "activity": rich_node.get("activity"),
            "label": rich_node.get("label"),
            "functional_category": rich_node.get("functional_category"),
            "screen_purpose": rich_node.get("screen_purpose"),
            "description": rich_node.get("description"),
            "data_displayed": rich_node.get("data_displayed", []),
            "primitives": rich_node.get("primitives", {}),
            "capture_priority": rich_node.get("capture_priority"),
            "status": rich_node.get("status"),
        }

    # Validation report
    rpt_path = root / "output" / "report.json"
    if rpt_path.exists():
        rpt = json.loads(rpt_path.read_text(encoding="utf-8"))
        out["report"] = {
            "summary": rpt.get("summary", {}),
            "issues_sample": (rpt.get("issues") or [])[:5],
        }

    # Print final JSON to stdout
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "ab6e4e61")
