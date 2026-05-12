"""Coalesce 매칭 정책 정의.

5가지 정책을 같은 fingerprint 입력에 대해 다른 매칭 규칙으로 적용해
canonical 결정 결과를 비교한다. 모든 정책은 production ScreenSigner 의
fingerprint 계산은 그대로 쓰고, matching rule 만 swap.

정책 인자는 같은 두 fingerprint 와 임계값들. 반환: bool (같은 화면인가).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from stage3_walk.screen_signer import ScreenSignature


# ─── 단순 정책 ────────────────────────────────────────────────


def l1_only(fp_a: ScreenSignature, fp_b: ScreenSignature,
            phash_threshold: int = 10, gnn_threshold: float = 0.82) -> bool:
    """L1 (구조 해시) 만 비교. L2/L3 무시. 양쪽 다 없으면 다른 화면."""
    if not (fp_a.structural_hash and fp_b.structural_hash):
        return False
    return fp_a.structural_hash == fp_b.structural_hash


def l2_only(fp_a: ScreenSignature, fp_b: ScreenSignature,
            phash_threshold: int = 10, gnn_threshold: float = 0.82) -> bool:
    """L2 (perceptual hash) 만 비교. 양쪽 다 있어야 의미. 없으면 매칭 안 됨."""
    if not (fp_a.perceptual_hash and fp_b.perceptual_hash):
        return False
    from stage3_walk.screen_signer import ScreenSigner
    h = ScreenSigner()
    dist = h._hamming_distance(fp_a.perceptual_hash, fp_b.perceptual_hash)
    return dist <= phash_threshold


def l3_only(fp_a: ScreenSignature, fp_b: ScreenSignature,
            phash_threshold: int = 10, gnn_threshold: float = 0.82) -> bool:
    """L3 (graph embedding cosine) 만 비교."""
    if not (fp_a.gnn_embedding and fp_b.gnn_embedding):
        return False
    from stage3_walk.screen_signer import ScreenSigner
    h = ScreenSigner()
    sim = h._cosine_similarity(fp_a.gnn_embedding, fp_b.gnn_embedding)
    return sim >= gnn_threshold


# ─── 결합 정책 ────────────────────────────────────────────────


def l1_or_l2(fp_a: ScreenSignature, fp_b: ScreenSignature,
             phash_threshold: int = 10, gnn_threshold: float = 0.82) -> bool:
    """L1 일치 OR L2 일치. 즉 한쪽이라도 매칭하면 같은 화면."""
    return l1_only(fp_a, fp_b) or l2_only(fp_a, fp_b, phash_threshold)


def union_all(fp_a: ScreenSignature, fp_b: ScreenSignature,
              phash_threshold: int = 10, gnn_threshold: float = 0.82) -> bool:
    """L1 OR L2 OR L3. 멘토 의문 시나리오 — 정말 union 이 더 좋은가?"""
    return (
        l1_only(fp_a, fp_b)
        or l2_only(fp_a, fp_b, phash_threshold)
        or l3_only(fp_a, fp_b, phash_threshold, gnn_threshold)
    )


def l1_authoritative(fp_a: ScreenSignature, fp_b: ScreenSignature,
                     phash_threshold: int = 10, gnn_threshold: float = 0.82) -> bool:
    """현재 production 정책. L1 양쪽 있으면 L1 만, 한쪽 없으면 L2/L3 폴백."""
    have_l1 = bool(fp_a.structural_hash and fp_b.structural_hash)
    if have_l1:
        return fp_a.structural_hash == fp_b.structural_hash
    if l2_only(fp_a, fp_b, phash_threshold):
        return True
    if l3_only(fp_a, fp_b, phash_threshold, gnn_threshold):
        return True
    return False


# ─── 정책 레지스트리 ──────────────────────────────────────────


@dataclass
class Policy:
    """이름 + matcher + 설명. CLI 출력 / 보고서에 사용."""
    name: str
    matcher: Callable[..., bool]
    description: str


POLICIES: list[Policy] = [
    Policy("L1_only",          l1_only,          "구조 해시 단독. L2/L3 무시."),
    Policy("L2_only",          l2_only,          "perceptual hash 단독."),
    Policy("L3_only",          l3_only,          "그래프 임베딩 cosine 단독."),
    Policy("L1+L2",            l1_or_l2,         "L1 OR L2 union."),
    Policy("L1+L2+L3",         union_all,        "L1 OR L2 OR L3 full union."),
    Policy("L1_authoritative", l1_authoritative, "현재 production 정책 — L1 결정권자."),
]


def lookup(name: str) -> Policy:
    """이름으로 정책 검색. 없으면 KeyError."""
    for p in POLICIES:
        if p.name == name:
            return p
    raise KeyError(f"unknown policy: {name}")
