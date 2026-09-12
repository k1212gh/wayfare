"""PairClassifier 단위 테스트 + semantic_merge Tier L 통합."""

from __future__ import annotations

import random

import pytest

from stage6_screenmap import pair_classifier as pc
from stage6_screenmap.pair_classifier import (
    FEATURE_NAMES, LogisticPairClassifier, evaluate, pair_features,
)


def test_pair_features_identical_and_neutral():
    a = {"structural_hash": "h1", "perceptual_hash": "ff00ff00ff00ff00", "gnn_embedding": [1.0, 0.0],
         "activity": "A", "label": "홈", "title_text": "홈", "widget_count": 10, "screenshot_md5": "m"}
    f = pair_features(a, dict(a))
    assert len(f) == len(FEATURE_NAMES)
    assert f == [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    # 정보 없는 쌍 → 중립 0.5 (md5 만 0)
    n = pair_features({}, {})
    assert n == [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.0]


def test_pair_features_differences():
    a = {"structural_hash": "h1", "perceptual_hash": "0000000000000000", "activity": "A", "widget_count": 5}
    b = {"structural_hash": "h2", "perceptual_hash": "ffffffffffffffff", "activity": "B", "widget_count": 10}
    f = pair_features(a, b)
    assert f[0] == 0.0          # l1 다름
    assert f[1] == 0.0          # 해밍 64 → 유사도 0
    assert f[3] == 0.0          # activity 다름
    assert f[6] == 0.5          # 5/10


def _synthetic(n=300, seed=7, missing_rate=0.4):
    """같은/다른 화면 쌍을 흉내 낸 특징 벡터. 실제 데이터처럼 pHash/GNN/제목/위젯수는
    결측(0.5 중립)일 수 있어 그 경우 l1/label/activity 로 판단하도록 마스킹한다."""
    rng = random.Random(seed)
    X, y = [], []
    for _ in range(n):
        same = rng.random() < 0.5
        if same:
            x = [1.0 if rng.random() < 0.8 else 0.0, rng.uniform(0.85, 1.0), rng.uniform(0.9, 1.0),
                 1.0, rng.uniform(0.7, 1.0), rng.uniform(0.6, 1.0), rng.uniform(0.8, 1.0), 0.0]
        else:
            x = [0.0, rng.uniform(0.3, 0.8), rng.uniform(0.5, 0.95),
                 1.0 if rng.random() < 0.5 else 0.0, rng.uniform(0.0, 0.5), rng.uniform(0.0, 0.5),
                 rng.uniform(0.2, 0.9), 0.0]
        for idx in (1, 2, 5, 6):   # phash_sim, gnn_cos, title_sim, widget_ratio 결측 가능
            if rng.random() < missing_rate:
                x[idx] = 0.5
        X.append(x)
        y.append(1 if same else 0)
    return X, y


def test_fit_learns_separable_rule_and_roundtrips(tmp_path):
    X, y = _synthetic()
    clf = LogisticPairClassifier().fit(X, y, epochs=1500, lr=0.2)
    m = evaluate(clf, X, y)
    assert m["precision"] >= 0.95 and m["recall"] >= 0.95, m
    # 학습된 가중치의 방향: l1_equal / label_sim 양수
    w = dict(zip(FEATURE_NAMES, clf.weights))
    assert w["l1_equal"] > 0 and w["label_sim"] > 0
    path = clf.save(tmp_path / "w.json")
    loaded = LogisticPairClassifier.load(path)
    assert abs(loaded.predict_proba(X[0]) - clf.predict_proba(X[0])) < 1e-9


def test_load_default_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(pc, "DEFAULT_WEIGHTS_PATH", tmp_path / "nope.json")
    assert pc.load_default() is None


def test_fit_rejects_empty():
    with pytest.raises(ValueError):
        LogisticPairClassifier().fit([], [])


# ─── semantic_merge Tier L 통합 ─────────────────────────────────

def _node(sid, label, sh, act="A"):
    return {"screen_id": sid, "label": label, "structure_str": sh, "activity": act,
            "screenshot_ref": "", "widgets": []}


def test_tier_l_inactive_without_env(monkeypatch):
    from stage6_screenmap import semantic_merge as sm
    monkeypatch.delenv("COALESCE_LEARNED", raising=False)
    sm._reset_learned_cache()
    assert sm._learned_classifier() is None
    # 기존 동작: 라벨 동일 → Tier 1 merge
    assert sm._is_mergeable(_node("a", "홈", "h1"), _node("b", "홈", "h2"), [], 0.85) is True


def test_tier_l_confident_verdict_overrides(monkeypatch, tmp_path):
    from stage6_screenmap import semantic_merge as sm
    X, y = _synthetic()
    clf = LogisticPairClassifier().fit(X, y, epochs=1500, lr=0.2)
    wpath = tmp_path / "w.json"
    clf.save(wpath)
    monkeypatch.setattr(pc, "DEFAULT_WEIGHTS_PATH", wpath)
    monkeypatch.setenv("COALESCE_LEARNED", "1")
    sm._reset_learned_cache()
    assert sm._learned_classifier() is not None
    # 구조 같고 라벨 같음 → 같은 화면 (기존 tier 없이도 True)
    same_a, same_b = _node("a", "메뉴 목록", "h1"), _node("b", "메뉴 목록", "h1")
    assert sm._is_mergeable(same_a, same_b, [], 0.85) is True
    # 구조 다르고 라벨 전혀 다름 → 분류기가 강한 '다름' → False
    diff_a, diff_b = _node("a", "주문 완료", "h1"), _node("b", "매장 찾기 지도", "h2")
    assert sm._is_mergeable(diff_a, diff_b, [], 0.85) is False
    sm._reset_learned_cache()
