"""사람 라벨링 전 자동 분류 단계.

자동으로 라벨 가능한 케이스를 먼저 처리해 사람 라벨링 부담 감소:
  - 같은 screenshot path → SAME 확정
  - 다른 activity → 거의 확실 DIFFERENT (sanity)
  - byte md5 동일 → SAME 확정 (다른 path 라도 픽셀 같음)

남은 쌍 = 사람 라벨링 필요. needs_review 목록 출력.

CLI:
  python -m experiments.coalesce_ablation.auto_prelabel
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


DATA = Path(__file__).resolve().parent / "data"
INPUT = DATA / "pairs_to_label.jsonl"
PRELABELED = DATA / "pairs_prelabeled.jsonl"
NEEDS_REVIEW = DATA / "pairs_needs_review.jsonl"


def load_state(tour_id: str, state_file: str) -> dict | None:
    p = Path(f"./workspace/{tour_id}/dynamic/states/{state_file}")
    if not p.exists():
        return None
    try:
        return json.loads(p.read_bytes())
    except Exception:
        return None


def md5_of(path: str) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        return hashlib.md5(p.read_bytes()).hexdigest()
    except Exception:
        return ""


def main():
    pairs = []
    for line in INPUT.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pairs.append(json.loads(line))

    prelabeled: list[dict] = []
    needs_review: list[dict] = []

    for p in pairs:
        sa = load_state(p["tour_id"], p["screen_a"])
        sb = load_state(p["tour_id"], p["screen_b"])
        if not sa or not sb:
            continue

        ss_a = sa.get("screenshot_path", "") or ""
        ss_b = sb.get("screenshot_path", "") or ""
        act_a = sa.get("activity", "") or ""
        act_b = sb.get("activity", "") or ""

        decision = None
        reason = None

        if ss_a and ss_a == ss_b:
            decision, reason = True, "auto: same screenshot path"
        elif ss_a and ss_b and md5_of(ss_a) == md5_of(ss_b) and md5_of(ss_a):
            decision, reason = True, "auto: byte-identical screenshot (md5)"
        elif p["category"] == "sanity_different_activity" and act_a != act_b:
            decision, reason = False, f"auto: different activity ({act_a.split('.')[-1]} vs {act_b.split('.')[-1]})"

        record = dict(p)
        record["screenshot_a"] = ss_a
        record["screenshot_b"] = ss_b
        record["activity_a"] = act_a
        record["activity_b"] = act_b

        if decision is not None:
            record["same_screen"] = decision
            record["rationale"] = reason
            record["label_source"] = "auto"
            prelabeled.append(record)
        else:
            needs_review.append(record)

    PRELABELED.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in prelabeled),
        encoding="utf-8",
    )
    NEEDS_REVIEW.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in needs_review),
        encoding="utf-8",
    )

    print(f"prelabeled (auto): {len(prelabeled)}", file=sys.stderr)
    print(f"  same: {sum(1 for r in prelabeled if r['same_screen'])}", file=sys.stderr)
    print(f"  diff: {sum(1 for r in prelabeled if not r['same_screen'])}", file=sys.stderr)
    print(f"needs human review: {len(needs_review)}", file=sys.stderr)
    print(f"  by category:", file=sys.stderr)
    cats: dict[str, int] = {}
    for r in needs_review:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    for c, n in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"    {c}: {n}", file=sys.stderr)
    print(f"\nfiles:\n  {PRELABELED}\n  {NEEDS_REVIEW}", file=sys.stderr)


if __name__ == "__main__":
    main()
