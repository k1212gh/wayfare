"""검색 프로브 — 검색창이 있는 화면을 만나면 LLM 이 만든 검색어를 넣고 결과·상세 화면까지 캡처한다.

왜 (2026-09-13, docs/agent_readiness_plan.md #1):
  검색 결과·목록은 입력값에 따라 내용이 바뀌는데, 기존 탐색은 (a) WebView 검색창(EditText 아님)을 못 찾고
  (b) fixture 고정값만 넣으며 (c) 결과 화면을 그냥 화면 하나로 저장했다 → 에이전트가 "무슨 검색어로 어떤 행을
  눌러야 상세로 가는지" 알 수 없었다. 메가커피 1차 탐색에서 자동 입력 0회.

동작 (탐색 루프에서 화면당 한 번):
  1. 감지: widget_table 로 입력 필드(EditText 또는 클릭 컨테이너+힌트)를 찾는다. 필드가 3개 이상이면 폼(회원가입 등) →
     프로브 대상 아님. 비밀번호/인증번호/전화번호 힌트가 있으면 인증 폼 → 제외.
  2. 검색어: 텍스트 LLM 에 앱 이름·힌트·지금까지 앱에서 본 개체명(메뉴·매장·이벤트 이름)을 주고 "결과 있을 검색어 2개 +
     결과 없을 검색어 1개" JSON 을 받는다. LLM 이 없으면 fixture sample_inputs → 본 개체명 → 건너뜀.
  3. 실행: 필드 탭 → 유니코드 입력(u2 send_keys) → Enter → 결과 캡처(전이 type_submit, input_value) → 결과 첫 행 탭 →
     상세 캡처(전이 click, list_item=True) → Back. 검색어마다 반복. 마지막에 Back 으로 원래 화면 복귀 시도.
  4. 예산: 화면 4개 × 검색어 3개. 인증/결제 화면 가드는 워커의 기존 가드가 담당.

지도 쪽(walk_transitions)은 type_submit 전이를 받아 결과 노드에 dynamic{kind: search_results, query_field, queries}
와 행→상세 액션을 기록한다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter

from stage4_screens.widget_table import extract_widget_table, build_selector, parse_bounds

logger = logging.getLogger(__name__)

MAX_FIELDS_PER_WALK = int(os.environ.get("SEARCH_PROBE_MAX_FIELDS", "4"))
QUERIES_PER_FIELD = 3
_FORM_HINT = re.compile(r"(비밀번호|password|인증번호|verification|전화번호|휴대폰|phone|이메일|email|아이디|생년|birth|이름|name|주민)", re.IGNORECASE)
_GENERIC_ENTITY = {"이전", "뒤로", "닫기", "취소", "확인", "새로고침", "추가", "더보기", "전체", "홈", "메뉴", "검색", "로그인", "전체보기",
                   "설정", "알림", "이벤트", "쿠폰", "선물", "주문", "결제", "장바구니", "마이페이지", "다음", "완료", "선택", "등록"}
_ENTITY_RE = re.compile(r"^[가-힣A-Za-z][가-힣A-Za-z0-9 ]{1,11}$")
_SENTENCE_RE = re.compile(r"(했어요|습니다|세요|입니다|합니다|해요|하기)\.?$")   # 토스트/안내문은 개체명이 아니다
# 결과 없음 화면 판정 — 이 문구가 보이면 그 검색어는 실패, 안내문을 LLM 에 피드백해 한 번 재시도
_EMPTY_RE = re.compile(r"(결과가 없|검색 결과 없|일치하는 .*없|찾을 수 없|없습니다\.?$|no results|nothing found|not found)", re.IGNORECASE)

_SYSTEM_PROMPT = (
    "You help explore an Android app's search feature to build a screen-flow map.\n"
    "Given the app name, the search box hint, and words actually seen inside the app, propose search queries.\n"
    "Rules: first decide WHAT this box searches from the screen title, the hint and the surrounding texts "
    "(e.g. a store finder wants store names or regions like 강남/서울, a menu search wants menu names). "
    "Then give 2 queries that will very likely return results — prefer real entity names seen in the app of that kind, "
    "or a common region/category word if none were seen; and 1 query that will certainly return no results "
    "(random letters like 'zzqx'). Same language as the app (Korean if the screen is Korean). "
    "Each query at most 12 characters. Never use personal data.\n"
    'Respond with strict JSON only: {"queries": [{"text": "...", "expect": "results"}, {"text": "...", "expect": "results"}, '
    '{"text": "...", "expect": "empty"}]}'
)


class SearchProbe:
    def __init__(self, walker, fixture: dict | None = None):
        self.w = walker
        self.fixture = fixture or {}
        self.probed: set[str] = set()
        self.entities: Counter = Counter()
        self.stats = {"fields": 0, "queries": 0, "results": 0, "details": 0, "failed": 0, "llm": 0, "empty": 0, "retries": 0}
        self._last_transition: dict | None = None
        self.client = None
        try:
            from stage5_annotate.llm_client import is_llm_configured, create_client
            if is_llm_configured()[0]:
                self.client = create_client(max_retries=1)
        except Exception as e:  # noqa: BLE001
            logger.info("[search] LLM unavailable (%s) — fixture/entity fallback", e)

    # ── 관찰: 앱에서 본 개체명 수집 ─────────────────────────────────────────
    def observe(self, state: dict) -> None:
        for v in state.get("views") or []:
            if (v.get("package") or "") and (v.get("package") or "") != getattr(self.w, "package", v.get("package")):
                continue
            t = (v.get("text") or "").strip()
            if t and _ENTITY_RE.match(t) and t not in _GENERIC_ENTITY and not t.isdigit() and not _SENTENCE_RE.search(t):
                self.entities[t] += 1

    # ── 감지 ─────────────────────────────────────────────────────────────
    def find_fields(self, state: dict) -> list[dict]:
        widgets = extract_widget_table(state.get("views") or [], getattr(self.w, "package", "") or "")
        fields = [w for w in widgets if w.get("editable")]
        if not fields or len(fields) >= 3:
            return []                       # 폼(회원가입·주소 입력) 은 대상 아님
        if any(_FORM_HINT.search(w.get("label") or "") for w in fields):
            return []                       # 인증/개인정보 폼
        return fields

    def should_probe(self, state: dict, canonical_id: str) -> bool:
        if self.stats["fields"] >= MAX_FIELDS_PER_WALK:
            return False
        key = state.get("structure_str") or canonical_id
        if key in self.probed:
            return False
        return bool(self.find_fields(state))

    # ── 검색어 ───────────────────────────────────────────────────────────
    @staticmethod
    def screen_texts(state: dict, k: int = 15) -> list[str]:
        """화면 상단부터 보이는 텍스트 (제목·힌트·안내문) — 검색 대상 종류를 LLM 이 알 수 있게."""
        out = []
        for v in state.get("views") or []:
            t = (v.get("text") or v.get("content_desc") or "").strip()
            if t and 1 < len(t) <= 40 and t not in out and "알림" not in t:
                out.append(t)
            if len(out) >= k:
                break
        return out

    def generate_queries(self, state: dict, field: dict, feedback: str = "") -> list[dict]:
        hint = field.get("label") or field.get("text") or field.get("content_desc") or ""
        app = getattr(self.w, "app_label", "") or getattr(self.w, "package", "")
        seen = [t for t, _ in self.entities.most_common(40)]
        texts = self.screen_texts(state)
        if self.client is not None:
            try:
                user = (f"App: {app}\nSearch box hint: {hint or '(no hint)'!r}\n"
                        f"Texts on this screen (top to bottom): {', '.join(texts) if texts else '(none)'}\n"
                        f"Words seen in the app (most frequent first): {', '.join(seen) if seen else '(none yet)'}\n"
                        + (f"Previous attempt: {feedback}\n" if feedback else "")
                        + "Propose the 3 queries.")
                resp = self.client.query_json(_SYSTEM_PROMPT, user, max_tokens=300)
                qs = resp.get("queries") if isinstance(resp, dict) else resp
                out = []
                for q in qs or []:
                    if isinstance(q, dict) and str(q.get("text") or "").strip():
                        out.append({"text": str(q["text"]).strip()[:12], "expect": "empty" if q.get("expect") == "empty" else "results"})
                if out:
                    self.stats["llm"] += 1
                    return out[:QUERIES_PER_FIELD]
            except Exception as e:  # noqa: BLE001
                logger.warning("[search] query generation failed: %s", str(e)[:120])
        # fixture → 개체명 → 없음
        samples = (self.fixture.get("sample_inputs") or {}) if isinstance(self.fixture, dict) else {}
        out = []
        for k, v in samples.items():
            if k.lower() in hint.lower() or k.lower() in ("search", "검색"):
                out.append({"text": str(v)[:12], "expect": "results"})
                break
        for t in seen[:2]:
            if len(out) >= 2:
                break
            out.append({"text": t, "expect": "results"})
        if out:
            out.append({"text": "zzqx", "expect": "empty"})
        return out[:QUERIES_PER_FIELD]

    # ── 실행 ─────────────────────────────────────────────────────────────
    def run(self, state: dict, canonical_id: str, event_count: int) -> int:
        """프로브 실행. 사용한 이벤트 수를 돌려준다 (워커가 event_count 에 더함)."""
        from . import u2_helper
        fields = self.find_fields(state)
        if not fields:
            return 0
        self.probed.add(state.get("structure_str") or canonical_id)
        self.stats["fields"] += 1
        field = fields[0]
        queries = self.generate_queries(state, field)
        if not queries:
            logger.info("[search] no queries for field %r — skipped", field.get("label"))
            return 0
        logger.info("[search] field %r on %s → queries %s", (field.get("label") or "")[:30], canonical_id,
                    [q["text"] for q in queries])
        used = 0
        serial = self.w.device_serial
        field_sel = build_selector(f"{field.get('label') or ''}@[{field['bounds'][0]},{field['bounds'][1]}][{field['bounds'][2]},{field['bounds'][3]}]", [field])
        retried = False
        i = 0
        while i < len(queries):
            q = queries[i]
            i += 1
            try:
                # 1) 필드 탭 → 포커스
                self.w._tap_view({"bounds": f"[{field['bounds'][0]},{field['bounds'][1]}][{field['bounds'][2]},{field['bounds'][3]}]"})
                used += 1
                time.sleep(0.5)
                # 2) 입력 + 제출
                if not u2_helper.send_text(serial, q["text"], clear=True):
                    self.stats["failed"] += 1
                    logger.warning("[search] text input failed for %r", q["text"])
                    continue
                time.sleep(0.3)
                u2_helper.press_key(serial, 66)   # KEYCODE_ENTER
                used += 1
                self.stats["queries"] += 1
                self.w.wait_for_stable(timeout=3.0)
                # 3) 결과 캡처 + 전이
                result_id = self._capture_and_record(canonical_id, event_count + used, {
                    "event_type": "type_submit",
                    "event_str": f"type_submit \"{q['text']}\"@[{field['bounds'][0]},{field['bounds'][1]}][{field['bounds'][2]},{field['bounds'][3]}]",
                    "input_value": q["text"], "expect": q["expect"], "field": field_sel,
                })
                if not result_id:
                    continue
                self.stats["results"] += 1
                # 3b) 결과 없음 화면이면 기록하고, 결과를 기대했던 검색어였으면 안내문을 피드백해 한 번 재시도
                empty_msg = self._empty_message(self.w.states[-1] if self.w.states else {})
                if empty_msg:
                    self.stats["empty"] += 1
                    if self._last_transition is not None:
                        self._last_transition["outcome"] = "empty"
                    if q["expect"] == "results" and not retried:
                        retried = True
                        self.stats["retries"] += 1
                        fb = f"'{q['text']}' returned no results; the empty-state text says: {empty_msg!r}. Pick a different kind of query."
                        more = [x for x in self.generate_queries(state, field, feedback=fb) if x["expect"] == "results" and x["text"] not in {y["text"] for y in queries}]
                        if more:
                            logger.info("[search] retry with %s", [x["text"] for x in more[:2]])
                            queries.extend(more[:2])
                    continue
                # 4) 결과 있는 검색이면 첫 행 → 상세
                if q["expect"] == "results":
                    row = self._first_result_row(self.w.states[-1] if self.w.states else {}, field)
                    if row:
                        self.w._tap_view({"bounds": f"[{row['bounds'][0]},{row['bounds'][1]}][{row['bounds'][2]},{row['bounds'][3]}]"})
                        used += 1
                        self.w.wait_for_stable(timeout=2.5)
                        detail_id = self._capture_and_record(result_id, event_count + used, {
                            "event_type": "click",
                            "event_str": f"click {row.get('label') or row.get('class')}@[{row['bounds'][0]},{row['bounds'][1]}][{row['bounds'][2]},{row['bounds'][3]}]",
                            "list_item": True, "item_text": row.get("label") or "",
                        })
                        if detail_id:
                            self.stats["details"] += 1
                        self.w._press_back()
                        used += 1
                        self.w.wait_for_stable(timeout=2.0)
            except Exception as e:  # noqa: BLE001
                self.stats["failed"] += 1
                logger.warning("[search] probe step failed: %s", str(e)[:160])
        # 원래 화면으로: 결과 화면이면 Back 한 번
        self.w._press_back()
        used += 1
        self.w.wait_for_stable(timeout=2.0)
        logger.info("[search] done on %s: %s", canonical_id, self.stats)
        return used

    def _capture_and_record(self, from_id: str, idx: int, extra: dict) -> str | None:
        new_screen = self.w._capture_screen(idx)
        if not new_screen:
            return None
        fp = self.w.hasher.compute_fingerprint(new_screen.get("views", []), new_screen.get("activity", ""), new_screen.get("screenshot_path", ""))
        match = self.w.hasher.find_match(fp)
        if match:
            new_id = match
        else:
            new_id = f"screen_{len(self.w.hasher.known_fingerprints):03d}"
            self.w.hasher.register(new_id, fp)
            self.w.hash_stats["new_screens"] = self.w.hash_stats.get("new_screens", 0) + 1
            logger.info("  -> NEW screen (search): %s", new_id)
        new_screen["canonical_id"] = new_id
        new_screen["state_str"] = new_id
        self.observe(new_screen)
        self._last_transition = None
        if new_id != from_id:
            t = {"from_screen": from_id, "to_screen": new_id}
            t.update(extra)
            self.w.transitions.append(t)
            self._last_transition = t
            fr = getattr(self.w, "frontier", None)
            if fr is not None:
                try:
                    fr.observe_transition(from_id, extra.get("event_str", ""), new_id)
                except Exception:
                    pass
        return new_id

    @staticmethod
    def _empty_message(state: dict) -> str:
        """결과 없음 문구 + 바로 다음 안내문("매장명, 주소로 검색해보세요") 을 합쳐 돌려준다."""
        texts = [(v.get("text") or "").strip() for v in state.get("views") or []]
        texts = [t for t in texts if t]
        for i, t in enumerate(texts):
            if _EMPTY_RE.search(t):
                nxt = texts[i + 1] if i + 1 < len(texts) and len(texts[i + 1]) <= 60 else ""
                return (t + (" " + nxt if nxt else ""))[:120]
        return ""

    def _first_result_row(self, state: dict, field: dict) -> dict | None:
        """결과 화면에서 검색창 아래 첫 번째 결과 행(카드).

        2026-09-13 실측 보정: 검색창 바로 아래의 필터 칩("주차", "DP")이 먼저 잡혀 필터를 켜 버렸다 →
        행은 화면 폭의 절반 이상·높이 100px 이상인 클릭 컨테이너만 (칩·탭·버튼 제외).
        """
        views = state.get("views") or []
        widgets = extract_widget_table(views, getattr(self.w, "package", "") or "")
        fb = parse_bounds(field.get("bounds")) or (0, 0, 0, 0)
        screen_w = max((parse_bounds(v.get("bounds")) or (0, 0, 0, 0))[2] for v in views) if views else 1440
        min_w, min_h = 0.5 * screen_w, 100
        rows = [w for w in widgets if w.get("clickable") and (w.get("label") or "") and not w.get("editable")
                and w["bounds"][1] >= fb[3]
                and (w["bounds"][2] - w["bounds"][0]) >= min_w and (w["bounds"][3] - w["bounds"][1]) >= min_h
                and (w.get("label") or "") not in _GENERIC_ENTITY
                and not _FORM_HINT.search(w.get("label") or "")
                and not _SENTENCE_RE.search(w.get("label") or "")]
        rows.sort(key=lambda w: (w["bounds"][1], w["bounds"][0]))
        return rows[0] if rows else None
