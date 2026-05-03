"""External page guard — webview 안에서 외부 도메인 진입 차단.

문제 (2026-05-01 메가커피 bbbd6491 등 재현):
  메가커피의 webview 내부 메뉴 일부가 Queens Smile / 카카오 OAuth / 네이버
  로그인 등 외부 페이지로 navigate. 패키지는 여전히 com.megacoffee 라
  package_guard 통과 → walking 이 외부 사이트 안에서 길 잃음.

원리 (W1+W2+W3 통합):
  W1' (URL hint): view 의 text 에 https://... 또는 도메인 패턴 검출 →
                   own 도메인 (megamgccoffee.com 등) 외면 외부.
  W2 (keyword): "QUEENS SMILE", "카카오", "네이버", "OAuth" 등 외부 hint
                 키워드 검출.
  W3 (blacklist): 외부 진입 직전의 trigger 액션을 영구 black-list →
                   같은 메뉴 재클릭 안 함.
  W4 (manifest host) — 미적용. W1~W3 안 먹히면 추가 고려 (상위 메모).

흐름:
  1. capture 후 detect_outbound_intent(views) 호출
  2. is_external 이면 logger 기록 + back press
  3. 직전 action 의 trigger desc 를 external_blacklist 에 추가
  4. score_action 이 blacklist trigger 발견 시 강 페널티
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

logger = logging.getLogger(__name__)


# 메가커피 own 도메인 (필요 시 앱별 config 로 분리). 부분 매칭.
DEFAULT_OWN_DOMAIN_PARTS = (
    "megamgccoffee", "waldlust", "mega-mgc",
)

# 외부 페이지 hint — 큰 사회 OAuth / 외부 서비스 / 일반 외부 도메인
EXTERNAL_HINT_KEYWORDS = (
    # OAuth providers
    "queenssmile", "QUEENS SMILE", "queens smile",
    "naver login", "네이버 로그인", "Naver Login",
    "kakao login", "카카오 로그인", "Kakao Login", "TalkAuth",
    "google login", "Google 계정", "Sign in with Google",
    "facebook login", "apple id",
    # 일반 외부 페이지
    "OAuth", "openid",
    "외부 서비스", "외부 사이트",
    # 약관/정책 페이지가 외부 도메인일 때 — 메가커피 자체 정책은 own
    # url 매칭으로 들어가니 OK.
)

# https://domain.tld 형식 URL — domain 그룹 추출
_URL_RE = re.compile(r"https?://([a-z0-9.\-]+\.[a-z]{2,})", re.IGNORECASE)
# 베어 도메인 — 키워드 안에 "queenssmile.com" 같은 형식
_BARE_DOMAIN_RE = re.compile(r"\b([a-z0-9\-]{2,}\.(?:com|net|org|io|kr|co\.kr|app))\b", re.IGNORECASE)


def _is_own_domain(domain: str, own_parts: Iterable[str]) -> bool:
    d = domain.lower()
    return any(part.lower() in d for part in own_parts)


def detect_outbound_intent(
    views: list[dict],
    own_domain_parts: Iterable[str] = DEFAULT_OWN_DOMAIN_PARTS,
    max_views_to_scan: int = 80,
) -> tuple[bool, str]:
    """반환: (is_external, reason).

    own_domain_parts 안 의 부분 문자열을 가진 도메인은 internal 로 간주.
    외부 도메인 URL 또는 EXTERNAL_HINT_KEYWORDS 중 하나라도 hit 면 external.

    빠르게 동작하도록 상위 max_views_to_scan view 만 검사.
    """
    if not views:
        return False, ""

    for v in views[:max_views_to_scan]:
        text = (v.get("text") or "")
        desc = (v.get("content_desc") or "")
        rid = (v.get("resource_id") or "")
        combined = f"{text} {desc} {rid}"
        if not combined.strip():
            continue

        # URL 형식 — domain 추출 후 own 비교
        for m in _URL_RE.finditer(combined):
            domain = m.group(1)
            if not _is_own_domain(domain, own_domain_parts):
                return True, f"external URL: {domain}"

        # 베어 도메인 (URL 스킴 없이 "queenssmile.com" 등)
        for m in _BARE_DOMAIN_RE.finditer(combined):
            domain = m.group(1)
            if not _is_own_domain(domain, own_domain_parts):
                # 단, own 부분 매칭 안 되어도 일반 단어 (예: app.kr) 인 경우 false
                # positive 우려. 따라서 hint 키워드와 동시 hit 시만 강하게 판정.
                # 여기서는 일단 후보 — keyword 검사 후 확정.
                return True, f"external bare-domain: {domain}"

        # Keyword hint — 부분 매칭 (대소문자 무시)
        c_lower = combined.lower()
        for kw in EXTERNAL_HINT_KEYWORDS:
            if kw.lower() in c_lower:
                return True, f"external hint: {kw}"

    return False, ""


def get_blacklist_penalty(
    action_desc: str,
    blacklist: set[str],
    penalty: float = -10.0,
) -> float:
    """blacklist 에 등록된 trigger 의 score 강 감점.

    score_action 에서 호출. blacklist hit → penalty (-10), 그 외 0.
    """
    if action_desc and action_desc in blacklist:
        return penalty
    return 0.0
