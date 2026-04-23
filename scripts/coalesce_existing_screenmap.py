"""Retroactively coalesce an already-generated ScreenMap via the D approach.

Usage:
    python scripts/coalesce_existing_screenmap.py <screen_map.json> [--threshold 0.85] [--out OUT]

If --out is omitted, the file is overwritten in place (a .bak copy is saved
alongside). Prints a before/after summary.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# Allow running from repo root without installing
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stage6_screenmap.semantic_merge import semantic_merge


def main() -> int:
    ap = argparse.ArgumentParser(description="Coalesce ScreenMap nodes (D approach)")
    ap.add_argument("screenmap_path", help="Path to screen_map.json")
    ap.add_argument("--threshold", type=float, default=0.85,
                    help="Label similarity threshold (SequenceMatcher ratio), 0-1")
    ap.add_argument("--out", help="Output path (default: overwrite in place with .bak)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Only report what would be merged; don't write")
    args = ap.parse_args()

    in_path = Path(args.screenmap_path)
    if not in_path.exists():
        print(f"error: {in_path} not found", file=sys.stderr)
        return 1

    screenmap = json.loads(in_path.read_text(encoding="utf-8"))
    nodes_before = len(screenmap.get("screen_map", {}).get("graph", {}).get("nodes", []))

    semantic_merge(screenmap, threshold=args.threshold)
    meta = screenmap.get("screen_map", {}).get("metadata", {}).get("semantic_merge") \
        or screenmap.get("metadata", {}).get("semantic_merge", {})
    merges = meta.get("merges_applied", 0)
    nodes_after = meta.get("nodes_after", nodes_before)

    print(f"Input:  {in_path}")
    print(f"Nodes:  {nodes_before} → {nodes_after}  ({'-' if nodes_after < nodes_before else '+'}{abs(nodes_before - nodes_after)})")
    print(f"Merges: {merges}  (threshold={args.threshold})")

    if merges and not args.dry_run:
        print()
        print("=== merge records (first 10) ===")
        for m in meta.get("merges", [])[:10]:
            act = m.get("activity", "").rsplit(".", 1)[-1]
            sim = m.get("similarity", 0)
            print(f"  sim={sim:.2f}  {act:35s}  "
                  f"KEEP [{m.get('label_kept','?'):35s}]  "
                  f"<- REMOVE [{m.get('label_removed','?')}]")

    if args.dry_run:
        print("\n(dry-run: no file written)")
        return 0

    out_path = Path(args.out) if args.out else in_path
    if out_path == in_path:
        # Safety: keep the original around
        bak = in_path.with_suffix(in_path.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(in_path, bak)
            print(f"\nBackup saved: {bak}")
    out_path.write_text(json.dumps(screenmap, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote:  {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
