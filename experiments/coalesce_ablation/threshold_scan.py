"""L2 (pHash distance) 와 L3 (cosine similarity) 임계값 sensitivity 분석.

같은 잡 fingerprint 입력에 대해 임계값만 scan 해서 canonical 수가 어떻게
변하는지 측정. "임계값 5/10/15 가 진짜 의미 있는가" 에 대한 데이터 기반 답.

CLI:
  python -m experiments.coalesce_ablation.threshold_scan <tour_id>

출력:
  reports/threshold_scan_<tour_id>.json
  stdout — scan 곡선 ASCII 표
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from stage3_walk.screen_signer import ScreenSignature
from .replay import load_fingerprints_from_tour, assign_canonicals, REPORTS
from .policies import Policy, l2_only, l3_only, l1_authoritative
from .metrics import format_table


# 임계값 scan 그리드
PHASH_GRID = [2, 4, 6, 8, 10, 12, 14, 16, 18, 20]
GNN_GRID = [0.70, 0.75, 0.80, 0.82, 0.85, 0.90, 0.92, 0.95, 0.98]


def make_l2_policy(threshold: int) -> Policy:
    return Policy(
        name=f"L2_thr={threshold}",
        matcher=lambda a, b, phash_threshold=threshold, gnn_threshold=0.82:
            l2_only(a, b, phash_threshold, gnn_threshold),
        description=f"L2 단독, 거리 ≤ {threshold}",
    )


def make_l3_policy(threshold: float) -> Policy:
    return Policy(
        name=f"L3_thr={threshold:.2f}",
        matcher=lambda a, b, phash_threshold=10, gnn_threshold=threshold:
            l3_only(a, b, phash_threshold, gnn_threshold),
        description=f"L3 단독, cosine ≥ {threshold}",
    )


def make_authoritative_policy(phash_t: int, gnn_t: float) -> Policy:
    return Policy(
        name=f"AUTH_p{phash_t}_g{gnn_t:.2f}",
        matcher=lambda a, b, phash_threshold=phash_t, gnn_threshold=gnn_t:
            l1_authoritative(a, b, phash_threshold, gnn_threshold),
        description=f"L1 authoritative, fallback pHash≤{phash_t} / cosine≥{gnn_t}",
    )


def scan_for_tour(tour_id: str) -> dict:
    print(f"\n=== Threshold scan: {tour_id} ===")
    fps = load_fingerprints_from_tour(tour_id)
    print(f"  loaded {len(fps)} fingerprints")

    # L2 scan
    l2_rows = []
    for thr in PHASH_GRID:
        pol = make_l2_policy(thr)
        cans = assign_canonicals(fps, pol)
        l2_rows.append({
            "phash_threshold": thr,
            "n_canonicals": len(set(cans)),
            "reduction_ratio": round(1 - len(set(cans)) / max(1, len(fps)), 3),
        })

    # L3 scan
    l3_rows = []
    for thr in GNN_GRID:
        pol = make_l3_policy(thr)
        cans = assign_canonicals(fps, pol)
        l3_rows.append({
            "gnn_threshold": thr,
            "n_canonicals": len(set(cans)),
            "reduction_ratio": round(1 - len(set(cans)) / max(1, len(fps)), 3),
        })

    # Authoritative (L1 + fallback) scan — L1 의 effect 가 dominant 일 것
    auth_rows = []
    for phash_t in PHASH_GRID[::2]:   # subset
        for gnn_t in GNN_GRID[::2]:   # subset
            pol = make_authoritative_policy(phash_t, gnn_t)
            cans = assign_canonicals(fps, pol)
            auth_rows.append({
                "phash_threshold": phash_t,
                "gnn_threshold": gnn_t,
                "n_canonicals": len(set(cans)),
            })

    print("\n--- L2 단독 scan ---")
    print(format_table(l2_rows, ["phash_threshold", "n_canonicals", "reduction_ratio"]))
    print("\n--- L3 단독 scan ---")
    print(format_table(l3_rows, ["gnn_threshold", "n_canonicals", "reduction_ratio"]))
    print("\n--- L1 authoritative + fallback scan ---")
    print(format_table(auth_rows, ["phash_threshold", "gnn_threshold", "n_canonicals"]))

    return {
        "tour_id": tour_id,
        "n_screens": len(fps),
        "l2_scan": l2_rows,
        "l3_scan": l3_rows,
        "auth_scan": auth_rows,
    }


def cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("tour_id", help="잡 ID")
    args = ap.parse_args()
    r = scan_for_tour(args.tour_id)
    out = REPORTS / f"threshold_scan_{args.tour_id}.json"
    out.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nsaved {out}")


if __name__ == "__main__":
    cli()
