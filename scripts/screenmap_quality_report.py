"""ScreenMap 품질 리포트 CLI — Phase 1 P1.5.

Usage:
    python scripts/screenmap_quality_report.py --tour <tour_id> [--format json|md]

각 잡의 ScreenMap metadata 를 framework-agnostic 하게 측정. CI 회귀 검증 / baseline
비교 / DeskClock·Mattermost·Calendar 일괄 비교 등에 사용.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / "workspace"
sys.path.insert(0, str(ROOT))

from stage6_screenmap.metadata_refresh import refresh_metadata  # noqa: E402


def collect(tour_id: str) -> dict:
    tour_dir = WORKSPACE / tour_id
    screenmap_path = tour_dir / "output" / "screen_map.json"
    if not screenmap_path.exists():
        return {"tour_id": tour_id, "error": f"ScreenMap not found at {screenmap_path}"}

    screenmap = json.loads(screenmap_path.read_text(encoding="utf-8"))
    static_path = tour_dir / "static" / "analysis.json"
    static_info = (
        json.loads(static_path.read_text(encoding="utf-8"))
        if static_path.exists() else None
    )
    md = refresh_metadata(screenmap, static_info=static_info)

    apk_meta = {}
    apk_meta_path = tour_dir / "apk" / "metadata.json"
    if apk_meta_path.exists():
        apk_meta = json.loads(apk_meta_path.read_text(encoding="utf-8"))

    return {
        "tour_id": tour_id,
        "package": apk_meta.get("package_name", ""),
        "framework": apk_meta.get("framework", ""),
        "total_nodes": md.get("total_nodes", 0),
        "total_edges": md.get("total_edges", 0),
        "actionable_nodes": md.get("actionable_nodes", 0),
        "plannable_nodes": md.get("plannable_nodes", 0),
        "reachable_count": md.get("reachable_count", 0),
        "orphan_nodes": md.get("orphan_nodes", 0),
        "dead_end_nodes": md.get("dead_end_nodes", 0),
        "validation_issues": md.get("validation_issues", 0),
        "issue_severity": md.get("issue_severity", {}),
        "activity_coverage": md.get("activity_coverage"),
    }


def render_md(rows: list[dict]) -> str:
    if not rows:
        return "_no tours_"
    lines = ["# ScreenMap Quality Report", ""]
    lines.append("| Tour | Pkg | FW | Nodes | Edges | Actionable | Plannable | Reach | Orphan | Dead | Issues (H/M/L) | ActCov |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['tour_id']} | — | — | — | — | — | — | — | — | — | {r['error']} | — |")
            continue
        sev = r.get("issue_severity") or {}
        ac = r.get("activity_coverage")
        ac_s = f"{ac:.2f}" if isinstance(ac, (int, float)) else "—"
        lines.append(
            f"| `{r['tour_id']}` | {r['package']} | {r['framework']} | "
            f"{r['total_nodes']} | {r['total_edges']} | "
            f"{r['actionable_nodes']} | {r['plannable_nodes']} | "
            f"{r['reachable_count']} | {r['orphan_nodes']} | {r['dead_end_nodes']} | "
            f"{sev.get('high',0)}/{sev.get('medium',0)}/{sev.get('low',0)} | {ac_s} |"
        )
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tour", action="append", default=[],
                   help="tour id. 여러 번 사용 가능. 생략하면 workspace 의 모든 잡.")
    p.add_argument("--format", choices=["json", "md"], default="md")
    args = p.parse_args()

    tour_ids = args.tour
    if not tour_ids:
        if WORKSPACE.exists():
            tour_ids = sorted(
                d.name for d in WORKSPACE.iterdir()
                if d.is_dir() and (d / "output" / "screen_map.json").exists()
            )

    rows = [collect(jid) for jid in tour_ids]

    if args.format == "json":
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        print(render_md(rows))


if __name__ == "__main__":
    main()
