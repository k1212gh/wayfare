"""3 ground truth source (auto / human_C / llm_A) 통합 후 정책별 precision/recall.

각 source 별로 별개 평가 + 3 source 동의한 subset (B) 평가.

CLI:
  python -m experiments.coalesce_ablation.evaluate

입력:
  data/pairs_prelabeled.jsonl   — auto (md5 / same path / different activity)
  data/labels_human_C.jsonl     — 사람 (나) 라벨
  data/labels_llm_A.jsonl       — Claude vision LLM 라벨 (선택)

출력:
  reports/eval_per_source.json
  stdout — 3 source 별 precision/recall 표
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from stage3_walk.screen_signer import ScreenSigner
from .policies import POLICIES
from .replay import load_fingerprints_from_tour, REPORTS
from .metrics import format_table


DATA = Path(__file__).resolve().parent / "data"
SOURCES = {
    "auto":     DATA / "pairs_prelabeled.jsonl",
    "human_C":  DATA / "labels_human_C.jsonl",
    "llm_A":    DATA / "labels_llm_A.jsonl",
}


def load_labels(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "same_screen" in d:
            out.append(d)
    return out


def load_fingerprint(tour_id: str, state_file: str, cache: dict):
    key = (tour_id, state_file)
    if key in cache:
        return cache[key]
    p = Path(f"./workspace/{tour_id}/dynamic/states/{state_file}")
    if not p.exists():
        cache[key] = None
        return None
    try:
        d = json.loads(p.read_bytes())
    except Exception:
        cache[key] = None
        return None
    hasher = ScreenSigner()
    fp = hasher.compute_fingerprint(
        d.get("views", []), d.get("activity", ""),
        d.get("screenshot_path", "") or "", eager=True,
    )
    cache[key] = fp
    return fp


def evaluate_policy_against(labels: list[dict], cache: dict) -> dict:
    """labels (ground truth) 에 대해 모든 정책 evaluate. dict per policy."""
    results = {}
    for p in POLICIES:
        tp = fp_ = tn = fn = 0
        for r in labels:
            fp_a = load_fingerprint(r["tour_id"], r["screen_a"], cache)
            fp_b = load_fingerprint(r["tour_id"], r["screen_b"], cache)
            if fp_a is None or fp_b is None:
                continue
            predicted = p.matcher(fp_a, fp_b)
            truth = bool(r["same_screen"])
            if predicted and truth: tp += 1
            elif predicted and not truth: fp_ += 1
            elif not predicted and not truth: tn += 1
            else: fn += 1
        prec = tp / (tp + fp_) if (tp + fp_) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        results[p.name] = {
            "TP": tp, "FP": fp_, "TN": tn, "FN": fn,
            "precision": round(prec, 3),
            "recall": round(rec, 3),
            "f1": round(f1, 3),
        }
    return results


def intersect_agreement(label_sources: dict[str, list[dict]]) -> list[dict]:
    """두 source 가 모두 라벨링한 쌍 중 둘 다 같은 결정인 것만 추출.

    auto + human_C 만 있으면 둘 다 라벨 한 쌍 + 같은 결정.
    """
    # key = (tour_id, screen_a, screen_b) 정규화
    def k(r):
        return (r["tour_id"], *sorted([r["screen_a"], r["screen_b"]]))

    label_maps = {src: {k(r): r for r in labels}
                  for src, labels in label_sources.items()}
    common_keys = set.intersection(*(set(m.keys()) for m in label_maps.values() if m))
    agreed = []
    for ck in common_keys:
        decisions = [m[ck]["same_screen"] for m in label_maps.values()]
        if len(set(decisions)) == 1:
            # 모두 동의 — 첫 source 의 record 사용
            base = next(iter(label_maps.values()))[ck]
            agreed.append(base)
    return agreed


def main():
    cache: dict = {}
    all_labels = {src: load_labels(path) for src, path in SOURCES.items()}
    for src, ls in all_labels.items():
        print(f"  {src}: {len(ls)} labels", file=sys.stderr)

    # 각 source 별 정책 평가
    per_source: dict[str, dict] = {}
    for src, labels in all_labels.items():
        if not labels:
            continue
        per_source[src] = evaluate_policy_against(labels, cache)

    # 모든 source 가 동의한 쌍 (가장 신뢰도 높은 ground truth)
    nonempty = {k: v for k, v in all_labels.items() if v}
    if len(nonempty) >= 2:
        agreed = intersect_agreement(nonempty)
        if agreed:
            per_source["agreement_B"] = evaluate_policy_against(agreed, cache)
            print(f"  agreement_B (다중 source 동의): {len(agreed)} labels",
                  file=sys.stderr)

    # 표 출력
    for src, results in per_source.items():
        print(f"\n=== Ground truth: {src} ===")
        rows = []
        for p in POLICIES:
            r = results[p.name]
            r2 = {"policy": p.name, **r}
            rows.append(r2)
        print(format_table(
            rows,
            ["policy", "TP", "FP", "TN", "FN", "precision", "recall", "f1"],
        ))

    out = REPORTS / "eval_per_source.json"
    out.write_text(json.dumps(per_source, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
