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
    assert detect_outbound_intent([]) == (False, "", None)


def test_own_domain_url_is_internal():
    views = [_v("Visit https://www.megamgccoffee.com/menu")]
    is_ext, _, _md = detect_outbound_intent(views)
    assert is_ext is False


def test_external_url_detected():
    views = [_v("Visit https://accounts.google.com/login")]
    is_ext, reason, _md = detect_outbound_intent(views)
    assert is_ext is True
    assert "accounts.google.com" in reason


def test_external_keyword_queens_smile():
    views = [_v("QUEENS SMILE 로그인")]
    is_ext, reason, _md = detect_outbound_intent(views)
    assert is_ext is True
    assert "QUEENS SMILE" in reason or "queens smile" in reason.lower()


def test_external_keyword_kakao():
    views = [_v(desc="카카오 로그인")]
    is_ext, reason, _md = detect_outbound_intent(views)
    assert is_ext is True


def test_external_oauth_keyword():
    views = [_v("OAuth 인증")]
    is_ext, reason, _md = detect_outbound_intent(views)
    assert is_ext is True


def test_bare_domain_external():
    views = [_v("queenssmile.com")]
    is_ext, reason, _md = detect_outbound_intent(views)
    assert is_ext is True


def test_internal_app_text_no_false_positive():
    views = [
        _v("메가커피 홈"),
        _v("커피 메뉴"),
        _v("주문하기"),
    ]
    is_ext, _, _md = detect_outbound_intent(views)
    assert is_ext is False


def test_resource_id_with_url_pattern():
    views = [_v(rid="https://external.example.com/x")]
    is_ext, _, _md = detect_outbound_intent(views)
    assert is_ext is True


def test_max_views_to_scan_limits_search():
    """80개 view 까지만 스캔 (성능). 81번째 외부 hint 는 무시."""
    views = [_v("내부 텍스트")] * 80 + [_v("QUEENS SMILE")]
    is_ext, _, _md = detect_outbound_intent(views, max_views_to_scan=80)
    assert is_ext is False
    is_ext2, _, _md2 = detect_outbound_intent(views, max_views_to_scan=200)
    assert is_ext2 is True


# ─── Feature A — global domain blacklist (2026-05-06) ─────


def test_global_domain_load_empty_when_no_file(tmp_path):
    from stage3_walk.outbound_intent_guard import load_global_domain_blacklist
    assert load_global_domain_blacklist(tmp_path) == set()


def test_global_domain_save_then_load(tmp_path):
    from stage3_walk.outbound_intent_guard import (
        load_global_domain_blacklist, append_global_domain,
    )
    append_global_domain(tmp_path, "queenssmile.com", "co.kr.waldlust.megacoffee")
    append_global_domain(tmp_path, "kauth.kakao.com", "co.kr.waldlust.megacoffee")
    domains = load_global_domain_blacklist(tmp_path)
    assert "queenssmile.com" in domains
    assert "kauth.kakao.com" in domains


def test_global_domain_hit_count_increment(tmp_path):
    """같은 도메인 재진입 시 hit_count 증가, 별도 entry 안 만듦."""
    import json
    from stage3_walk.outbound_intent_guard import append_global_domain
    append_global_domain(tmp_path, "kauth.kakao.com", "pkg.a")
    append_global_domain(tmp_path, "kauth.kakao.com", "pkg.b")
    append_global_domain(tmp_path, "kauth.kakao.com", "pkg.c")
    data = json.loads((tmp_path / "_global_domains.json").read_text(encoding="utf-8"))
    assert len(data["domains"]) == 1
    assert data["domains"][0]["hit_count"] == 3
    assert data["domains"][0]["first_seen_package"] == "pkg.a"


def test_global_domain_own_skipped(tmp_path):
    """A-4 own_domain 안전장치 — 자기 앱 도메인은 글로벌 등록 거부."""
    from stage3_walk.outbound_intent_guard import (
        append_global_domain, load_global_domain_blacklist,
    )
    append_global_domain(
        tmp_path, "megamgccoffee.com", "co.kr.waldlust.megacoffee",
        own_domain_parts=["megamgccoffee", "waldlust"],
    )
    assert load_global_domain_blacklist(tmp_path) == set()


def test_w4_global_domain_hit_returns_matched():
    """global_domains hit 시 (True, reason, matched_domain) 반환."""
    views = [_v("Continue with kauth.kakao.com")]
    is_ext, reason, matched = detect_outbound_intent(
        views, global_domains={"kauth.kakao.com"}
    )
    assert is_ext is True
    assert "global learned" in reason
    assert matched == "kauth.kakao.com"


def test_w4_first_priority_over_keywords():
    """W4 (global) 가 W2 (keyword) 보다 먼저 — 학습된 도메인이 빠르게 검출."""
    views = [_v("Continue with foobar.example.com (KAKAO LOGIN)")]
    is_ext, reason, matched = detect_outbound_intent(
        views, global_domains={"foobar.example.com"}
    )
    assert is_ext is True
    assert matched == "foobar.example.com"   # global hit 먼저


# ─── A-5 derive_own_domain_parts ───────────────────────────


def test_derive_from_fixture_explicit():
    from stage3_walk.outbound_intent_guard import derive_own_domain_parts
    fixture = {"own_domain_parts": ["myapp", "myservice"]}
    assert derive_own_domain_parts("com.x.myapp", fixture=fixture) == ["myapp", "myservice"]


def test_derive_from_manifest_intent_filter():
    from stage3_walk.outbound_intent_guard import derive_own_domain_parts
    static_info = {
        "activities": [{
            "intent_filters": [{"data": [{"host": "m.myservice.com"}]}],
        }]
    }
    parts = derive_own_domain_parts("com.x.myapp", static_info=static_info)
    assert "myservice" in parts


def test_derive_from_package_reverse():
    from stage3_walk.outbound_intent_guard import derive_own_domain_parts
    parts = derive_own_domain_parts("co.kr.waldlust.megacoffee")
    # 일반 단어 (com/net/co/kr) 제외, 4글자+ segment
    assert "megacoffee" in parts or "waldlust" in parts


def test_derive_empty_when_nothing():
    from stage3_walk.outbound_intent_guard import derive_own_domain_parts
    assert derive_own_domain_parts("") == []


# ─── get_blacklist_penalty ──────────────────────────────

def test_blacklist_penalty_applied():
    assert get_blacklist_penalty("click 외부 메뉴@[0,0][100,100]", {"click 외부 메뉴@[0,0][100,100]"}) == -10.0


def test_blacklist_penalty_not_applied_when_not_listed():
    assert get_blacklist_penalty("click 안전한 액션", set()) == 0.0
    assert get_blacklist_penalty("click 안전한 액션", {"click 다른"}) == 0.0


def test_blacklist_empty_action_desc_returns_zero():
    assert get_blacklist_penalty("", {"click x"}) == 0.0
