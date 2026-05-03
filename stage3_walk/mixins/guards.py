"""Guards mixin — pause/resume on auto-detected login screens.

When the walker hits a login/auth screen it writes ``paused.json`` and blocks
in :py:meth:`GuardsMixin._wait_for_resume` until the user (via the dashboard
``Resume`` button) removes the file or the pause times out.
"""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)


class GuardsMixin:
    """Login detection + cooperative pause/resume."""

    _LOGIN_KEYWORDS = (
        "login", "log_in", "signin", "sign_in", "logon",
        "password", "passwd", "pwd",
        "email", "username", "phone",
        "otp", "verify", "verification", "2fa",
        "auth", "oauth",
        # C 옵션 (2026-04-24): SaaS 앱 server URL / workspace 입력 화면
        "server url", "server_url", "domain", "host name", "hostname",
        "workspace url", "workspace_url", "tenant",
    )

    # 한국어 키워드 — 로그인 / 비밀번호 / 인증 / 서버 URL
    _LOGIN_KEYWORDS_KO = (
        "로그인", "비밀번호", "암호", "이메일", "전화번호",
        "인증번호", "인증 번호", "본인인증", "서버 주소", "서버주소",
    )

    def _detect_user_input_needed(self, state: dict) -> bool:
        """Heuristic: does the current screen require manual login / auth?

        Trigger 조건 (어느 하나만 해도):
          1) activity FQN 에 login/signin/auth 키워드
          2) password 타입 EditText 존재
          3) 영문 키워드 ≥2 종류 hit
          4) 한국어 키워드 ≥1 hit + EditText ≥1 (false-positive 방지)
          5) EditText ≥3 + login keyword ≥1 hit (server-URL 같은 dense form)
        """
        if self._paused_file.exists():
            return False  # already paused, don't re-trigger

        activity = (state.get("activity") or "").lower()
        if any(kw in activity for kw in ("login", "signin", "sign_in", "oauth", "auth")):
            return True

        views = state.get("views", [])
        has_password = False
        hit_kw: set[str] = set()
        hit_kw_ko: set[str] = set()
        edittext_count = 0
        for v in views:
            cls = (v.get("class") or "").lower()
            rid = (v.get("resource_id") or "").lower()
            desc_raw = v.get("content_desc") or ""
            text_raw = v.get("text") or ""
            desc = desc_raw.lower()
            text = text_raw.lower()
            combined = f"{rid} {desc} {text}"
            combined_ko = f"{desc_raw} {text_raw}"  # 한국어는 case 무관
            if "edittext" in cls:
                edittext_count += 1
                if "password" in rid or "password" in desc or "pwd" in rid:
                    has_password = True
            for kw in self._LOGIN_KEYWORDS:
                if kw in combined:
                    hit_kw.add(kw)
            for kw in self._LOGIN_KEYWORDS_KO:
                if kw in combined_ko:
                    hit_kw_ko.add(kw)

        if has_password:
            return True
        # 2026-05-02: license / FAQ / 본문-only 페이지 false positive 차단.
        # OpenSourceLicenseActivity (a4b6c66f state_0192) 의 license 텍스트가
        # 'email' / 'auth' (AUTHORS) / 'domain' (public domain) 부분문자열 매칭으로
        # 발동 → paused 무한 대기. EditText 1개 이상 가드 추가하면 진짜 login 폼
        # (이메일+비번) 만 발동.
        if len(hit_kw) >= 2 and edittext_count >= 1:
            return True
        # 한국어 1개 hit + EditText 있으면 (한국 앱 false-positive 적음)
        if hit_kw_ko and edittext_count >= 1:
            return True
        # SaaS server-URL 폼: dense EditText + login keyword 1개
        if edittext_count >= 3 and len(hit_kw) >= 1:
            return True
        return False

    def _request_user_input(self, state: dict) -> None:
        """Write paused.json so dashboard shows a banner + Resume button."""
        reason = "login/auth screen detected"
        activity = state.get("activity", "")
        if activity and any(kw in activity.lower() for kw in ("login", "signin", "auth")):
            reason = f"login screen: {activity.rsplit('.', 1)[-1]}"
        payload = {
            "reason": reason,
            "auto": True,
            "activity": activity,
            "since": time.time(),
        }
        try:
            self._paused_file.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("PAUSED — waiting for user: %s", reason)
        except Exception as e:
            logger.warning("Failed to write paused.json: %s", e)

    def _wait_for_resume(self) -> bool:
        """Block until paused.json is removed, cancel is set, or timeout.

        Returns True if we should continue, False if walk should abort.
        """
        pause_start = time.time()
        logged = False
        while self._paused_file.exists():
            if self._cancel_flag.exists():
                logger.info("Cancel during pause — stopping")
                return False
            if (time.time() - pause_start) > self._pause_max_seconds:
                logger.warning("Pause timed out after %ds — cancelling", self._pause_max_seconds)
                try:
                    self._paused_file.unlink(missing_ok=True)
                except Exception:
                    pass
                return False
            if not logged:
                logger.info("Waiting for user to Resume (paused.json present)")
                logged = True
            time.sleep(2)
        logger.info("Resumed after %.1fs", time.time() - pause_start)
        return True
