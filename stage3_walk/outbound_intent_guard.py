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

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


# 메가커피 own 도메인 (필요 시 앱별 config 로 분리). 부분 매칭.
# A-5 (2026-05-06): 폐기 예정. derive_own_domain_parts() 의 fallback 으로만 사용.
DEFAULT_OWN_DOMAIN_PARTS = (
    "megamgccoffee", "waldlust", "mega-mgc",
)


# ───────────────────────────────────────────────────────────────────────
# Feature A — 앱간 글로벌 도메인 블랙리스트 (2026-05-06)
# ───────────────────────────────────────────────────────────────────────

_GLOBAL_DOMAINS_FILENAME = "_global_domains.json"


def load_global_domain_blacklist(learned_dir: Path) -> set[str]:
    """잡 시작 시 호출. 모든 앱이 학습한 외부 도메인 set.

    파일 없거나 깨졌으면 빈 set 반환 + 로그 (잡 실패 안 함).
    """
    p = Path(learned_dir) / _GLOBAL_DOMAINS_FILENAME
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        domains = {d["domain"].lower() for d in data.get("domains", []) if d.get("domain")}
        if domains:
            logger.info("[global] %d domains loaded from %s", len(domains), p.name)
        return domains
    except Exception as e:
        logger.warning("[global] load failed: %s", e)
        return set()


def append_global_domain(
    learned_dir: Path,
    domain: str,
    package: str,
    own_domain_parts: Iterable[str] = (),
) -> None:
    """W1/W4 가 새 외부 도메인 발견 시 호출.

    own_domain (자기 앱 도메인) 은 글로벌에 안 올림 (A-4 안전장치).
    이미 등록된 도메인이면 hit_count++.

    atomic rename 으로 race condition 방지 (다중 잡 동시).
    """
    domain = (domain or "").lower().strip()
    if not domain:
        return
    # A-4: 자기 앱 도메인은 글로벌 등록 거부
    if _is_own_domain(domain, own_domain_parts):
        return

    p = Path(learned_dir) / _GLOBAL_DOMAINS_FILENAME
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
        else:
            data = {"domains": [], "version": 1}
        domains_list = data.setdefault("domains", [])
        # 기존 entry 찾기
        existing = next((d for d in domains_list if d.get("domain") == domain), None)
        now = time.time()
        if existing:
            existing["hit_count"] = existing.get("hit_count", 0) + 1
            existing["last_seen_at"] = now
        else:
            domains_list.append({
                "domain": domain,
                "first_seen_package": package,
                "first_seen_at": now,
                "hit_count": 1,
                "last_seen_at": now,
            })
            logger.info("[global] +domain %r (from %s) → %d total",
                        domain, package, len(domains_list))
        # atomic write
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:
        logger.debug("[global] save failed for %r: %s", domain, e)


# ───────────────────────────────────────────────────────────────────────
# A-5 own_domain 자동 추출 (manifest fallback + package reverse)
# ───────────────────────────────────────────────────────────────────────


def derive_own_domain_parts(
    package: str,
    fixture: dict | None = None,
    static_info: dict | None = None,
) -> list[str]:
    """앱별 own_domain_parts 추출. 우선순위:

      1. fixture 의 'own_domain_parts' 명시 (1순위)
      2. AndroidManifest 의 intent-filter <data android:host> 자동 추출
      3. package 도메인 reverse (com.kr.waldlust.megacoffee → ['megacoffee', 'waldlust'])
      4. 빈 list (W1 비활성화, W2/W4 만 사용)

    A-5 (2026-05-06): fixture 없는 앱도 자동 동작 — DEFAULT 메가커피 하드코딩
    의존성 제거.
    """
    # 1. fixture 명시
    if fixture:
        explicit = fixture.get("own_domain_parts") or []
        if explicit:
            return [str(s).lower() for s in explicit]

    # 2. manifest intent-filter <data> hosts
    if static_info:
        hosts = []
        for af in static_info.get("activities", []) or []:
            for filt in af.get("intent_filters", []) or []:
                for d in filt.get("data", []) or []:
                    h = (d.get("host") or "").lower().strip()
                    if h and h != "*":
                        hosts.append(h)
        if hosts:
            # tld 제거 → 부분 매칭에 사용
            parts = []
            for h in hosts:
                # m.megamgccoffee.com → megamgccoffee
                segments = h.replace("www.", "").split(".")
                if len(segments) >= 2:
                    parts.append(segments[-2])  # 끝에서 두 번째 segment
            if parts:
                return list(set(parts))

    # 3. package reverse (가장 unique 한 segment)
    if package:
        segs = [s for s in package.split(".") if len(s) >= 4 and s not in ("com", "net", "org", "kr", "co", "ui", "app", "android")]
        if segs:
            # 가장 긴 segment 우선 (보통 앱 이름)
            segs.sort(key=len, reverse=True)
            return segs[:2]

    # 4. 빈 list
    return []

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
    global_domains: set[str] | None = None,
) -> tuple[bool, str, str | None]:
    """반환: (is_external, reason, matched_domain).

    A-2 (2026-05-06): 시그니처 확장 — `matched_domain` 추가 반환 (글로벌
    학습용). global_domains 도 받아 W4 (앱간 학습 도메인) 첫 검사.

    검사 순서:
      W4 — global_domains hit (가장 빠름, 학습 누적)
      W1 — URL https://domain 형식
      W1' — bare domain (queenssmile.com)
      W2 — EXTERNAL_HINT_KEYWORDS

    own_domain_parts 안의 부분 문자열을 가진 도메인은 internal 로 간주.

    빠르게 동작하도록 상위 max_views_to_scan view 만 검사.
    """
    if not views:
        return False, "", None

    global_domains = global_domains or set()

    for v in views[:max_views_to_scan]:
        text = (v.get("text") or "")
        desc = (v.get("content_desc") or "")
        rid = (v.get("resource_id") or "")
        combined = f"{text} {desc} {rid}"
        if not combined.strip():
            continue

        # W4 — global_domains hit (앱간 학습)
        c_lower = combined.lower()
        if global_domains:
            for gd in global_domains:
                if gd in c_lower:
                    return True, f"global learned: {gd}", gd

        # W1 — URL 형식 → domain 추출 후 own 비교
        for m in _URL_RE.finditer(combined):
            domain = m.group(1).lower()
            if not _is_own_domain(domain, own_domain_parts):
                return True, f"external URL: {domain}", domain

        # W1' — 베어 도메인 (URL 스킴 없이)
        for m in _BARE_DOMAIN_RE.finditer(combined):
            domain = m.group(1).lower()
            if not _is_own_domain(domain, own_domain_parts):
                return True, f"external bare-domain: {domain}", domain

        # W2 — Keyword hint
        for kw in EXTERNAL_HINT_KEYWORDS:
            if kw.lower() in c_lower:
                return True, f"external hint: {kw}", None

    return False, "", None


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
