"""StallDetector — runtime 동적 stall 감지 → vision_tapper 호출 trigger.

원칙:
  T1 (Static, ViewTreeChain.is_primary_sufficient) 은 entry 시점에 framework
  분석 가능성만 검사. 진짜 score-based walk 의 stall 은 runtime 신호 — 4개:
    T2: 최근 N events 동안 새 unique canonical 0
    T3: 최근 5 액션 모두 outside-tap 패턴 (bottom_tab/overflow)
    T4: 같은 canonical 4+ 연속 visit

사용 (tap_walker 메인 루프):
    detector = StallDetector(window=10)
    detector.record(canonical, action_type, was_new_screen, fragment)
    is_stall, reason = detector.is_stall()
    if is_stall:
        # vision_tapper 호출 + 추정 좌표 click

비용 통제:
  - StallDetector 자체 비용 0 (in-memory deque)
  - vision_tapper 가 budget cap (env: VISION_BUDGET=10) 자체 통제
  - cache 도 자체 (state_str 별 결과 재사용)
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)


class StallDetector:
    """Sliding window 기반 4 trigger 검사 — vision fallback 호출 결정.

    Args:
        window: 최근 N events 의 새 state 발견 비율 검사 window. default 10.
        no_progress_n: 최근 K events 동안 새 state 0 이면 T2 hit. default 5.
        outside_tap_n: 최근 K 액션 모두 outside 면 T3 hit. default 5.
        same_canonical_n: 같은 canonical K번 연속이면 T4 hit. default 4.
    """

    OUTSIDE_TAP_TYPES = ("bottom_tab", "overflow", "drawer")

    def __init__(
        self,
        window: int = 10,
        no_progress_n: int = 5,
        outside_tap_n: int = 5,
        same_canonical_n: int = 4,
    ):
        self.window = window
        self.no_progress_n = no_progress_n
        self.outside_tap_n = outside_tap_n
        self.same_canonical_n = same_canonical_n
        self.event_new_history: deque[bool] = deque(maxlen=window)
        self.action_type_history: deque[str] = deque(maxlen=outside_tap_n)
        self.canonical_history: deque[str] = deque(maxlen=same_canonical_n)
        self.fragment_history: deque[str] = deque(maxlen=window)
        # 통계
        self.trigger_counts: dict[str, int] = {
            "T2_no_progress": 0,
            "T3_outside_tap": 0,
            "T4_same_canonical": 0,
        }

    def record(
        self,
        canonical: str,
        action_type: str,
        was_new_screen: bool,
        fragment: str = "",
    ) -> None:
        """매 액션 후 호출. 호출자: tap_walker."""
        self.event_new_history.append(bool(was_new_screen))
        self.action_type_history.append(action_type or "")
        self.canonical_history.append(canonical or "")
        self.fragment_history.append(fragment or "")

    def reset(self) -> None:
        """잡 시작 또는 explicit reset (예: soft_restart 후)."""
        self.event_new_history.clear()
        self.action_type_history.clear()
        self.canonical_history.clear()
        self.fragment_history.clear()

    def is_stall(self) -> tuple[bool, str]:
        """Returns (is_stall, reason). vision fallback 호출 결정용.

        4 trigger 순차 검사 — 가장 신뢰도 높은 것 우선:
          T2 (no_progress) > T4 (same_canonical) > T3 (outside_tap)
        """
        # T2: 최근 N events 동안 새 state 0
        if len(self.event_new_history) >= self.no_progress_n:
            recent = list(self.event_new_history)[-self.no_progress_n:]
            if not any(recent):
                self.trigger_counts["T2_no_progress"] += 1
                return True, f"T2: no_new_screen_{self.no_progress_n}_events"

        # T4: 같은 canonical N+ 연속 (deque maxlen=N)
        if (len(self.canonical_history) == self.same_canonical_n and
                len(set(self.canonical_history)) == 1):
            self.trigger_counts["T4_same_canonical"] += 1
            return True, f"T4: same_canonical_{self.same_canonical_n}x"

        # T3: 최근 N 액션 모두 outside-tap 류
        if (len(self.action_type_history) == self.outside_tap_n and
                all(a in self.OUTSIDE_TAP_TYPES
                    for a in self.action_type_history)):
            self.trigger_counts["T3_outside_tap"] += 1
            return True, f"T3: outside_tap_only_{self.outside_tap_n}"

        return False, ""

    def stats(self) -> dict[str, Any]:
        """잡 끝에 record — 어느 trigger 가 얼마나 hit 됐는지."""
        total_events = sum(1 for _ in self.event_new_history)
        return {
            "events_recorded": total_events,
            "trigger_counts": dict(self.trigger_counts),
            "total_triggers": sum(self.trigger_counts.values()),
        }
