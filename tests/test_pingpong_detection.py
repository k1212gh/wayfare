"""P0-13: ping-pong loop 인식 — 두 canonical 사이 alternating 패턴 감지.

메가커피 "메가퀵결제 관리 ↔ 등록" 케이스 (own_domain 안 결제 폼) 는
external_guard W2 안 발동 + stall escape 의 "연속 같은 canonical" 조건도
안 맞아 walk 시간 50% 까지 낭비. ping-pong 패턴을 별도 인식해 진입 액션 학습.

여기서는 detection 알고리즘만 단위 테스트. 실제 학습 트리거는 통합 환경
의존이라 e2e 잡으로 검증.
"""

from __future__ import annotations


def _ping_pong_detected(recent: list[str]) -> bool:
    """tap_walker 의 ping-pong 검출 로직 추출 (테스트용 미러).

    P0-13.1 (2026-05-06): 정확한 alternating 검사 — even index 모두 같고
    odd index 모두 같음. 이전 a_count in (3,4) 는 AAAABBB block 패턴도
    잘못 trigger 했음.
    """
    if len(recent) < 7:
        return False
    last_7 = recent[-7:]
    unique = set(last_7)
    if len(unique) != 2:
        return False
    if last_7[0] == last_7[1]:
        return False
    if not all(last_7[i] == last_7[0] for i in range(0, 7, 2)):
        return False
    if not all(last_7[i] == last_7[1] for i in range(1, 7, 2)):
        return False
    return True


# ─── Positive cases (ping-pong 정확히 감지해야 함) ─────────────


def test_perfect_alternating_aba_aba_a():
    """A,B,A,B,A,B,A — 가장 명확한 ping-pong."""
    assert _ping_pong_detected(["A", "B", "A", "B", "A", "B", "A"])


def test_perfect_alternating_bab_aba_b():
    """B,A,B,A,B,A,B — 반대 시작."""
    assert _ping_pong_detected(["B", "A", "B", "A", "B", "A", "B"])


def test_alternating_with_window_history():
    """이전 noise 가 있어도 마지막 7개가 alternating 이면 hit."""
    seq = ["X", "Y", "Z"] + ["A", "B", "A", "B", "A", "B", "A"]
    assert _ping_pong_detected(seq)


# ─── Negative cases (false positive 안 일어나야 함) ──────────


def test_too_short_window_no_detect():
    """5 step alternating 은 아직 의심만 — 7 채워야 hit."""
    assert not _ping_pong_detected(["A", "B", "A", "B", "A"])


def test_three_unique_canonicals_no_detect():
    """3 종류 canonical 면 정상 walk — alternating 아님."""
    assert not _ping_pong_detected(["A", "B", "C", "A", "B", "C", "A"])


def test_single_canonical_no_detect():
    """같은 canonical 만 7번 — stall 케이스 (P0-12 가 처리)."""
    assert not _ping_pong_detected(["A"] * 7)


def test_hub_outbound_no_detect():
    """홈 hub 에서 다양한 sub 화면 도달 — alternating 아닌 분기."""
    seq = ["HOME", "MENU", "HOME", "STORE", "HOME", "EVENT", "HOME"]
    assert not _ping_pong_detected(seq)


def test_almost_alternating_but_one_off():
    """6 step alternating 후 새 canonical 1번 — ping-pong 깨짐."""
    seq = ["A", "B", "A", "B", "A", "B", "C"]
    assert not _ping_pong_detected(seq)


def test_ratio_2_5_no_detect():
    """A,A,B,A,A,B,A — A가 5번, alternating 패턴 X."""
    assert not _ping_pong_detected(["A", "A", "B", "A", "A", "B", "A"])


# ─── P0-13.1 false positive 회귀 ──────────────────────────


def test_block_pattern_aaaabbb_no_detect():
    """A,A,A,A,B,B,B — block→block. 이전 P0-13 는 trigger 했지만
    P0-13.1 은 alternating 만 잡으므로 false positive 차단.

    563d2e9b 잡에서 4회 모두 이 패턴으로 false positive (학습 0).
    """
    assert not _ping_pong_detected(["A", "A", "A", "A", "B", "B", "B"])


def test_block_pattern_bbbaaaa_no_detect():
    """B,B,B,A,A,A,A — 반대 block. false positive 차단."""
    assert not _ping_pong_detected(["B", "B", "B", "A", "A", "A", "A"])


def test_partial_alternating_aabab_no_detect():
    """A,A,B,A,B,A,B — 시작은 같은 글자 두 번. alternating 아님."""
    assert not _ping_pong_detected(["A", "A", "B", "A", "B", "A", "B"])


def test_double_at_end_no_detect():
    """A,B,A,B,A,B,B — 마지막 두 개 같음. 진짜 alternating 아님."""
    assert not _ping_pong_detected(["A", "B", "A", "B", "A", "B", "B"])


# ─── Edge cases ────────────────────────────────────────────


def test_empty_no_crash():
    assert not _ping_pong_detected([])


def test_just_one_no_crash():
    assert not _ping_pong_detected(["A"])
