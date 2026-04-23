"""ComposeViewTreeReader — Jetpack Compose 앱용.

Compose 앱의 특징:
- uiautomator XML에서 clickable=false로 나오는 경우가 많음 (semantic 미완)
- 대부분 요소가 android.view.View 클래스
- text / content-desc 기반으로 식별해야 함

XMLViewTreeReader를 상속하여 is_actionable만 확장:
- clickable=false라도 text/content-desc 있는 View는 탭 가능으로 간주
- 위치 기반 보너스 (하단 탭 영역 등)
"""

from __future__ import annotations

import re
from typing import Any

from .xml_view_tree import XMLViewTreeReader


class ComposeViewTreeReader(XMLViewTreeReader):
    name = "compose"

    def is_actionable(self, view: dict) -> bool:
        """Compose fallback: clickable=false라도 text/desc 있는 View는 actionable."""
        if not view.get("visible", True):
            return False

        if view.get("clickable") or view.get("scrollable"):
            return True

        # Compose 특유 케이스: bare View + text/content-desc
        has_text = bool(view.get("text") or view.get("content_desc"))
        is_view_like = view.get("class") in (
            "View",
            "android.view.View",
            "ComposeView",
            "androidx.compose.ui.platform.ComposeView",
        )
        if has_text and is_view_like:
            return True

        return False

    def score_action(self, view: dict, context: dict[str, Any]) -> float:
        score = super().score_action(view, context)

        # 화면 위치 기반 보너스 — 하단(nav bar)/상단(header) 영역에 가점
        try:
            bounds = view.get("bounds", "")
            y_center = self._bounds_y_center(bounds)
            if y_center is not None:
                # 해상도는 알 수 없으나 일반적 임계값 사용
                if y_center > 1500:  # 하단 nav bar 추정
                    score += 1.5
                elif y_center < 300:  # 상단 header 추정
                    score += 1.0
        except Exception:
            pass

        # text/desc 있는 bare View는 "probable button" 보너스
        cls = view.get("class", "")
        has_text = bool(view.get("text") or view.get("content_desc"))
        if has_text and cls in ("View", "android.view.View") and not view.get("clickable"):
            score += 1.0

        return score

    @staticmethod
    def _bounds_y_center(bounds) -> float | None:
        """bounds에서 y 중심 좌표 추출."""
        if isinstance(bounds, dict):
            return (bounds.get("y1", 0) + bounds.get("y2", 0)) / 2
        if isinstance(bounds, list) and len(bounds) == 2:
            return (bounds[0][1] + bounds[1][1]) / 2
        if isinstance(bounds, str):
            nums = re.findall(r"\d+", bounds)
            if len(nums) >= 4:
                return (int(nums[1]) + int(nums[3])) / 2
        return None
