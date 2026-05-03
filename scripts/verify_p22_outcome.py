"""P2.2 검증 — primitive_detector + screenmap_annotator 만 다시 돌려서 outcome_hint 채움.

벼경된 sequencing 검증: screenmap_annotator 가 primitives 를 보고 primitive_outcomes
를 반환하는지. 사용:

    python scripts/verify_p22_outcome.py b7b152fd
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import config as cfg_module  # loads .env

from config import PipelineConfig  # noqa: E402
from stage6_screenmap.primitive_detector import detect_primitives_for_screenmap  # noqa: E402
from stage6_screenmap.metadata_refresh import refresh_metadata  # noqa: E402
from stage5_annotate.screenmap_annotator import annotate_screenmap  # noqa: E402


def main(tour_id: str):
    cfg = PipelineConfig(tour_id=tour_id)
    cfg.ensure_dirs()
    screenmap_path = cfg.output_dir / cfg.screenmap_output_filename
    if not screenmap_path.exists():
        print(f"[err] {screenmap_path} missing")
        return 1

    bak = screenmap_path.with_suffix(".before_p22.bak.json")
    bak.write_text(screenmap_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[bak] {bak.name}")

    screenmap = json.loads(screenmap_path.read_text(encoding="utf-8"))
    g = screenmap["screen_map"]["graph"]
    print(f"[before] nodes={len(g['nodes'])} edges={len(g['edges'])}")

    n_with_prim_before = sum(1 for n in g["nodes"] if n.get("primitives"))
    n_outcome_before = sum(1 for e in g["edges"] if e.get("outcome"))
    n_hint_before = sum(
        1 for n in g["nodes"]
        for items in (n.get("primitives") or {}).values()
        for it in items if isinstance(it, dict) and it.get("outcome_hint")
    )
    print(f"[before] with_primitives={n_with_prim_before} edge.outcome={n_outcome_before} hint={n_hint_before}")

    # 1. primitive_detector
    summary = detect_primitives_for_screenmap(screenmap, tour_dir=cfg.tour_dir)
    print(f"[primitives] {summary}")
    n_with_prim_after = sum(1 for n in g["nodes"] if n.get("primitives"))
    print(f"[primitives] with_primitives={n_with_prim_after}")

    # save before screenmap_annotator (screenmap_annotator reads from disk)
    screenmap_path.write_text(json.dumps(screenmap, indent=2, ensure_ascii=False), encoding="utf-8")

    # 2. screenmap_annotator — primitive_outcomes 와 edge_outcomes 추출
    print("[annotate] running screenmap_annotator (primitives 가 보임)...")
    annotate_screenmap(cfg)

    # 3. refresh_metadata
    screenmap = json.loads(screenmap_path.read_text(encoding="utf-8"))
    g = screenmap["screen_map"]["graph"]
    refresh_metadata(screenmap)
    screenmap_path.write_text(json.dumps(screenmap, indent=2, ensure_ascii=False), encoding="utf-8")

    n_outcome_after = sum(1 for e in g["edges"] if e.get("outcome"))
    n_hint_after = sum(
        1 for n in g["nodes"]
        for items in (n.get("primitives") or {}).values()
        for it in items if isinstance(it, dict) and it.get("outcome_hint")
    )
    print(f"[after] nodes={len(g['nodes'])} edges={len(g['edges'])}")
    print(f"[after] edge.outcome={n_outcome_after} primitive.outcome_hint={n_hint_after}")
    samples = []
    for n in g["nodes"]:
        for ptype, items in (n.get("primitives") or {}).items():
            for it in items:
                if isinstance(it, dict) and it.get("outcome_hint"):
                    samples.append(f"{ptype}:{it.get('id','?')} → {it['outcome_hint']}")
                    if len(samples) >= 5:
                        break
            if len(samples) >= 5:
                break
        if len(samples) >= 5:
            break
    print("[samples]")
    for s in samples:
        print(f"  - {s}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python scripts/verify_p22_outcome.py <tour_id>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
