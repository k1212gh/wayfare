"""Login guard (_detect_user_input_needed) — false positive 회귀.

2026-05-02: 메가커피 a4b6c66f 의 OpenSourceLicenseActivity 가 license 텍스트
의 부분문자열 매칭 ('email', 'auth' in AUTHORS, 'domain' in public domain) 으로
paused 발동된 회귀. EditText 가드 추가로 차단되어야.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


def _make_guards(tmp_path: Path):
    """GuardsMixin 인스턴스 fake — paused/cancel 파일 경로만 셋업."""
    from stage3_walk.mixins.guards import GuardsMixin

    class _Probe(GuardsMixin):
        def __init__(self, tmp):
            self._paused_file = tmp / "paused.json"
            self._cancel_flag = tmp / "cancel.flag"
            self._pause_max_seconds = 10

    return _Probe(tmp_path)


def _v(cls="android.widget.TextView", text="", desc="", rid=""):
    return {"class": cls, "text": text, "content_desc": desc, "resource_id": rid}


# ─── False positive 차단 ──────────────────────────────

def test_license_text_no_edittext_does_not_pause(tmp_path):
    """OpenSourceLicense 텍스트의 'email/auth/domain' 부분문자열 매칭 — EditText 없으면 발동 안 함."""
    g = _make_guards(tmp_path)
    state = {
        "activity": "com.naver.maps.map.app.OpenSourceLicenseActivity",
        "views": [
            _v(text=(
                "THE FOLLOWING SETS FORTH ATTRIBUTION NOTICES FOR third-party "
                "components. We thank the open source community. please email us "
                "at opensource@navercorp.com. AUTHORS list. public domain."
            )),
        ],
    }
    assert g._detect_user_input_needed(state) is False


def test_faq_page_with_keywords_no_pause(tmp_path):
    """FAQ 본문에 'login', 'password' 단어 있어도 EditText 없으면 발동 안 함."""
    g = _make_guards(tmp_path)
    state = {
        "activity": "com.example.HelpActivity",
        "views": [
            _v(text="자주 묻는 질문: How to login? How to reset password?"),
            _v(text="login 절차 안내. email 인증 필요."),
        ],
    }
    assert g._detect_user_input_needed(state) is False


# ─── True positive 그대로 ──────────────────────────────

def test_real_login_form_pauses(tmp_path):
    """진짜 login 화면 — EditText (이메일/비밀번호) + 키워드 → 발동."""
    g = _make_guards(tmp_path)
    state = {
        "activity": "com.app.LoginActivity",
        "views": [
            _v(cls="android.widget.EditText", rid="email_input", text=""),
            _v(cls="android.widget.EditText", rid="password_input", text=""),
            _v(text="login"),
        ],
    }
    assert g._detect_user_input_needed(state) is True


def test_password_edittext_alone_pauses(tmp_path):
    """password EditText 만 있어도 (조건 2) 발동."""
    g = _make_guards(tmp_path)
    state = {
        "activity": "com.app.SomeActivity",
        "views": [
            _v(cls="android.widget.EditText", rid="user_password"),
        ],
    }
    assert g._detect_user_input_needed(state) is True


def test_login_in_activity_fqn_pauses(tmp_path):
    """activity FQN 에 login/auth 있으면 (조건 1) views 무관 발동."""
    g = _make_guards(tmp_path)
    state = {
        "activity": "com.app.SignInActivity",
        "views": [],
    }
    assert g._detect_user_input_needed(state) is True


def test_korean_keyword_with_edittext_pauses(tmp_path):
    """한국어 키워드 + EditText 1개 (조건 4) 발동."""
    g = _make_guards(tmp_path)
    state = {
        "activity": "com.app.SomeActivity",
        "views": [
            _v(cls="android.widget.EditText", rid="phone"),
            _v(text="비밀번호 입력"),
        ],
    }
    assert g._detect_user_input_needed(state) is True


def test_korean_keyword_without_edittext_no_pause(tmp_path):
    """한국어 키워드만 있고 EditText 없으면 (조건 4 가드 통과 X) 발동 안 함."""
    g = _make_guards(tmp_path)
    state = {
        "activity": "com.app.HelpActivity",
        "views": [
            _v(text="비밀번호 분실 시 본인인증 안내"),
        ],
    }
    assert g._detect_user_input_needed(state) is False


# ─── Edge cases ──────────────────────────────

def test_already_paused_does_not_re_trigger(tmp_path):
    """이미 paused.json 있으면 재발동 안 함."""
    g = _make_guards(tmp_path)
    g._paused_file.write_text("{}", encoding="utf-8")
    state = {
        "activity": "com.app.LoginActivity",
        "views": [_v(cls="android.widget.EditText", rid="password")],
    }
    assert g._detect_user_input_needed(state) is False
