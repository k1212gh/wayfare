"""ViewTreeChain + quality_checks 단위 테스트 — Framework 1차 분기."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from stage3_walk.view_tree_chain import ViewTreeChain
from stage3_walk.quality_checks import (
    quality_xml, quality_compose, quality_rn, quality_flutter, quality_webview,
    assess_quality,
)


def _v(cls="View", bounds="[0,0][1080,2400]", text="", desc="", clickable=False, rid=""):
    return {
        "class": cls, "bounds": bounds, "resource_id": rid,
        "text": text, "content_desc": desc, "clickable": clickable,
    }


# ─── quality_xml ─────────────────────────────────


def test_quality_xml_normal_screen():
    """clickable >= 2 + rid 풍부 → True."""
    views = [
        _v("LinearLayout"),
        _v("Button", text="OK", clickable=True, rid="ok_btn"),
        _v("Button", text="Cancel", clickable=True, rid="cancel_btn"),
        _v("TextView", text="Title", rid="title_tv"),
    ]
    assert quality_xml(views) is True


def test_quality_xml_no_clickable():
    views = [_v("LinearLayout"), _v("TextView", text="hi", rid="tv1")]
    assert quality_xml(views) is False


def test_quality_xml_dominant_webview_returns_false():
    views = [
        _v("LinearLayout"),
        _v("WebView", "[0,0][1080,2200]"),  # 91% 영역
        _v("FrameLayout"),  # 자식 부족
    ]
    assert quality_xml(views) is False


# ─── quality_compose ────────────────────────────


def test_quality_compose_with_wrapper_and_text():
    """ComposeView wrapper + text views 5+ → True."""
    views = [
        _v("ComposeView"),
        _v("Text", text="Title"),
        _v("Text", text="Subtitle"),
        _v("Button", text="OK"),
        _v("Text", text="Description"),
        _v("Text", text="Footer"),
    ]
    assert quality_compose(views) is True


def test_quality_compose_wrapper_but_no_text():
    """ComposeView 만 있고 자식 없음 → False (semantic 비활성)."""
    views = [
        _v("ComposeView"),
        _v("FrameLayout"),
        _v("FrameLayout"),
    ]
    assert quality_compose(views) is False


def test_quality_compose_no_wrapper_with_clickable():
    """wrapper 없어도 clickable >= 3 → True."""
    views = [
        _v("LinearLayout"),
        _v("Button", clickable=True),
        _v("Button", clickable=True),
        _v("Button", clickable=True),
    ]
    assert quality_compose(views) is True


# ─── quality_rn ──────────────────────────────────


def test_quality_rn_react_views():
    """ReactView / RCT 5+ → True. 가시 데이터 없어도 RN view 자체로 판정."""
    # _v default 가 모든 신호 비어있음 — clickable/rid/content_desc 다 0.
    # 그래도 RN view 5개+ 면 통과.
    views = [
        _v("LinearLayout"),
        _v("ReactViewGroup"),
        _v("ReactTextView"),
        _v("RCTText"),
        _v("RCTView"),
        _v("RCTView"),
        _v("RCTView"),  # 한 개 더 — 5개 보장
    ]
    assert quality_rn(views) is True


def test_quality_rn_clickable_with_signal():
    views = [
        _v("LinearLayout"),
        _v("Button", clickable=True, rid="btn_1"),
        _v("Button", clickable=True, desc="Submit"),
    ]
    assert quality_rn(views) is True


def test_quality_rn_dominant_webview_returns_false():
    """RN 메인이지만 OAuth WebView 진입 — fallback 필요."""
    views = [
        _v("LinearLayout"),
        _v("RNCWebView", "[0,0][1080,2200]"),
        _v("FrameLayout"),
    ]
    assert quality_rn(views) is False


# ─── quality_flutter ────────────────────────────


def test_quality_flutter_semantic_disabled():
    """FlutterView 만 + 자식 적음 → False (semantic 비활성)."""
    views = [
        _v("FlutterView"),
        _v(""),  # 빈 class
    ]
    assert quality_flutter(views) is False


def test_quality_flutter_semantic_active():
    """semantic 활성 시 text views 5+ → True."""
    views = [
        _v("FlutterView"),
        _v("Text", text="Login"),
        _v("Text", text="Email"),
        _v("Text", text="Password"),
        _v("Text", text="Forgot"),
        _v("Text", text="Sign Up"),
        _v("Text", text="Welcome"),
    ]
    assert quality_flutter(views) is True


# ─── quality_webview ────────────────────────────


def test_quality_webview_a11y_active():
    """dominant WebView 인데 자식 a11y 잘 잡힘 → True (분석 가능)."""
    views = [
        _v("LinearLayout"),
        _v("WebView", "[0,0][1080,2200]"),
        _v("View", text="Login", clickable=True),
        _v("View", text="email"),
        _v("View", text="password"),
        _v("View", text="Submit", clickable=True),
        _v("View", text="Help"),
        _v("View", text="Welcome"),
    ]
    assert quality_webview(views) is True


def test_quality_webview_blank():
    """dominant WebView + 자식 부족 → False (fallback 필요)."""
    views = [
        _v("LinearLayout"),
        _v("WebView", "[0,0][1080,2200]"),
        _v("FrameLayout"),
    ]
    assert quality_webview(views) is False


# ─── assess_quality dispatcher ──────────────────


def test_assess_quality_dispatches_by_framework():
    views_xml = [_v("LinearLayout"),
                 _v("Button", clickable=True, rid="b1"),
                 _v("Button", clickable=True, rid="b2")]
    assert assess_quality("xml", views_xml) is True
    assert assess_quality("XML", views_xml) is True  # case-insensitive


def test_assess_quality_unknown_framework_falls_back_xml():
    views = [_v("LinearLayout"),
             _v("Button", clickable=True, rid="b1"),
             _v("Button", clickable=True, rid="b2")]
    assert assess_quality("vapor", views) is True  # xml 룰 적용


# ─── ViewTreeChain ─────────────────────────────


def test_chain_primary_sufficient_increments_count():
    chain = ViewTreeChain("xml")
    views = [_v("LinearLayout"),
             _v("Button", clickable=True, rid="b1"),
             _v("Button", clickable=True, rid="b2")]
    assert chain.is_primary_sufficient(views) is True
    assert chain.primary_calls == 1
    assert chain.fallback_calls == 0


def test_chain_fallback_no_vision_returns_empty():
    """vision_tapper None 이면 fallback 시도해도 빈 리스트."""
    chain = ViewTreeChain("webview", vision_tapper=None)
    actions = chain.fallback_to_vision("/fake/path.png", "screen_a")
    assert actions == []
    assert chain.fallback_calls == 0


def test_chain_fallback_with_vision_calls_extract():
    mock_vc = MagicMock()
    mock_vc.extract_actionable.return_value = [
        {"label": "OK", "bounds": [100, 100, 200, 200], "confidence": 0.9}
    ]
    chain = ViewTreeChain("webview", vision_tapper=mock_vc)
    actions = chain.fallback_to_vision("/fake/path.png", "screen_a")
    assert len(actions) == 1
    assert chain.fallback_calls == 1
    mock_vc.extract_actionable.assert_called_once_with("/fake/path.png", "screen_a")


def test_chain_stats_format():
    chain = ViewTreeChain("rn")
    chain.primary_calls = 10
    chain.fallback_calls = 2
    s = chain.stats()
    assert s["framework"] == "rn"
    assert s["primary_calls"] == 10
    assert s["fallback_calls"] == 2
    assert abs(s["fallback_ratio"] - 2/12) < 0.001
