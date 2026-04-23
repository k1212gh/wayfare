"""RNViewTreeReader — React Native 앱용.

RN 앱의 특징:
- JS bridge로 네이티브 뷰 조작. uiautomator로는 보이긴 함.
- 클래스가 `com.facebook.react.views.*` 계열 (ReactViewGroup, ReactTextView 등)
- resource-id 거의 없음. `content-desc`가 JS의 `accessibilityLabel` / `testID`를 담음.
- Scrollable: `ReactScrollView`, `ReactHorizontalScrollView`
"""

from __future__ import annotations

from typing import Any

from .xml_view_tree import XMLViewTreeReader


class RNViewTreeReader(XMLViewTreeReader):
    name = "react-native"

    # RN-specific class name signatures
    RN_VIEW_CLASSES = (
        "com.facebook.react.views.view.ReactViewGroup",
        "com.facebook.react.views.text.ReactTextView",
        "com.facebook.react.views.image.ReactImageView",
        "com.facebook.react.views.scroll.ReactScrollView",
        "com.facebook.react.views.scroll.ReactHorizontalScrollView",
        "com.facebook.react.views.textinput.ReactEditText",
        "com.facebook.react.ReactRootView",
    )

    def is_actionable(self, view: dict) -> bool:
        if not view.get("visible", True):
            return False
        if view.get("clickable") or view.get("scrollable"):
            return True
        # RN common pattern: ReactViewGroup with accessibilityLabel but
        # clickable=false (because the TouchableOpacity wrapper translates
        # touches at the JS level, not via native onClickListener).
        cls = view.get("class", "")
        has_a11y = bool(view.get("content_desc"))
        if has_a11y and any(rcls in cls for rcls in ("ReactViewGroup",
                                                      "ReactImageView",
                                                      "ReactTextView")):
            return True
        return False

    def score_action(self, view: dict, context: dict[str, Any]) -> float:
        score = super().score_action(view, context)
        cls = view.get("class", "")
        # Prefer RN-specific clickable-equivalents
        if any(rcls in cls for rcls in self.RN_VIEW_CLASSES):
            if view.get("content_desc"):  # accessibilityLabel/testID present
                score += 1.5
            elif view.get("text"):
                score += 1.0
        return score
