"""ViewTreeChain — Framework 1차 분기 + Universal Vision Fallback.

원칙 (screenatlas_completeness_principle 메모리):
  한 앱 안에 framework 가 mixed (Mattermost 메인 RN + OAuth WebView). framework
  label 만으로는 부족 — per-screen 분석 가능성으로 분기.

흐름:
    chain = ViewTreeChain(framework, primary_reader, vision_tapper)

    # 매 화면 dump 후
    if chain.is_primary_sufficient(views):
        actions = primary.score / 추출    # 기존 흐름 (무료)
    else:
        actions = chain.fallback_to_vision(screenshot, state_str)
        # → vision-LLM 좌표 추정 → 호출자가 (x,y) click

비용 효율:
  - 분석 가능 화면 = vision 호출 0
  - 분석 불가 화면만 vision 호출 (잡당 $0.01-0.05)
  - 한 잡 안에서 framework mixed 자동 분기
"""

from __future__ import annotations

import logging
from typing import Any

from .quality_checks import assess_quality

logger = logging.getLogger(__name__)


class ViewTreeChain:
    """Per-screen quality 결정 + vision fallback orchestration.

    primary_reader 는 screenatlas 의 기존 XMLViewTreeReader / framework 별 변종.
    vision_tapper 는 VisionTapper (vision_tapper.py). 둘 다 None 가능 —
    fallback 안 쓰면 quality check 만 활용.
    """

    def __init__(
        self,
        framework: str,
        primary_reader: Any = None,
        vision_tapper: Any = None,
    ):
        self.framework = (framework or "xml").lower()
        self.primary = primary_reader
        self.vision = vision_tapper
        # 통계 — 잡당 vision 호출 횟수 추적
        self.primary_calls = 0
        self.fallback_calls = 0

    def is_primary_sufficient(self, views: list[dict]) -> bool:
        """Framework-specific quality check. 결과:
            True  = primary extractor (XML/Compose/RN/Flutter native) 충분
            False = vision fallback 호출 권장
        """
        ok = assess_quality(self.framework, views)
        if ok:
            self.primary_calls += 1
        return ok

    def fallback_to_vision(
        self,
        screenshot_path: str,
        state_str: str = "",
    ) -> list[dict]:
        """Vision LLM 으로 actionable 좌표 추정.

        vision_tapper 가 None 이면 빈 리스트 (graceful degradation).
        호출자: 결과의 click_point() 로 uiautomator click.
        """
        if not self.vision:
            logger.debug("[chain] no vision_tapper — skipping fallback")
            return []
        actions = self.vision.extract_actionable(screenshot_path, state_str)
        if actions:
            self.fallback_calls += 1
            logger.info(
                "[chain] vision fallback: %d actions (primary %d, fallback %d)",
                len(actions), self.primary_calls, self.fallback_calls,
            )
        return actions

    def stats(self) -> dict:
        """잡 끝에 record — primary vs fallback 비율."""
        total = self.primary_calls + self.fallback_calls
        return {
            "framework": self.framework,
            "primary_calls": self.primary_calls,
            "fallback_calls": self.fallback_calls,
            "fallback_ratio": (
                round(self.fallback_calls / total, 3) if total else 0.0
            ),
        }
