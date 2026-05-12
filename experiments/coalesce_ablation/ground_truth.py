"""사람이 라벨링한 ground truth 쌍으로 각 정책의 precision/recall 측정.

ground truth 파일 포맷 (data/ground_truth_pairs.jsonl, 한 줄 = 한 쌍):
  {
    "tour_id": "e88edb42",
    "screen_a": "state_0019.json",
    "screen_b": "state_0023.json",
    "same_screen": true,
    "rationale": "같은 메뉴 화면, 시간 텍스트만 다름"
  }

CLI:
  python -m experiments.coalesce_ablation.ground_truth
  python -m experiments.coalesce_ablation.ground_truth --label   # 라벨링 도우미 출력
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from stage3_walk.screen_signer import ScreenSigner
from .policies import POLICIES
from .metrics import PolicyResult, format_table
from .replay import WORKSPACE, REPORTS


DATA = Path(__file__).resolve().parent / "data"
GT_FILE = DATA / "ground_truth_pairs.jsonl"


def load_screen_signature(tour_id: str, state_filename: str):
    """잡 + state 파일 이름으로 fingerprint 재계산."""
    sp = WORKSPACE / tour_id / "dynamic" / "states" / state_filename
    if not sp.exists():
        raise FileNotFoundError(sp)
    d = json.loads(sp.read_bytes())
    hasher = ScreenSigner()
    return hasher.compute_fingerprint(
        d.get("views", []),
        d.get("activity", ""),
        d.get("screenshot_path", "") or "",
        eager=True,
    )


def load_pairs() -> list[dict]:
    """ground truth jsonl 읽기. 파일 없으면 빈 list."""
    if not GT_FILE.exists():
        return []
    pairs = []
    for line in GT_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            pairs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return pairs


def evaluate() -> None:
    pairs = load_pairs()
    if not pairs:
        print(f"no ground truth found at {GT_FILE}", file=sys.stderr)
        print("샘플 라벨링부터 시작하세요. `--label` 옵션으로 도우미 출력 가능.",
              file=sys.stderr)
        sys.exit(1)

    print(f"loaded {len(pairs)} labeled pairs")

    # 각 쌍의 fingerprint 캐시
    fp_cache: dict[tuple[str, str], object] = {}
    for pair in pairs:
        for k in ("screen_a", "screen_b"):
            key = (pair["tour_id"], pair[k])
            if key not in fp_cache:
                try:
                    fp_cache[key] = load_screen_signature(*key)
                except FileNotFoundError:
                    fp_cache[key] = None

    # 정책별 평가
    rows = []
    for p in POLICIES:
        r = PolicyResult(policy_name=p.name)
        for pair in pairs:
            fp_a = fp_cache[(pair["tour_id"], pair["screen_a"])]
            fp_b = fp_cache[(pair["tour_id"], pair["screen_b"])]
            if fp_a is None or fp_b is None:
                continue
            r.n_pairs_evaluated += 1
            predicted = p.matcher(fp_a, fp_b)
            truth = bool(pair["same_screen"])
            if predicted:
                r.n_pairs_matched += 1
            if predicted and truth:
                r.true_positive += 1
            elif predicted and not truth:
                r.false_positive += 1
            elif not predicted and not truth:
                r.true_negative += 1
            else:
                r.false_negative += 1
        rows.append(r.summary_row())

    print("\n=== Ground truth 기반 precision/recall ===")
    print(format_table(
        rows,
        ["policy", "n_pairs_evaluated", "TP", "FP", "TN", "FN",
         "precision", "recall", "f1"],
    ))

    out = REPORTS / "ground_truth_eval.json"
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nsaved {out}")


def label_helper() -> None:
    """라벨링 도우미: workspace 의 잡 → state 파일 쌍 추천 + screenshot 경로 출력.

    사람이 두 screenshot 을 본 후 같은 화면인지 라벨링해서 jsonl 에 append.
    """
    print("# Ground truth 라벨링 도우미")
    print(f"# 라벨링 결과를 {GT_FILE} 에 한 줄씩 JSON 으로 추가하세요.")
    print("# 포맷: {\"tour_id\":\"...\",\"screen_a\":\"state_0001.json\","
          "\"screen_b\":\"state_0002.json\",\"same_screen\":true,"
          "\"rationale\":\"...\"}")
    print()
    print("# 워크스페이스의 잡들:")
    if not WORKSPACE.exists():
        print(f"# (no workspace at {WORKSPACE})")
        return
    for jp in sorted(WORKSPACE.iterdir()):
        sd = jp / "dynamic" / "states"
        if sd.exists():
            n = len(list(sd.glob("state_*.json")))
            print(f"#   {jp.name}: {n} states")


def cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", action="store_true",
                    help="ground truth 라벨링 도우미 출력")
    args = ap.parse_args()
    if args.label:
        label_helper()
    else:
        evaluate()


if __name__ == "__main__":
    cli()
