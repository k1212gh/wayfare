"""PairClassifier — 두 화면이 같은 화면인지 판정하는 학습형 쌍 분류기.

근거: "Understanding Automated Web GUI Testing: An Empirical Study Across
Exploration Strategies and State Abstractions" (Liu et al., arXiv 2606.16650,
2026) — 임계값형 추상화(구조/픽셀 유사도)보다 학습형 쌍 분류기(Judge)가
모델 기반 크롤러에서 가장 높은 커버리지를 냈고, 추상화 선택만으로 커버리지
14.8%p 차이가 났다. 이 프로젝트의 ablation 실험에서도 현재 정책
(L1_authoritative) 이 사람이 고른 어려운 8쌍에서 precision/recall 0 이었다.

설계:
  - 특징 8개: L1 구조해시 일치, pHash 유사도, GNN 코사인, activity 일치,
    라벨 유사도, 제목 유사도, 위젯 수 비율, md5 일치.
  - 순수 numpy 로지스틱 회귀 (sklearn 의존 없음). 표준화 + L2.
  - 가중치는 JSON 으로 저장/로드. 파일이 없으면 분류기는 비활성.
  - 학습: scripts/train_pair_classifier.py (experiments 라벨 + workspace 상태).
  - 사용: semantic_merge 의 Tier L (COALESCE_LEARNED=1) 및 향후 ScreenSigner 폴백.

입력 dict 키 (없으면 중립값): structural_hash, perceptual_hash(hex),
gnn_embedding(list[float]), activity, label, title_text, widget_count,
screenshot_md5.
"""

from __future__ import annotations

import json
import logging
import math
from difflib import SequenceMatcher
from pathlib import Path

logger = logging.getLogger(__name__)

FEATURE_NAMES = [
    "l1_equal", "phash_sim", "gnn_cos", "activity_equal",
    "label_sim", "title_sim", "widget_ratio", "md5_equal",
]
DEFAULT_WEIGHTS_PATH = Path(__file__).with_name("pair_classifier_weights.json")


# ── 특징 ────────────────────────────────────────────────────────

def _hamming_hex(h1: str, h2: str) -> int | None:
    try:
        a, b = int(h1, 16), int(h2, 16)
    except (TypeError, ValueError):
        return None
    return bin(a ^ b).count("1")


