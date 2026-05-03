"""Framework-specific quality checks — ViewTreeChain 의 1차 분기.

원리: 한 앱 안에 framework 가 mixed (Mattermost RN + OAuth WebView). framework
label 만으로는 부족 — 화면별 분석 가능성으로 분기.

각 함수: views (uiautomator dump) → bool
  True  = 이 framework 의 native extractor 로 충분히 분석 가능
  False = vision-LLM fallback 호출 권장
"""

from __future__ import annotations

from typing import Callable

from .view_tree_parser import is_webview_dominant


def quality_xml(views: list[dict]) -> bool:
    """XML / Native Android — clickable + rid 명확.

    실측 (DeskClock 8b72067f): clickable >= 2 + rid 풍부 → 정상.
    """
    if not views:
        return False
    n_clickable = sum(1 for v in views if v.get("clickable"))
    n_rid = sum(1 for v in views if v.get("resource_id"))
    # WebView dominant 면 XML 분석 무용
    is_wv, _ = is_webview_dominant(views)
    if is_wv:
        return False
    return n_clickable >= 2 and n_rid >= 1


def quality_compose(views: list[dict]) -> bool:
    """Compose — wrapper clickable=False 흔함. text 풍부함이 신호.

    실측 (Calendar c122650b): primitives 9/93. wrapper 안 자식 들어갈 수 있나
    + text views 풍부 OR clickable >= 3 으로 판단.
    """
    if not views:
        return False
    is_wv, _ = is_webview_dominant(views)
    if is_wv:
        return False
    has_compose_wrapper = any("ComposeView" in (v.get("class") or "") for v in views)
    n_text = sum(1 for v in views if v.get("text") or v.get("content_desc"))
    n_clickable = sum(1 for v in views if v.get("clickable"))
    if has_compose_wrapper:
        return n_text >= 5  # wrapper 인데 text 잡힘 → semantic 활성
    return n_clickable >= 3


def quality_rn(views: list[dict]) -> bool:
    """React Native — testID → resource_id 매핑 또는 ReactView 풍부.

    실측 (Mattermost 5ba3011d): actionable=63/78 (81%). RN view 잘 dump 됨.
    """
    if not views:
        return False
    is_wv, _ = is_webview_dominant(views)
    if is_wv:
        return False  # OAuth/payment WebView 진입 — vision fallback
    n_clickable = sum(1 for v in views if v.get("clickable"))
    n_signal = sum(1 for v in views
                   if v.get("resource_id") or v.get("content_desc"))
    rn_views = sum(1 for v in views
                   if "ReactView" in (v.get("class") or "")
                   or "RCT" in (v.get("class") or ""))
    return (n_clickable >= 2 and n_signal >= 1) or rn_views >= 5


def quality_flutter(views: list[dict]) -> bool:
    """Flutter — semantic tree 활성 시만. production 앱은 보통 비활성.

    semantic 비활성 시: 단일 FlutterView Canvas — views 거의 비어있음.
    semantic 활성 시: text views 풍부 (semantic node 가 view 로 노출).
    """
    if not views:
        return False
    flutter_only = all(
        "FlutterView" in (v.get("class") or "") or not v.get("class")
        for v in views[:5]
    )
    if flutter_only and len(views) < 10:
        return False  # semantic 비활성 → 빈 화면
    n_text = sum(1 for v in views if v.get("text"))
    return n_text >= 5


def quality_webview(views: list[dict]) -> bool:
    """WebView — 안 element 추출 가능?

    멘토 의견 + 실측 (view_tree_parser 가 WebView 안 view 도 일반 처리):
    a11y 활성된 element 가 자동 잡힘. dominant WebView + 자식 부족 시만 fallback.
    """
    is_wv, _ = is_webview_dominant(views)
    if not is_wv:
        return True  # WebView 가 dominant 아님 또는 a11y 자식 풍부 — OK
    return False  # dominant + 자식 부족 — fallback 필요


# Framework label → quality check 함수
QUALITY_CHECKS: dict[str, Callable[[list[dict]], bool]] = {
    "xml": quality_xml,
    "compose": quality_compose,
    "react-native": quality_rn,
    "rn": quality_rn,
    "flutter": quality_flutter,
    "webview": quality_webview,
}


def assess_quality(framework: str, views: list[dict]) -> bool:
    """Framework label 따른 quality check 호출. unknown framework 면 xml 로 fallback."""
    fn = QUALITY_CHECKS.get((framework or "xml").lower(), quality_xml)
    try:
        return fn(views)
    except Exception:
        return True  # safe default — primary extractor 시도
