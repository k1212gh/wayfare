"""Walk diagnostics CLI — walk 끝난 후 'why didn't it go deeper' 답.

Usage:
    python scripts/walk_diag.py <tour_id> [--format md|json]
    python scripts/walk_diag.py 1dd3e899
    python scripts/walk_diag.py 1dd3e899 --format json > diag.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from stage3_walk.walk_analyzer import analyze_walk  # noqa: E402


def render_md(r: dict) -> str:
    out = []
    out.append(f"# Walk Diagnostics — `{Path(r['tour_dir']).name}`\n")

    totals = r["totals"]
    out.append(f"**Totals**: raw_screens={totals['raw_screens']} · "
               f"transitions={totals['transitions']} · "
               f"walk_screens={totals['walk_screens']}\n")

    # Hints — 가장 위에 (사용자가 처음 보는 곳)
    out.append("## 🔍 Root cause hints (evidence-based)\n")
    for h in r["root_cause_hints"]:
        out.append(f"- {h}")
    out.append("")

    # Fragment distribution
    fd = r["fragment_distribution"]
    out.append("## Fragment distribution (편향 감지)\n")
    out.append("| Fragment | Count | Ratio |")
    out.append("|----------|------:|------:|")
    for f in fd["by_fragment"][:15]:
        flag = " ⚠" if f["ratio"] > 0.4 else ""
        out.append(f"| `{f['fragment'] or '(unknown)'}` | {f['count']} | {f['ratio']*100:.1f}%{flag} |")
    out.append(f"\nUnique fragments: **{fd['unique_fragments']}**\n")

    # Entry coverage
    out.append("## Entry coverage (FAB / drawer / overflow / tab / settings)\n")
    out.append("| Entry | Seen states | Clickable states | Tapped |")
    out.append("|-------|------------:|-----------------:|-------:|")
    for et, info in r["entry_coverage"].items():
        seen = info["screens_with_view"]
        clk = info["screens_with_clickable"]
        tap = info["tapped"]
        flag = ""
        if seen >= 3 and clk == 0:
            flag = " ❌ extraction failed"
        elif seen >= 5 and tap == 0:
            flag = " ⚠ never tapped"
        out.append(f"| `{et}` | {seen} | {clk} | {tap}{flag} |")
    out.append("")

    # Action distribution
    out.append("## Action distribution (top 20)\n")
    out.append("| Count | Event |")
    out.append("|------:|-------|")
    for a in r["action_distribution"]:
        out.append(f"| {a['count']} | `{a['event'][:90]}` |")
    out.append("")

    # Missed entry candidates
    me = r["missed_entry_candidates"]
    if me:
        out.append("## Missed entry candidates (clickable=False but suspicious)\n")
        out.append("| Class | RID | Desc | Pattern | N |")
        out.append("|-------|-----|------|---------|--:|")
        for m in me[:10]:
            cls = (m.get("class") or "").rsplit(".", 1)[-1][:30]
            rid = (m.get("resource_id") or "")[:25]
            desc = (m.get("content_desc") or m.get("text") or "")[:25]
            pat = m.get("matched_pattern", "")
            n = m.get("occurrences", 0)
            out.append(f"| `{cls}` | `{rid}` | {desc} | `{pat}` | {n} |")
        out.append("")

    # Visit concentration
    vc = r["visit_concentration"]
    out.append("## Visit concentration\n")
    out.append(f"- Top 5 states 가 액션 **{vc['top_5_concentration_ratio']*100:.1f}%** 차지")
    out.append(f"- Unique from-states: **{vc['unique_from_screens']}**")
    for s in vc["top_5"]:
        out.append(f"  - `{s['state']}`: {s['from_count']} actions")
    out.append("")

    # Stall events
    se = r["stall_events"]
    out.append("## Stall events\n")
    out.append(f"- back presses: **{se['back_presses']}**")
    out.append(f"- max consecutive same action: **{se['max_consecutive_same_action']}**\n")

    # Activity coverage
    ac = r["activity_coverage"]
    if ac.get("declared"):
        ratio = (ac.get("visited") or 0) / ac["declared"]
        out.append("## Activity coverage (manifest)\n")
        out.append(f"- declared **{ac['declared']}** · visited **{ac.get('visited')}** "
                   f"({ratio*100:.1f}%)")
        if ac.get("unvisited_declared"):
            out.append("- unvisited (top 10):")
            for a in ac["unvisited_declared"]:
                out.append(f"  - `{a.rsplit('.',1)[-1]}`")
        out.append("")

    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tour_id", help="tour id under workspace/")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("--workspace", default="workspace")
    args = ap.parse_args()

    tour_dir = REPO / args.workspace / args.tour_id
    if not tour_dir.exists():
        print(f"tour dir not found: {tour_dir}", file=sys.stderr)
        return 1

    report = analyze_walk(tour_dir)

    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(render_md(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
