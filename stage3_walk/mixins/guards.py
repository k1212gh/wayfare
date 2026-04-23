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
    )

    def _detect_user_input_needed(self, state: dict) -> bool:
        """Heuristic: does the current screen require manual login / auth?"""
        if self._paused_file.exists():
            return False  # already paused, don't re-trigger

        activity = (state.get("activity") or "").lower()
        if any(kw in activity for kw in ("login", "signin", "sign_in", "oauth", "auth")):
            return True

        views = state.get("views", [])
        has_password = False
        hit_kw: set[str] = set()
        for v in views:
            cls = (v.get("class") or "").lower()
            rid = (v.get("resource_id") or "").lower()
            desc = (v.get("content_desc") or "").lower()
            text = (v.get("text") or "").lower()
            combined = f"{rid} {desc} {text}"
            if "edittext" in cls and ("password" in rid or "password" in desc or "pwd" in rid):
                has_password = True
            for kw in self._LOGIN_KEYWORDS:
                if kw in combined:
                    hit_kw.add(kw)

        if has_password:
            return True
        # Need at least 2 distinct keywords to reduce false positives
        return len(hit_kw) >= 2

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
