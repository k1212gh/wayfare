"""Phase B (2026-04-29) — task fixture loader + ScreenMap feasibility matcher.

원칙 (screenatlas_completeness_principle 메모리):
  completeness 는 task scope 안에서만 정의 — "이 ScreenMap 가 task N개의 step path
  를 만들 수 있는 노드를 갖고 있나" 가 binary, decidable. "모든 화면 발견" 은
  unbounded 라 평가 불가.

YAML 형식 (tests/fixtures/{app}.yaml):
    app: <name>
    tasks:
      - id: <id>
        goal: <설명>
        expected_screens:
          - description: <설명>
            match_any:
              activity_substr: [<substr>, ...]      # any-of 부분일치
              functional_category: [<cat>, ...]     # any-of 정확일치
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def load_fixture(app: str | None) -> dict | None:
    """tests/fixtures/{app}.yaml 로드. 없거나 PyYAML 없으면 None."""
    if not app:
        return None
    p = FIXTURE_DIR / f"{app}.yaml"
    if not p.exists():
        return None
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed — task fixture skipped")
        return None
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("fixture load failed for %s: %s", app, e)
        return None


def extract_keywords(fixture: dict | None) -> list[str]:
    """fixture 의 expected_screens 에서 view text/desc 매칭 가능한 한국어/영문
    키워드를 모두 추출 — score_action 의 task-keyword 보너스용.

    2026-05-03 (E): activity_substr 의 substr 가 view text 매칭 안 되니 (그건
    activity 분류용), 별도 task goal 의 description / id 에서 키워드 뽑기.
    예: 'browse_menu' goal '홈 → 메뉴 → 음료 리스트' → ['메뉴', '음료']
    """
    if not fixture:
        return []
    keywords = set()
    # task description / goal 에서 한국어 명사 후보 추출 (간단 휴리스틱)
    # 핵심 task 명사들을 직접 화이트리스트
    NOUN_HINTS = (
        # 메뉴/주문
        "메뉴", "음료", "커피", "주문", "장바구니", "옵션", "수량", "사이즈",
        "결제", "결제하기", "주문하기", "담기",
        # 매장
        "매장", "매장찾기", "근처", "지도", "검색",
        # 멤버십/쿠폰
        "멤버십", "마이페이지", "내정보", "마이", "쿠폰", "기프티콘", "선물",
        "적립", "포인트", "스탬프", "충전",
        # 회원/로그인
        "로그인", "회원가입", "본인인증",
        # 일반 시계 / 알람 (DeskClock fixture 호환)
        "알람", "타이머", "스톱워치",
        # C (2026-05-03): 메가커피 fixture 가 명시한 앱-특화 텍스트.
        # 하단 5탭 + 자주 쓰는 진입 라벨. NOUN_HINTS 매칭은 fixture.text_blob
        # 검사라 fixture 에 단어가 들어있어야만 keywords set 에 추가됨.
        "메가오더", "이벤트", "전체메뉴", "선물하기", "홈", "카운트다운",
    )
    for task in (fixture.get("tasks") or []):
        # text_blob — id/goal/description 에 더해 expected_screens.match_any
        # 의 text_substr 도 포함. text_substr 는 fixture 가 명시적으로 정의한
        # task 의 핵심 단어 (메가오더/스탬프/매장찾기 등) 라 walk-time 키워드
        # boost 의 화이트리스트로 자연스럽다.
        text_parts = [task.get("id", ""), task.get("goal", "")]
        for s in (task.get("expected_screens") or []):
            text_parts.append(s.get("description", ""))
            ma = s.get("match_any") or {}
            text_parts.extend(ma.get("text_substr", []) or [])
        text_blob = " ".join(text_parts)
        for n in NOUN_HINTS:
            if n in text_blob:
                keywords.add(n)
    return sorted(keywords)


def _node_matches(node: dict, match: dict) -> bool:
    """match_any 룰 — 시그널 하나라도 맞으면 True (OR 매칭).

    B (2026-05-03): text_substr 추가. webview-dominant 앱은 task 화면이
    manifest 에 별도 액티비티로 안 등록됨 (메가커피의 메뉴/매장/장바구니가
    모두 단일 WebActivity 안의 다른 URL/title). label / screen_purpose /
    description 검사로 노드 텍스트 매칭 가능하게 한다.
    """
    if not match:
        return False
    activity = (node.get("activity") or "").lower()
    category = (node.get("functional_category") or "").lower()

    for substr in match.get("activity_substr", []) or []:
        if substr and substr.lower() in activity:
            return True

    cats = [c.lower() for c in (match.get("functional_category", []) or [])]
    if category and category in cats:
        return True

    text_substrs = match.get("text_substr", []) or []
    if text_substrs:
        text_fields = " ".join(str(node.get(f) or "") for f in (
            "label", "screen_purpose", "description",
            "title_text", "page_text_top",
        )).lower()
        for substr in text_substrs:
            if substr and substr.lower() in text_fields:
                return True

    return False


def evaluate_task_coverage(
    nodes: list[dict],
    fixture: dict,
) -> dict:
    """각 task 의 expected_screens 가 ScreenMap 노드와 모두 매칭되는지 평가.

    Returns 형식:
      {
        "total": N, "feasible": K, "ratio": K/N,
        "tasks": [
          {"id": ..., "goal": ..., "feasible": bool,
           "missing": [<screen description>, ...]},
          ...
        ],
      }
    """
    tasks = (fixture or {}).get("tasks", []) or []
    if not tasks:
        return {"total": 0, "feasible": 0, "ratio": None, "tasks": []}

    results = []
    feasible_count = 0
    for task in tasks:
        expected = task.get("expected_screens", []) or []
        missing: list[str] = []
        for screen in expected:
            match_rule = screen.get("match_any", {}) or {}
            if not any(_node_matches(n, match_rule) for n in nodes):
                missing.append(screen.get("description", "?"))
        feasible = (not missing) and bool(expected)
        if feasible:
            feasible_count += 1
        results.append({
            "id": task.get("id"),
            "goal": task.get("goal"),
            "feasible": feasible,
            "missing": missing,
        })

    return {
        "total": len(tasks),
        "feasible": feasible_count,
        "ratio": round(feasible_count / len(tasks), 3),
        "tasks": results,
    }
