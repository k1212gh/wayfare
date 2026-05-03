"""W6 — webview 안 clickable=False 텍스트 view 도 actionable 인정.

2026-05-02 (메가커피 e2a2c46f 회귀):
  webview 의 메뉴/메가오더/주문 텍스트가 clickable=False TextView 로만
  노출되어 walker 가 단 한 번도 클릭 못 함. is_actionable() 에 W6 케이스
  추가하여 webview 자식 텍스트 (적당한 height) 는 actionable 후보로.
"""

from __future__ import annotations

from stage3_walk.view_tree_readers.xml_view_tree import XMLViewTreeReader


def _v(text="", desc="", clickable=False, parent_cls="", bounds=(0, 100, 200, 180), cls="android.widget.TextView"):
    return {
        "class": cls,
        "text": text,
        "content_desc": desc,
        "clickable": clickable,
        "scrollable": False,
        "long_clickable": False,
        "visible": True,
        "parent_class": parent_cls,
        "bounds": list(bounds),
    }


# ─── W6 actionable 인정 ─────────────────────────────

def test_webview_text_no_clickable_actionable():
    """parent 가 WebView + text 있는 TextView (height 80) → actionable."""
    e = XMLViewTreeReader()
    v = _v(text="메가오더", parent_cls="android.webkit.WebView")
    assert e.is_actionable(v) is True


def test_chromewebview_text_actionable():
    e = XMLViewTreeReader()
    v = _v(text="메뉴", parent_cls="com.google.android.webview.ChromeWebView")
    assert e.is_actionable(v) is True


def test_rn_webview_text_actionable():
    e = XMLViewTreeReader()
    v = _v(text="이벤트", parent_cls="com.reactnativecommunity.webview.RNCWebView")
    assert e.is_actionable(v) is True


# ─── W6 false positive 차단 ─────────────────────────

def test_normal_textview_outside_webview_not_actionable():
    """parent 가 일반 LinearLayout (webview 아님) → 기존 동작 (clickable=False 면 무시)."""
    e = XMLViewTreeReader()
    v = _v(text="메뉴", parent_cls="android.widget.LinearLayout")
    assert e.is_actionable(v) is False


def test_empty_text_in_webview_not_actionable():
    """webview 자식이지만 text/desc 둘 다 빈 경우 → 무시."""
    e = XMLViewTreeReader()
    v = _v(text="", desc="", parent_cls="android.webkit.WebView")
    assert e.is_actionable(v) is False


def test_too_tall_view_not_actionable():
    """webview 안인데 height 500 (너무 큰 wrapper) → 무시."""
    e = XMLViewTreeReader()
    v = _v(text="이벤트", parent_cls="android.webkit.WebView", bounds=(0, 0, 1080, 500))
    assert e.is_actionable(v) is False


def test_too_short_view_not_actionable():
    """height 10 (너무 작음 — 줄 간격 등) → 무시."""
    e = XMLViewTreeReader()
    v = _v(text="x", parent_cls="android.webkit.WebView", bounds=(0, 100, 50, 110))
    assert e.is_actionable(v) is False


def test_invisible_view_not_actionable():
    e = XMLViewTreeReader()
    v = _v(text="메뉴", parent_cls="android.webkit.WebView")
    v["visible"] = False
    assert e.is_actionable(v) is False


# ─── 기존 동작 유지 ─────────────────────────

def test_clickable_true_still_actionable():
    e = XMLViewTreeReader()
    v = _v(text="", clickable=True, parent_cls="android.widget.LinearLayout")
    assert e.is_actionable(v) is True


def test_scrollable_still_actionable():
    e = XMLViewTreeReader()
    v = _v(parent_cls="android.widget.LinearLayout", cls="android.widget.ScrollView")
    v["scrollable"] = True
    assert e.is_actionable(v) is True


# ─── score_action W6 페널티 ─────────────────────────

def test_w6_score_lower_than_native_clickable():
    """W6 actionable 은 진짜 clickable 보다 낮은 score (-0.5 페널티)."""
    e = XMLViewTreeReader()
    ctx = {"canonical": "c1", "visit_count": 0, "tried_actions": set()}
    native = _v(text="메뉴", clickable=True, parent_cls="android.widget.LinearLayout")
    w6 = _v(text="메뉴", clickable=False, parent_cls="android.webkit.WebView")
    s_native = e.score_action(native, ctx)
    s_w6 = e.score_action(w6, ctx)
    assert s_w6 < s_native, f"W6 score {s_w6} should be < native {s_native}"
