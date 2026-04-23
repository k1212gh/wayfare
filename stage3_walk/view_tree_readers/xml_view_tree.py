"""XMLViewTreeReader — 전통 XML 기반 Android 앱용 (Java/Kotlin).

resource-id, clickable 속성을 신뢰. 가장 잘 동작하는 기준 Extractor.
"""

from __future__ import annotations

from typing import Any

from .base import ViewTreeReader

# 네비게이션 관련 키워드
NAV_KEYWORDS = [
    "tab", "menu", "nav", "drawer", "settings", "more",
    "home", "profile", "search", "toolbar", "option",
    "notification", "account", "calendar", "event",
    "write", "create", "add", "new", "compose", "edit",
    "back", "close", "cancel", "done", "save",
    "detail", "info", "about", "help",
    # Drawer / overflow / avatar / settings entry points (Tier-1 addition)
    "hamburger", "menu_icon", "drawer_toggle", "drawer_indicator",
    "avatar", "user_image", "profile_pic", "account_circle",
    "gear", "cog", "preferences",
    "overflow", "kebab", "three_dot",
]

# Strong signals in content-desc that reveal hidden nav entry points
NAV_DESC_STRONG = [
    "open drawer", "open menu", "open navigation",
    "navigation drawer", "main menu",
    "your profile", "view profile", "account", "settings",
    "more options", "options menu",
]


class XMLViewTreeReader(ViewTreeReader):
    name = "xml"

    def is_actionable(self, view: dict) -> bool:
        """clickable / scrollable / long_clickable 모두 인정."""
        if not view.get("visible", True):
            return False
        return bool(view.get("clickable") or view.get("scrollable")
                    or view.get("long_clickable"))

    def get_action_desc(self, view: dict) -> str:
        """액션 설명 문자열. long-press 전용이면 'longclick' 접두."""
        rid = view.get("resource_id", "")
        text = view.get("text", "")
        desc = view.get("content_desc", "")
        cls = view.get("class", "")
        bounds = view.get("bounds", "")
        label = rid or desc or text or cls or str(bounds)
        # Prefer click if clickable; longpress-only if view is long_clickable
        # but not clickable — avoids conflating a clickable item's longclick
        # variant with its click (handled separately if we expose both later).
        if view.get("clickable"):
            action = "click"
        elif view.get("long_clickable"):
            action = "longclick"
        elif view.get("scrollable"):
            action = "scroll"
        else:
            action = "click"
        return f"{action} {label}"

    def score_action(self, view: dict, context: dict[str, Any]) -> float:
        rid = view.get("resource_id", "")
        text = view.get("text", "")
        desc = view.get("content_desc", "")
        cls = view.get("class", "")
        combined = (rid + text + desc + cls).lower()

        canonical = context.get("canonical", "")
        visit_count = context.get("visit_count", 0)
        tried_actions: set[str] = context.get("tried_actions", set())
        action_desc = self.get_action_desc(view)

        score = 1.0

        # === 미시도 액션 보너스 (가장 큰 신호) ===
        if action_desc not in tried_actions:
            score += 4.0

        # 네비게이션 요소 보너스
        if any(k in combined for k in NAV_KEYWORDS):
            score += 2.0
        # 드로어/프로필/설정 강한 시그널 — content-desc 기반
        desc_lower = desc.lower()
        if any(s in desc_lower for s in NAV_DESC_STRONG):
            score += 3.0

        # 클래스 보너스
        if "Button" in cls:
            score += 1.5
        elif "ImageView" in cls or "ImageButton" in cls:
            score += 1.0
        elif "Tab" in cls:
            score += 2.0

        # resource-id 있는 요소 보너스 (실제 버튼일 가능성 높음)
        if rid:
            score += 0.5

        # === 패널티 ===
        score -= visit_count * 1.5  # 방문 횟수

        if action_desc in tried_actions:
            score -= 8.0  # 이미 시도한 액션

        if "RecyclerView" in str(view.get("parent_class", "")):
            score -= 2.0  # 리스트 아이템

        if view.get("scrollable") and not view.get("clickable"):
            score -= 1.0  # scroll-only

        return score
