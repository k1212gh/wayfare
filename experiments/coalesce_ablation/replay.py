"""기존 워크스페이스 잡의 state dump 를 입력으로 각 정책을 시뮬레이션.

ground truth 가 없으니 각 정책이 만드는 canonical 수만 비교하고, 정책 간
"두 캡처를 같은 canonical 로 묶었는지" 결정을 jaccard 로 비교한다.

CLI:
  python -m experiments.coalesce_ablation.replay <tour_id> [tour_id ...]
  python -m experiments.coalesce_ablation.replay --all   # workspace 모든 잡

출력:
  reports/replay_<tour_id>.json  — 각 정책의 canonical 수 + 정책간 일치율
  stdout — ASCII 표
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from stage3_walk.screen_signer import ScreenSignature, ScreenSigner
from .policies import POLICIES, Policy
from .metrics import PolicyResult, jaccard, format_table


WORKSPACE = Path(__file__).resolve().parents[2] / "workspace"
REPORTS = Path(__file__).resolve().parent / "reports"
REPORTS.mkdir(exist_ok=True)


def load_fingerprints_from_tour(tour_id: str) -> list[ScreenSignature]:
    """잡의 state dump 를 읽어 매 캡처의 fingerprint 를 재계산.

    각 캡처는 structure_str 가 이미 디스크에 저장돼 있어 L1 은 재계산 불필요.
    L2 (perceptual hash) 와 L3 (graph embedding) 는 production hasher 로
    다시 계산해 SrateFingerprint 채움.
    """
    states_dir = WORKSPACE / tour_id / "dynamic" / "states"
    if not states_dir.exists():
        raise FileNotFoundError(f"no states dir: {states_dir}")
    hasher = ScreenSigner()
    fps: list[ScreenSignature] = []
    for sf in sorted(states_dir.glob("state_*.json")):
        d = json.loads(sf.read_bytes())
        ss_path = d.get("screenshot_path", "") or ""
        # Ablation 비교에는 모든 신호 채워야 — eager.
        fp = hasher.compute_fingerprint(
            d.get("views", []),
            d.get("activity", ""),
            ss_path,
            eager=True,
        )
        # 디스크에 있는 structure_str 와 일치하는지 sanity check
        disk_l1 = d.get("structure_str", "")
        if disk_l1 and fp.structural_hash != disk_l1:
            # production hasher 가 코드 변경된 경우 디스크 hash 와 다를 수 있음.
            # 이 실험에선 production hasher 의 결과를 우선.
            pass
        fps.append(fp)
    return fps


def assign_canonicals(fps: list[ScreenSignature], policy: Policy) -> list[int]:
    """각 fingerprint 에 canonical 인덱스 부여. 같은 화면이면 같은 idx.

    O(N²) — N=수백 캡처면 충분히 빠름. union-find 안 써도 OK.
    """
    canonicals: list[int] = []
    known: list[ScreenSignature] = []
    for fp in fps:
        matched = -1
        for i, known_fp in enumerate(known):
            if policy.matcher(fp, known_fp):
                matched = i
                break
        if matched < 0:
            known.append(fp)
            canonicals.append(len(known) - 1)
        else:
            canonicals.append(matched)
    return canonicals


def run_replay_for_tour(tour_id: str) -> dict:
    """한 잡에 대해 모든 정책 실행. 결과 dict 반환."""
    print(f"\n=== Tour {tour_id} ===")
    fps = load_fingerprints_from_tour(tour_id)
    print(f"  loaded {len(fps)} fingerprints")

    per_policy: dict[str, list[int]] = {}
    results: list[PolicyResult] = []
    for p in POLICIES:
        cans = assign_canonicals(fps, p)
        per_policy[p.name] = cans
        r = PolicyResult(
            policy_name=p.name,
            n_screens=len(fps),
            n_canonicals=len(set(cans)),
        )
        results.append(r)

    # 정책 간 일치율 — 같은 캡처 쌍에 대해 두 정책이 같은 결정 (같은 canonical
    # 로 묶었는지 vs 아닌지) 을 내렸는지 비율.
    pair_agreement: dict[tuple[str, str], float] = {}
    n = len(fps)
    if n >= 2:
        for i in range(len(POLICIES)):
            for j in range(i + 1, len(POLICIES)):
                pa = POLICIES[i].name
                pb = POLICIES[j].name
                ca = per_policy[pa]
                cb = per_policy[pb]
                same = 0
                total = 0
                for x in range(n):
                    for y in range(x + 1, n):
                        same_a = ca[x] == ca[y]
                        same_b = cb[x] == cb[y]
                        if same_a == same_b:
                            same += 1
                        total += 1
                pair_agreement[(pa, pb)] = same / total if total else 1.0

    return {
        "tour_id": tour_id,
        "n_screens": len(fps),
        "per_policy_canonicals": {p.policy_name: p.n_canonicals for p in results},
        "pair_agreement": {f"{a}__vs__{b}": v for (a, b), v in pair_agreement.items()},
    }


def cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("tour_ids", nargs="*", help="잡 ID 들. --all 이면 무시")
    ap.add_argument("--all", action="store_true", help="workspace 의 모든 잡 처리")
    args = ap.parse_args()

    if args.all:
        tour_ids = sorted([p.name for p in WORKSPACE.iterdir()
                          if p.is_dir() and (p / "dynamic" / "states").exists()])
    else:
        tour_ids = args.tour_ids

    if not tour_ids:
        print("no tours found", file=sys.stderr)
        sys.exit(1)

    all_results = []
    for jid in tour_ids:
        try:
            r = run_replay_for_tour(jid)
            all_results.append(r)
        except FileNotFoundError as e:
            print(f"skip {jid}: {e}", file=sys.stderr)
            continue

    # canonical 수 요약 표
    print("\n=== canonical 수 (per policy, per tour) ===")
    rows = []
    for r in all_results:
        row = {"tour": r["tour_id"], "n": r["n_screens"]}
        row.update(r["per_policy_canonicals"])
        rows.append(row)
    cols = ["tour", "n"] + [p.name for p in POLICIES]
    print(format_table(rows, cols))

    # 보고서 저장
    out = REPORTS / "replay_summary.json"
    out.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nsaved {out}")


if __name__ == "__main__":
    cli()
