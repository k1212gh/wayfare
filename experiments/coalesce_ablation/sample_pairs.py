"""Ground truth 라벨링용 쌍 추출.

전수 라벨링 불가능 (124,000쌍, 약 346시간) → stratified sampling.

전략:
  suspicious 50: 정책간 disagree 케이스 (의심 영역)
    - 같은 canonical 끼리 (production 이 SAME 봄) — 진짜인가?
    - 다른 canonical 인데 pHash 가까움 (production 이 놓쳤나?)
  random 30: bias-free baseline
  sanity 20: 명백 케이스 (다른 activity / 같은 hash 등)

CLI:
  python -m experiments.coalesce_ablation.sample_pairs

출력:
  data/pairs_to_label.jsonl — 라벨링용 (label 비어있음)
  각 줄: {"tour_id":"...","screen_a":"...","screen_b":"...","category":"...","reason":"..."}
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from itertools import combinations

from stage3_walk.screen_signer import ScreenSigner
from .replay import load_fingerprints_from_tour, WORKSPACE


DATA = Path(__file__).resolve().parent / "data"
DATA.mkdir(exist_ok=True)
OUT = DATA / "pairs_to_label.jsonl"


def sample_pairs(clock_tour: str = "3a265c27", mega_tour: str = "432b611e",
                 n_total: int = 100, seed: int = 42) -> list[dict]:
    """두 잡에서 100쌍 추출. seed 고정으로 재현 가능."""
    random.seed(seed)
    hasher = ScreenSigner()
    pairs = []

    for tour_id in (clock_tour, mega_tour):
        states_dir = WORKSPACE / tour_id / "dynamic" / "states"
        if not states_dir.exists():
            print(f"skip {tour_id}: no states dir", file=sys.stderr)
            continue
        state_files = sorted(states_dir.glob("state_*.json"))
        if len(state_files) < 10:
            print(f"skip {tour_id}: too few states ({len(state_files)})", file=sys.stderr)
            continue

        # 각 state 의 fingerprint + activity + canonical 로딩
        infos = []
        for sf in state_files:
            try:
                d = json.loads(sf.read_bytes())
                fp = hasher.compute_fingerprint(
                    d.get("views", []), d.get("activity", ""),
                    d.get("screenshot_path", "") or "", eager=True,
                )
                infos.append({
                    "file": sf.name,
                    "activity": d.get("activity", ""),
                    "structure_hash": fp.structural_hash,
                    "perceptual_hash": fp.perceptual_hash,
                    "canonical": d.get("canonical_id", "") or fp.structural_hash,
                })
            except Exception as e:
                continue
        if len(infos) < 10:
            continue

        # 잡당 50쌍씩
        n_per_tour = n_total // 2

        # suspicious 25 — 같은 canonical 끼리 + 다른 canonical pHash 가까운
        suspicious: list[dict] = []
        # 같은 canonical 끼리 12쌍
        by_canonical: dict[str, list[dict]] = {}
        for info in infos:
            by_canonical.setdefault(info["canonical"], []).append(info)
        same_canon_pairs = []
        for k, group in by_canonical.items():
            if len(group) >= 2:
                same_canon_pairs.extend(list(combinations(group, 2)))
        random.shuffle(same_canon_pairs)
        for a, b in same_canon_pairs[:12]:
            suspicious.append({
                "tour_id": tour_id,
                "screen_a": a["file"], "screen_b": b["file"],
                "category": "same_canonical",
                "reason": "production hasher 가 SAME 으로 판정 — 진짜 같은 화면인지 검증",
            })

        # 다른 canonical 인데 pHash 가까운 13쌍
        ph = {i["file"]: i["perceptual_hash"] for i in infos if i["perceptual_hash"]}
        close_pairs = []
        infos_with_ph = [i for i in infos if i["perceptual_hash"]]
        for a, b in combinations(infos_with_ph, 2):
            if a["canonical"] == b["canonical"]:
                continue
            dist = hasher._hamming_distance(a["perceptual_hash"], b["perceptual_hash"])
            if 0 < dist <= 12:
                close_pairs.append((a, b, dist))
        close_pairs.sort(key=lambda t: t[2])
        for a, b, dist in close_pairs[:13]:
            suspicious.append({
                "tour_id": tour_id,
                "screen_a": a["file"], "screen_b": b["file"],
                "category": "different_canonical_visually_close",
                "reason": f"production hasher 가 DIFFERENT 로 판정 (pHash 거리 {dist}) — 놓쳤나?",
            })

        # random 15
        all_pairs = list(combinations(infos, 2))
        random.shuffle(all_pairs)
        random_pairs = []
        # suspicious 와 중복 제거
        seen = {(p["screen_a"], p["screen_b"]) for p in suspicious}
        for a, b in all_pairs:
            if (a["file"], b["file"]) in seen or (b["file"], a["file"]) in seen:
                continue
            random_pairs.append({
                "tour_id": tour_id,
                "screen_a": a["file"], "screen_b": b["file"],
                "category": "random",
                "reason": "bias-free baseline",
            })
            if len(random_pairs) >= 15:
                break

        # sanity 10 — 다른 activity 끼리 (명백 DIFFERENT)
        sanity = []
        diff_act_pairs = []
        for a, b in all_pairs:
            if a["activity"] != b["activity"] and a["activity"] and b["activity"]:
                diff_act_pairs.append((a, b))
        random.shuffle(diff_act_pairs)
        for a, b in diff_act_pairs[:10]:
            if (a["file"], b["file"]) in seen:
                continue
            sanity.append({
                "tour_id": tour_id,
                "screen_a": a["file"], "screen_b": b["file"],
                "category": "sanity_different_activity",
                "reason": "다른 activity — 거의 확실 DIFFERENT (negative control)",
            })

        tour_pairs = (suspicious + random_pairs + sanity)[:n_per_tour]
        # tour_id 별로 적절히 trim
        pairs.extend(tour_pairs)
        print(f"[{tour_id}] {len(tour_pairs)} pairs: "
              f"suspicious={len(suspicious)} random={len(random_pairs)} sanity={len(sanity)}",
              file=sys.stderr)

    return pairs[:n_total]


def main():
    pairs = sample_pairs()
    with OUT.open("w", encoding="utf-8") as f:
        f.write("# Auto-generated by sample_pairs.py. Edit to add labels:\n")
        f.write("# {..., \"same_screen\": true|false, \"rationale\": \"...\"}\n")
        f.write("# Category-by-category:\n")
        for cat in ("same_canonical", "different_canonical_visually_close",
                    "random", "sanity_different_activity"):
            n = sum(1 for p in pairs if p["category"] == cat)
            f.write(f"#   {cat}: {n}\n")
        f.write("\n")
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"\nwrote {len(pairs)} pairs to {OUT}")


if __name__ == "__main__":
    main()
