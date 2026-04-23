"""ViewTreeReader 추상 클래스 — framework별 UI 추출 인터페이스."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ViewTreeReader(ABC):
    """UI 요소 추출 및 액션 스코어링의 추상 인터페이스.

    구현체:
      - XMLViewTreeReader: 전통 XML 기반 Android 앱
      - ComposeViewTreeReader: Jetpack Compose
      - FlutterViewTreeReader: Flutter
      - RNViewTreeReader: React Native
    """

    #: Framework 이름 (subclass가 override)
    name: str = "base"

    @abstractmethod
    def score_action(self, view: dict, context: dict[str, Any]) -> float:
        """단일 view에 대한 액션 점수 계산.

        Args:
            view: UI 요소 dict (class, resource_id, text, content_desc, bounds, ...)
            context: 탐색 컨텍스트
                - canonical: 현재 화면의 canonical_id
                - visit_count: 해당 화면 방문 횟수
                - tried_actions: set[str] — 이미 시도한 action description

        Returns:
            점수 (높을수록 우선순위). 스코어링 규칙은 framework별로 다름.
        """
        raise NotImplementedError

    @abstractmethod
    def is_actionable(self, view: dict) -> bool:
        """이 view가 클릭/상호작용 대상인지 판단.

        XMLViewTreeReader는 clickable 속성 + scrollable을 본다.
        ComposeViewTreeReader는 clickable=false라도 text/desc 있으면 actionable.
        """
        raise NotImplementedError

    def get_action_desc(self, view: dict) -> str:
        """액션 설명 문자열 생성 (tried_actions 추적 키)."""
        rid = view.get("resource_id", "")
        text = view.get("text", "")
        desc = view.get("content_desc", "")
        cls = view.get("class", "")
        bounds = view.get("bounds", "")
        label = rid or desc or text or cls or str(bounds)
        action = "scroll" if view.get("scrollable") and not view.get("clickable") else "click"
        return f"{action} {label}"

    def prepare_device(self, device_serial: str) -> None:
        """탐색 시작 전 디바이스 설정 훅 (override 가능).

        예: FlutterViewTreeReader는 `setprop debug.flutter.semantics 1` 호출.
        """
        pass
