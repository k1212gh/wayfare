"""R2 — SUBMIT 키워드 hit view 는 parent 무관 actionable.

2026-05-03 (메가커피 581e8cc8 회귀):
  옵션 화면의 "담기" / "주문하기" 가 clickable=False + parent="View" 로
  W6/F2 가드 모두 통과 X. SUBMIT_KEYWORDS hit + 짧은 텍스트는 parent 무관
  actionable.
"""

from __future__ import annotations

from stage3_walk.view_tree_readers.xml_view_tree import XMLViewTreeReader


def _v(text="", desc="", clickable=False, parent_cls="android.widget.View",
       cls="android.widget.TextView"):
    return {
        "class": cls,
        "text": text, "content_desc": desc,
        "clickable": clickable, "scrollable": False, "long_clickable": False,
        "visible": True,
        "parent_class": parent_cls,
        "bounds": [0, 100, 200, 180],
    }


# ─── R2 actionable 인정 ─────────────────────────────

def test_submit_kw_dam_gi():
    """담기 텍스트, parent='View' (메가커피 옵션 화면) → actionable."""
    e = XMLViewTreeReader()
    assert e.is_actionable(_v(text="담기")) is True


def test_submit_kw_juhmun():
    e = XMLViewTreeReader()
    assert e.is_actionable(_v(text="주문하기")) is True


def test_submit_kw_with_parent_anywhere():
    """parent 가 LinearLayout/View/TextView/뭐든 → actionable."""
    e = XMLViewTreeReader()
    for p in ("android.widget.View", "android.widget.LinearLayout",
              "android.widget.FrameLayout", "androidx.core.widget.NestedScrollView"):
        assert e.is_actionable(_v(text="저장", parent_cls=p)) is True


def test_english_submit_kw():
    e = XMLViewTreeReader()
    assert e.is_actionable(_v(text="Add to cart")) is True


# ─── 길이 가드 — license 본문 false positive 차단 ─────────

def test_long_license_text_not_actionable():
    """license 본문에 '담기' 단어 들어있어도 텍스트 길이 21+ 면 무시."""
    e = XMLViewTreeReader()
    long_text = "이메일 인증 후 회원에 담기 위한 동의 절차"  # 21자+
    assert len(long_text) > 20
    assert e.is_actionable(_v(text=long_text)) is False


def test_empty_text_not_actionable():
    e = XMLViewTreeReader()
    assert e.is_actionable(_v(text="")) is False


# ─── 기존 동작 유지 ───────────────────────

def test_clickable_true_actionable():
    e = XMLViewTreeReader()
    assert e.is_actionable(_v(clickable=True)) is True


def test_no_submit_no_clickable_not_actionable():
    """submit 키워드 없고 clickable=False → 무시 (기존 동작)."""
    e = XMLViewTreeReader()
    assert e.is_actionable(_v(text="안녕하세요")) is False


def test_invisible_not_actionable():
    e = XMLViewTreeReader()
    v = _v(text="담기")
    v["visible"] = False
    assert e.is_actionable(v) is False
