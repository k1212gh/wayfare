"""External page guard — webview 외부 도메인 진입 detect."""

from __future__ import annotations

from stage3_walk.outbound_intent_guard import (
    detect_outbound_intent,
    get_blacklist_penalty,
    DEFAULT_OWN_DOMAIN_PARTS,
)


def _v(text="", desc="", rid=""):
    return {"text": text, "content_desc": desc, "resource_id": rid}


# ─── detect_outbound_intent ──────────────────────────────

def test_empty_views_returns_internal():
    assert detect_outbound_intent([]) == (False, "")


def test_own_domain_url_is_internal():
    views = [_v("Visit https://www.megamgccoffee.com/menu")]
    is_ext, _ = detect_outbound_intent(views)
    assert is_ext is False


def test_external_url_detected():
    views = [_v("Visit https://accounts.google.com/login")]
    is_ext, reason = detect_outbound_intent(views)
    assert is_ext is True
    assert "accounts.google.com" in reason


def test_external_keyword_queens_smile():
    views = [_v("QUEENS SMILE 로그인")]
    is_ext, reason = detect_outbound_intent(views)
    assert is_ext is True
    assert "QUEENS SMILE" in reason or "queens smile" in reason.lower()


def test_external_keyword_kakao():
    views = [_v(desc="카카오 로그인")]
    is_ext, reason = detect_outbound_intent(views)
    assert is_ext is True


def test_external_oauth_keyword():
    views = [_v("OAuth 인증")]
    is_ext, reason = detect_outbound_intent(views)
    assert is_ext is True


def test_bare_domain_external():
    views = [_v("queenssmile.com")]
    is_ext, reason = detect_outbound_intent(views)
    assert is_ext is True


def test_internal_app_text_no_false_positive():
    views = [
        _v("메가커피 홈"),
        _v("커피 메뉴"),
        _v("주문하기"),
    ]
    is_ext, _ = detect_outbound_intent(views)
    assert is_ext is False


def test_resource_id_with_url_pattern():
    views = [_v(rid="https://external.example.com/x")]
    is_ext, _ = detect_outbound_intent(views)
    assert is_ext is True


def test_max_views_to_scan_limits_search():
    """80개 view 까지만 스캔 (성능). 81번째 외부 hint 는 무시."""
    views = [_v("내부 텍스트")] * 80 + [_v("QUEENS SMILE")]
    is_ext, _ = detect_outbound_intent(views, max_views_to_scan=80)
    assert is_ext is False
    is_ext2, _ = detect_outbound_intent(views, max_views_to_scan=200)
    assert is_ext2 is True


# ─── get_blacklist_penalty ──────────────────────────────

def test_blacklist_penalty_applied():
    assert get_blacklist_penalty("click 외부 메뉴@[0,0][100,100]", {"click 외부 메뉴@[0,0][100,100]"}) == -10.0


def test_blacklist_penalty_not_applied_when_not_listed():
    assert get_blacklist_penalty("click 안전한 액션", set()) == 0.0
    assert get_blacklist_penalty("click 안전한 액션", {"click 다른"}) == 0.0


def test_blacklist_empty_action_desc_returns_zero():
    assert get_blacklist_penalty("", {"click x"}) == 0.0