def _cosine(v1: list[float], v2: list[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(x * y for x, y in zip(v1, v2))
    n1 = math.sqrt(sum(x * x for x in v1))
    n2 = math.sqrt(sum(y * y for y in v2))
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


def _text_sim(a: str, b: str) -> float:
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    if not a or not b:
        return 0.5   # 정보 없음 → 중립
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def pair_features(a: dict, b: dict) -> list[float]:
    """두 화면 표현에서 8차원 특징 벡터를 만든다. 결측은 중립값(0.5)."""
    sh_a = a.get("structural_hash") or a.get("structure_str") or ""
    sh_b = b.get("structural_hash") or b.get("structure_str") or ""
    if sh_a and sh_b:
        l1 = 1.0 if sh_a == sh_b else 0.0
    else:
        l1 = 0.5

    ph_a, ph_b = a.get("perceptual_hash") or "", b.get("perceptual_hash") or ""
    d = _hamming_hex(ph_a, ph_b) if (ph_a and ph_b) else None
    phash_sim = 0.5 if d is None else max(0.0, 1.0 - d / 64.0)

    ga, gb = a.get("gnn_embedding") or [], b.get("gnn_embedding") or []
    gnn = _cosine(ga, gb) if (ga and gb) else 0.5

    act_a, act_b = a.get("activity") or "", b.get("activity") or ""
    if act_a and act_b:
        act = 1.0 if act_a == act_b else 0.0
    else:
        act = 0.5

    label = _text_sim(a.get("label", ""), b.get("label", ""))
    title = _text_sim(a.get("title_text", ""), b.get("title_text", ""))

    wa, wb = a.get("widget_count"), b.get("widget_count")
    if isinstance(wa, (int, float)) and isinstance(wb, (int, float)) and max(wa, wb) > 0:
        ratio = min(wa, wb) / max(wa, wb)
    else:
        ratio = 0.5

    m_a, m_b = a.get("screenshot_md5") or "", b.get("screenshot_md5") or ""
    md5 = 1.0 if (m_a and m_b and m_a == m_b) else 0.0

    return [l1, phash_sim, gnn, act, label, title, ratio, md5]


# ── 모델 ────────────────────────────────────────────────────────

class LogisticPairClassifier:
    """표준화 + L2 정규화 로지스틱 회귀 (numpy 만 사용)."""

    def __init__(self, weights=None, bias: float = 0.0, mean=None, std=None):
        n = len(FEATURE_NAMES)
        self.weights = list(weights) if weights is not None else [0.0] * n
        self.bias = float(bias)
        self.mean = list(mean) if mean is not None else [0.0] * n
        self.std = list(std) if std is not None else [1.0] * n
        self.trained = weights is not None

    def _z(self, x: list[float]) -> list[float]:
        return [(xi - m) / (s if s else 1.0) for xi, m, s in zip(x, self.mean, self.std)]

    def decision(self, x: list[float]) -> float:
        z = self._z(x)
        return self.bias + sum(w * zi for w, zi in zip(self.weights, z))

    def predict_proba(self, x: list[float]) -> float:
        s = self.decision(x)
        s = max(-50.0, min(50.0, s))
        return 1.0 / (1.0 + math.exp(-s))

    def predict(self, x: list[float], threshold: float = 0.5) -> bool:
        return self.predict_proba(x) >= threshold

    def fit(self, X: list[list[float]], y: list[int], epochs: int = 3000,
            lr: float = 0.1, l2: float = 1e-3, class_weight: bool = True) -> "LogisticPairClassifier":
        import numpy as np
        Xa = np.asarray(X, dtype=float)
        ya = np.asarray(y, dtype=float)
        if Xa.ndim != 2 or len(Xa) == 0:
            raise ValueError("empty training set")
        mean = Xa.mean(axis=0)
        std = Xa.std(axis=0)
        std[std == 0] = 1.0
        Z = (Xa - mean) / std
        w = np.zeros(Z.shape[1])
        b = 0.0
        # 불균형 보정 — 같은화면/다른화면 비율이 치우쳐도 경계가 밀리지 않게
        if class_weight:
            pos = max(ya.sum(), 1.0)
            neg = max(len(ya) - ya.sum(), 1.0)
            sw = np.where(ya == 1, len(ya) / (2 * pos), len(ya) / (2 * neg))
        else:
            sw = np.ones_like(ya)
        for _ in range(epochs):
            s = Z @ w + b
            p = 1.0 / (1.0 + np.exp(-np.clip(s, -50, 50)))
            g = (p - ya) * sw
            gw = Z.T @ g / len(ya) + l2 * w
            gb = g.mean()
            w -= lr * gw
            b -= lr * gb
        self.weights = w.tolist()
        self.bias = float(b)
        self.mean = mean.tolist()
        self.std = std.tolist()
        self.trained = True
        return self

    # ── 직렬화 ──

    def to_dict(self) -> dict:
        return {
            "features": FEATURE_NAMES, "weights": self.weights, "bias": self.bias,
            "mean": self.mean, "std": self.std,
        }

    def save(self, path: Path | str = DEFAULT_WEIGHTS_PATH) -> Path:
        p = Path(path)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: Path | str = DEFAULT_WEIGHTS_PATH) -> "LogisticPairClassifier":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if d.get("features") != FEATURE_NAMES:
            raise ValueError("feature list mismatch — retrain the classifier")
        return cls(d["weights"], d.get("bias", 0.0), d.get("mean"), d.get("std"))


def load_default() -> LogisticPairClassifier | None:
    """기본 가중치 파일이 있으면 로드, 없으면 None (분류기 비활성)."""
    if not DEFAULT_WEIGHTS_PATH.exists():
        return None
    try:
        return LogisticPairClassifier.load(DEFAULT_WEIGHTS_PATH)
    except Exception as e:  # noqa: BLE001
        logger.warning("[pair_classifier] load failed: %s", e)
        return None


def evaluate(clf: LogisticPairClassifier, X: list[list[float]], y: list[int],
             threshold: float = 0.5) -> dict:
    tp = fp = tn = fn = 0
    for x, yi in zip(X, y):
        pred = clf.predict(x, threshold)
        if pred and yi:
            tp += 1
        elif pred and not yi:
            fp += 1
        elif not pred and yi:
            fn += 1
        else:
            tn += 1
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {"TP": tp, "FP": fp, "TN": tn, "FN": fn,
            "precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3)}
