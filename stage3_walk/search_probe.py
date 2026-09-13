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

_SYSTEM_PROMPT = (
    "You help explore an Android app's search feature to build a screen-flow map.\n"
    "Given the app name, the search box hint, and words actually seen inside the app, propose search queries.\n"
    "Rules: 2 queries that will very likely return results — prefer real entity names seen in the app "
    "(store names, menu items, regions, product names) that fit the hint; 1 query that will certainly return "
    "no results (random letters like 'zzqx'). Same language as the app (Korean if the hint is Korean). "
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
        self.stats = {"fields": 0, "queries": 0, "results": 0, "details": 0, "failed": 0, "llm": 0}
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
            if t and _ENTITY_RE.match(t) and t not in _GENERIC_ENTITY and not t.isdigit():
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
    def generate_queries(self, state: dict, field: dict) -> list[dict]:
        hint = field.get("label") or field.get("text") or field.get("content_desc") or ""
        app = getattr(self.w, "app_label", "") or getattr(self.w, "package", "")
        seen = [t for t, _ in self.entities.most_common(40)]
        if self.client is not None:
            try:
                user = (f"App: {app}\nSearch box hint: {hint!r}\n"
                        f"Words seen in the app (most frequent first): {', '.join(seen) if seen else '(none yet)'}\n"
                        "Propose the 3 queries.")
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
        for q in queries:
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
        if new_id != from_id:
            t = {"from_screen": from_id, "to_screen": new_id}
            t.update(extra)
            self.w.transitions.append(t)
            fr = getattr(self.w, "frontier", None)
            if fr is not None:
                try:
                    fr.observe_transition(from_id, extra.get("event_str", ""), new_id)
                except Exception:
                    pass
        return new_id

    def _first_result_row(self, state: dict, field: dict) -> dict | None:
        """결과 화면에서 검색창 아래 첫 번째 클릭 가능한 텍스트 행."""
        widgets = extract_widget_table(state.get("views") or [], getattr(self.w, "package", "") or "")
        fb = parse_bounds(field.get("bounds")) or (0, 0, 0, 0)
        rows = [w for w in widgets if w.get("clickable") and (w.get("label") or "") and not w.get("editable")
                and w["bounds"][1] >= fb[3] and (w.get("label") or "") not in _GENERIC_ENTITY
                and not _FORM_HINT.search(w.get("label") or "")]
        rows.sort(key=lambda w: (w["bounds"][1], w["bounds"][0]))
        return rows[0] if rows else None
