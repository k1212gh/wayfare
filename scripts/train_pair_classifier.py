"""학습형 화면 쌍 분류기 학습/평가 — experiments 라벨 + workspace 상태 파일 사용.

    PYTHONPATH=. python scripts/train_pair_classifier.py            # 학습 + LOO 평가 + 저장
    PYTHONPATH=. python scripts/train_pair_classifier.py --dry-run  # 저장 안 함
    PYTHONPATH=. python scripts/train_pair_classifier.py --labels a.jsonl b.jsonl

입력 라벨 형식 (experiments/coalesce_ablation/data/*.jsonl):
    {"tour_id": ..., "screen_a": "state_0001.json", "screen_b": "state_0002.json", "same_screen": true, ...}
상태 파일은 workspace/<tour_id>/dynamic/states/ 에 있어야 한다 (없는 쌍은 건너뜀).

출력: stage6_screenmap/pair_classifier_weights.json (COALESCE_LEARNED=1 일 때 Tier L 이 사용)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stage3_walk.screen_signer import ScreenSigner  # noqa: E402
from stage6_screenmap.pair_classifier import (  # noqa: E402
    DEFAULT_WEIGHTS_PATH, FEATURE_NAMES, LogisticPairClassifier, evaluate, pair_features,
)

DATA = ROOT / "experiments" / "coalesce_ablation" / "data"
DEFAULT_LABELS = [DATA / "labels_human_C.jsonl", DATA / "pairs_prelabeled.jsonl"]


def load_labels(paths: list[Path]) -> list[dict]:
    out, seen = [], set()
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "same_screen" not in d:
                continue
            key = (d.get("tour_id"), d.get("screen_a"), d.get("screen_b"))
            if key in seen:
                continue
            seen.add(key)
            out.append(d)
    return out


def state_repr(tour_id: str, state_file: str, hasher: ScreenSigner, cache: dict) -> dict | None:
    key = (tour_id, state_file)
    if key in cache:
        return cache[key]
    p = ROOT / "workspace" / tour_id / "dynamic" / "states" / state_file
    if not p.exists():
        cache[key] = None
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    fp = hasher.compute_fingerprint(d.get("views", []), d.get("activity", ""),
                                    d.get("screenshot_path", "") or "", eager=True)
    top_texts = [v.get("text", "") for v in d.get("views", []) if v.get("text")][:3]
    rep = {
        "structural_hash": fp.structural_hash, "perceptual_hash": fp.perceptual_hash,
        "gnn_embedding": fp.gnn_embedding, "activity": d.get("activity", ""),
        "title_text": " ".join(top_texts), "widget_count": fp.widget_count,
        "screenshot_md5": fp.screenshot_md5,
    }
    cache[key] = rep
    return rep


def l1_authoritative(a: dict, b: dict, hasher: ScreenSigner) -> bool:
    from stage3_walk.screen_signer import ScreenSignature
    fa = ScreenSignature(structural_hash=a["structural_hash"], perceptual_hash=a["perceptual_hash"],
                         gnn_embedding=a["gnn_embedding"])
    fb = ScreenSignature(structural_hash=b["structural_hash"], perceptual_hash=b["perceptual_hash"],
                         gnn_embedding=b["gnn_embedding"])
    return hasher._is_match(fa, fb)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", nargs="*", type=Path, default=DEFAULT_LABELS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--epochs", type=int, default=3000)
    args = ap.parse_args()

    labels = load_labels(args.labels)
    hasher = ScreenSigner()
    cache: dict = {}
    X, y, base_pred, skipped = [], [], [], 0
    for lab in labels:
        a = state_repr(lab["tour_id"], lab["screen_a"], hasher, cache)
        b = state_repr(lab["tour_id"], lab["screen_b"], hasher, cache)
        if a is None or b is None:
            skipped += 1
            continue
        X.append(pair_features(a, b))
        y.append(1 if lab["same_screen"] else 0)
        base_pred.append(l1_authoritative(a, b, hasher))

    print(f"labels={len(labels)} usable={len(X)} skipped(no state file)={skipped} "
          f"pos={sum(y)} neg={len(y) - sum(y)}")
    if len(X) < 10 or len(set(y)) < 2:
        print("학습 데이터 부족 — workspace 에 라벨된 tour 의 states/ 가 있어야 합니다. "
              "메가커피 A/B 실행 후 experiments/coalesce_ablation/sample_pairs.py 로 쌍을 만들고 라벨하세요.")
        return

    # baseline (현재 production 정책) 성능
    tp = sum(1 for p, t in zip(base_pred, y) if p and t)
    fp = sum(1 for p, t in zip(base_pred, y) if p and not t)
    fn = sum(1 for p, t in zip(base_pred, y) if not p and t)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    print(f"baseline L1_authoritative: precision={prec:.3f} recall={rec:.3f}")

    # leave-one-out 평가 (표본이 작으므로)
    loo_pred = []
    for i in range(len(X)):
        Xi = X[:i] + X[i + 1:]
        yi = y[:i] + y[i + 1:]
        if len(set(yi)) < 2:
            loo_pred.append(False)
            continue
        c = LogisticPairClassifier().fit(Xi, yi, epochs=args.epochs)
        loo_pred.append(c.predict(X[i]))
    tp = sum(1 for p, t in zip(loo_pred, y) if p and t)
    fp = sum(1 for p, t in zip(loo_pred, y) if p and not t)
    fn = sum(1 for p, t in zip(loo_pred, y) if not p and t)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    print(f"learned (leave-one-out): precision={prec:.3f} recall={rec:.3f}")

    clf = LogisticPairClassifier().fit(X, y, epochs=args.epochs)
    print("train-set:", evaluate(clf, X, y))
    print("weights:", {n: round(w, 3) for n, w in zip(FEATURE_NAMES, clf.weights)}, "bias", round(clf.bias, 3))
    if args.dry_run:
        print("(dry-run) 저장 안 함")
        return
    out = clf.save(DEFAULT_WEIGHTS_PATH)
    print("saved:", out)


if __name__ == "__main__":
    main()
