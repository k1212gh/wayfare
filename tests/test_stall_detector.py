"""StallDetector 단위 테스트 — T2/T3/T4 trigger 검사."""

from __future__ import annotations

from stage3_walk.stall_detector import StallDetector


# ─── T2 — No progress ───────────────────────────


def test_t2_triggers_after_5_no_new_screens():
    d = StallDetector(no_progress_n=5)
    for _ in range(5):
        d.record("c1", "click", was_new_screen=False)
    is_stall, reason = d.is_stall()
    assert is_stall
    assert "T2" in reason


def test_t2_does_not_trigger_with_progress():
    d = StallDetector(no_progress_n=5)
    for i in range(5):
        d.record(f"c{i}", "click", was_new_screen=True)
    assert d.is_stall() == (False, "")


def test_t2_recovers_after_new_screen():
    """5 stall → 1 progress → no T2 (window 안 새 state 1개 있음)."""
    d = StallDetector(no_progress_n=5)
    for _ in range(4):
        d.record("c1", "click", was_new_screen=False)
    d.record("c2", "click", was_new_screen=True)
    assert d.is_stall() == (False, "")


# ─── T3 — Outside-tap pattern ────────────────


def test_t3_triggers_5_bottom_tabs():
    """T3 검증 — canonical 다양화 (T4 차단). progress True (T2 차단)."""
    d = StallDetector(outside_tap_n=5)
    for i in range(5):
        d.record(f"c{i}", "bottom_tab", was_new_screen=True)
    is_stall, reason = d.is_stall()
    assert is_stall
    assert "T3" in reason


def test_t3_triggers_mixed_outside():
    """bottom_tab + overflow 만 5번 — T3 hit. canonical 다양화."""
    d = StallDetector(outside_tap_n=5)
    for i, at in enumerate(["bottom_tab", "overflow", "bottom_tab", "overflow", "bottom_tab"]):
        d.record(f"c{i}", at, was_new_screen=True)
    is_stall, reason = d.is_stall()
    assert is_stall
    assert "T3" in reason


def test_t3_does_not_trigger_with_inside_action():
    """5 액션 중 1개라도 click(button/text) 면 T3 안 hit. canonical 다양화."""
    d = StallDetector(outside_tap_n=5)
    actions = ["bottom_tab", "bottom_tab", "click", "bottom_tab", "bottom_tab"]
    for i, at in enumerate(actions):
        d.record(f"c{i}", at, was_new_screen=True)
    is_stall, reason = d.is_stall()
    assert not is_stall


# ─── T4 — Same canonical loop ──────────────


def test_t4_triggers_4_same_canonical():
    d = StallDetector(same_canonical_n=4)
    for _ in range(4):
        d.record("c1", "click", was_new_screen=True)
    is_stall, reason = d.is_stall()
    assert is_stall
    assert "T4" in reason


def test_t4_does_not_trigger_with_different_canonical():
    d = StallDetector(same_canonical_n=4)
    for c in ["c1", "c2", "c1", "c2"]:
        d.record(c, "click", was_new_screen=True)
    is_stall, reason = d.is_stall()
    assert not is_stall


# ─── Priority ordering ─────────────────────


def test_t2_takes_priority_over_t3():
    """T2 (no progress) 가 T3 (outside tap) 보다 신뢰도 높음."""
    d = StallDetector(no_progress_n=5, outside_tap_n=5)
    # 5 bottom_tab + 모두 was_new_screen=False
    # → T2 와 T3 둘 다 hit 조건 — T2 우선
    for _ in range(5):
        d.record("c1", "bottom_tab", was_new_screen=False)
    is_stall, reason = d.is_stall()
    assert is_stall
    assert "T2" in reason  # T3 가 아닌 T2 returned


# ─── Reset ──────────────────────────────────


def test_reset_clears_history():
    d = StallDetector(no_progress_n=5)
    for _ in range(5):
        d.record("c1", "click", was_new_screen=False)
    assert d.is_stall()[0]
    d.reset()
    assert d.is_stall() == (False, "")


# ─── Stats ──────────────────────────────────


def test_stats_count_triggers():
    d = StallDetector(no_progress_n=5)
    # T2 hit 만들기
    for _ in range(5):
        d.record("c1", "click", was_new_screen=False)
    d.is_stall()
    d.is_stall()  # 두 번 호출 → 두 번 카운트

    s = d.stats()
    assert s["trigger_counts"]["T2_no_progress"] == 2
    assert s["total_triggers"] == 2


# ─── Empty / edge case ─────────────────────


def test_no_history_no_stall():
    d = StallDetector()
    assert d.is_stall() == (False, "")


def test_partial_history_no_premature_trigger():
    """history 가 N 미만 — trigger 안 함."""
    d = StallDetector(no_progress_n=5, outside_tap_n=5, same_canonical_n=4)
    d.record("c1", "click", was_new_screen=False)
    d.record("c1", "click", was_new_screen=False)
    d.record("c1", "click", was_new_screen=False)
    assert d.is_stall() == (False, "")
