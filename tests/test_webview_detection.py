"""WebView dominant detection 단위 테스트."""

from __future__ import annotations

from stage3_walk.view_tree_parser import is_webview_dominant


def _v(cls="View", bounds="[0,0][1080,2400]", text="", desc="", clickable=False):
    return {
        "class": cls, "bounds": bounds,
        "text": text, "content_desc": desc, "clickable": clickable,
    }


def test_no_webview_returns_false():
    views = [
        _v("LinearLayout", "[0,0][1080,2400]"),
        _v("Button", "[0,100][200,200]", text="OK", clickable=True),
    ]
    is_dom, wv = is_webview_dominant(views)
    assert not is_dom
    assert wv is None


def test_small_webview_not_dominant():
    """WebView 가 화면 30% 만 차지 — 일반 화면."""
    views = [
        _v("LinearLayout", "[0,0][1080,2400]"),
        _v("WebView", "[0,0][500,1000]"),  # 약 19% 영역
        _v("Button", "[0,1100][200,1200]", text="OK", clickable=True),
        _v("Button", "[0,1300][200,1400]", text="Cancel", clickable=True),
    ]
    is_dom, wv = is_webview_dominant(views)
    assert not is_dom


def test_dominant_webview_with_a11y_children_not_problem():
    """WebView 가 dominant 하지만 자식 a11y 잡힘 — 분석 가능."""
    views = [
        _v("LinearLayout", "[0,0][1080,2400]"),
        _v("WebView", "[0,0][1080,2200]"),  # 화면 거의 전부
        # 자식 element 5+ — a11y 활성됨
        _v("View", text="Login", clickable=True),
        _v("View", text="email"),
        _v("View", text="password"),
        _v("View", text="Forgot password?", clickable=True),
        _v("View", text="Sign up", clickable=True),
        _v("View", text="Welcome"),
    ]
    is_dom, wv = is_webview_dominant(views)
    assert not is_dom  # 분석 가능 — fallback 불필요
    assert wv is None


def test_dominant_webview_empty_children_is_problem():
    """WebView 가 dominant + 자식 a11y 비활성 — vision fallback 필요."""
    views = [
        _v("LinearLayout", "[0,0][1080,2400]"),
        _v("WebView", "[0,0][1080,2200]"),  # 화면 91%
        # 자식 의미 있는 view 0
        _v("FrameLayout", clickable=False),
        _v("FrameLayout", clickable=False),
    ]
    is_dom, wv = is_webview_dominant(views)
    assert is_dom
    assert wv is not None
    assert "WebView" in wv["class"]


def test_rn_webview_class_recognized():
    """React Native 의 RNCWebView / RCTWebView 도 인식."""
    for cls in ["RNCWebView", "RCTWebView", "ChromeWebView"]:
        views = [
            _v("LinearLayout", "[0,0][1080,2400]"),
            _v(cls, "[0,0][1080,2200]"),
        ]
        is_dom, wv = is_webview_dominant(views)
        assert is_dom, f"{cls} 인식 실패"
        assert cls in wv["class"]


def test_empty_views():
    is_dom, wv = is_webview_dominant([])
    assert not is_dom
    assert wv is None
