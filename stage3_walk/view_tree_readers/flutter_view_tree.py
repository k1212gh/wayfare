"""FlutterViewTreeReader — Flutter 앱용.

Flutter 앱의 특징:
- Canvas에 그려지므로 uiautomator에는 보통 `FlutterView` 하나만 보임.
- Semantics가 활성화되면 내부 element들이 `content-desc` 있는 View로 노출.
- Semantics는 `debug.flutter.semantics=1` 설정 또는 앱 자체 설정에 따라 활성/비활성.
- resource-id 없음. `content-desc`가 Flutter의 Semantics label.

전략:
- prepare_device()에서 Semantics 강제 활성화 시도 (system property).
- is_actionable: content-desc가 있는 어떤 View이든 actionable로 간주.
- score: content-desc 기반 우선순위.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from .xml_view_tree import XMLViewTreeReader

logger = logging.getLogger(__name__)


class FlutterViewTreeReader(XMLViewTreeReader):
    name = "flutter"

    def prepare_device(self, device_serial: str) -> None:
        """Flutter Semantics 활성화. 이미 활성이면 no-op."""
        try:
            subprocess.run(
                ["adb", "-s", device_serial, "shell",
                 "setprop", "debug.flutter.semantics", "1"],
                capture_output=True, timeout=5,
            )
            logger.info("[flutter] enabled debug.flutter.semantics=1")
        except Exception as e:
            logger.debug("[flutter] setprop failed (non-fatal): %s", e)

    def is_actionable(self, view: dict) -> bool:
        if not view.get("visible", True):
            return False
        if view.get("clickable") or view.get("scrollable"):
            return True
        # Flutter: Semantics 노드는 대부분 clickable=false. content-desc로 탐지.
        cls = view.get("class", "")
        has_desc = bool(view.get("content_desc"))
        is_flutter_ish = (
            "flutter" in cls.lower()
            or cls in ("android.view.View", "View", "android.widget.FrameLayout")
        )
        if has_desc and is_flutter_ish:
            return True
        return False

    def score_action(self, view: dict, context: dict[str, Any]) -> float:
        score = super().score_action(view, context)
        cls = view.get("class", "")
        desc = view.get("content_desc", "") or ""
        # Flutter Semantics label 이 대부분 의미 명확 → 높게 평가
        if desc and "flutter" in cls.lower():
            score += 2.0
        elif desc and cls in ("android.view.View", "View"):
            score += 1.0
        # Flutter 내비게이션 힌트 (Semantics label 관용구)
        desc_low = desc.lower()
        for nav in ("back", "menu", "home", "search", "settings", "more"):
            if nav in desc_low:
                score += 0.5
                break
        return score
