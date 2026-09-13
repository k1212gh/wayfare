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
import tempfile
import time
from collections import Counter
from pathlib import Path

from stage4_screens.widget_table import extract_widget_table, build_selector, parse_bounds

from . import u2_helper
from .view_tree_parser import parse_ui_xml

logger = logging.getLogger(__name__)

MAX_FIELDS_PER_WALK = int(os.environ.get("SEARCH_PROBE_MAX_FIELDS", "4"))
QUERIES_PER_FIELD = 3
# 검색창으로 인정하는 힌트/제목 — 이게 없으면 메모·요청사항·주소 같은 자유 입력란이다 (실측: 주문하기 화면의 "직접 입력")
_SEARCH_HINT = re.compile(r"((?<![가-힣])검색|(?<![가-힣])찾기|조회|search|find)", re.IGNORECASE)
_NOTE_HINT = re.compile(r"(요청사항|직접 입력|메모|comment|note|리뷰|후기|답변|문의)", re.IGNORECASE)
# 힌트가 비어 있어도 검색창으로 인정하는 resource_id (실측: 메가커피 매장 정보의 EditText#keyword 는 hint·desc 모두 빈 문자열)
_SEARCH_RID = re.compile(r"(keyword|search|query|srch|\bkw\b|find)", re.IGNORECASE)
# 탐색 중 검색 화면으로 끌고 갈 액션 — 텍스트/desc/rid 가 검색·돋보기면 미시도 시 우선 탭 (프로브 예산이 남아 있을 때만)
# 헤더의 뒤로 버튼 — 단일 액티비티 WebView 앱은 워커의 Back 가드(메인 액티비티에서 Back 금지)에 걸리므로 화면 안 버튼을 쓴다
_BACK_DESC = re.compile(r"(뒤로|이전|back|close|닫기)", re.IGNORECASE)
_SEEK_RE = re.compile(r"((?<![가-힣])검색|돋보기|\bsearch\b|매장 ?찾기|store ?finder)", re.IGNORECASE)
# 결제·주문 확정 화면에서는 어떤 입력도 하지 않는다
_CHECKOUT_RE = re.compile(r"(결제하기|결제 ?금액|총 ?결제|주문하기|카드 ?번호|인증번호|비밀번호|송금|이체)", re.IGNORECASE)
_FORM_HINT = re.compile(r"(비밀번호|password|인증번호|verification|전화번호|휴대폰|phone|이메일|email|아이디|생년|birth|이름|name|주민)", re.IGNORECASE)
_GENERIC_ENTITY = {"이전", "뒤로", "닫기", "취소", "확인", "새로고침", "추가", "더보기", "전체", "홈", "메뉴", "검색", "로그인", "전체보기",
                   "설정", "알림", "이벤트", "쿠폰", "선물", "주문", "결제", "장바구니", "마이페이지", "다음", "완료", "선택", "등록"}
_ENTITY_RE = re.compile(r"^[가-힣A-Za-z][가-힣A-Za-z0-9 ]{1,11}$")
_RESULT_HEADER_RE = re.compile(r"(검색\s*결과|결과\s*\d+\s*건|\d+\s*건$|results?)", re.IGNORECASE)
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
        self._back_exits_app = False      # KEYCODE_BACK 이 앱을 종료시킨 적이 있으면 True — 이후 raw Back 금지
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
        texts = self.screen_texts(state, k=25, package=getattr(self.w, "package", "") or "")
        if any(_CHECKOUT_RE.search(t) for t in texts):
            return []                       # 결제·주문 화면 — 입력 금지
        # 검색창 근거 (하나면 충분): (a) 힌트에 검색/찾기/조회 (b) 화면 제목·본문에 검색이 있고 필드가 하나뿐
        # (c) resource_id 가 keyword/search/query (d) 필드가 하나뿐이고 힌트가 없는데 화면 상단(30%)의 넓은(60%+) 입력란이며
        #     화면 어디에도 개인정보·메모 힌트가 없다 — WebView 검색창은 hint 를 a11y 로 안 내보내는 경우가 있다
        title_has_search = any(_SEARCH_HINT.search(t) for t in texts[:6])
        form_on_screen = any(_FORM_HINT.search(t) or _NOTE_HINT.search(t) for t in texts)
        views = state.get("views") or []
        screen_w = max((parse_bounds(v.get("bounds")) or (0, 0, 0, 0))[2] for v in views) if views else 1440
        screen_h = max((parse_bounds(v.get("bounds")) or (0, 0, 0, 0))[3] for v in views) if views else 3040
        out = []
        for w in fields:
            lab = w.get("label") or ""
            if _NOTE_HINT.search(lab):
                continue
            rid = w.get("resource_id") or ""
            b = w.get("bounds") or [0, 0, 0, 0]
            top_wide = (not lab and len(fields) == 1 and not form_on_screen
                        and b[1] < 0.3 * screen_h and (b[2] - b[0]) >= 0.6 * screen_w)
            if _SEARCH_HINT.search(lab) or (title_has_search and len(fields) == 1) or _SEARCH_RID.search(rid) or top_wide:
                out.append(w)
        return out

    # ── 탐색 유도: 검색 화면으로 가는 액션을 먼저 누르게 ───────────────────
    def seek_action(self, actions: list[dict], tried: set, blacklist) -> dict | None:
        """미시도 액션 중 검색·돋보기로 보이는 것 (점수 높은 순). 프로브 예산이 남았을 때만."""
        if self.stats["fields"] >= MAX_FIELDS_PER_WALK:
            return None
        hits = []
        for a in actions:
            desc = a.get("desc", "")
            if desc in tried or desc in blacklist:
                continue
            v = a.get("view") or a
            blob = f"{v.get('text') or ''} {v.get('content_desc') or ''} {v.get('resource_id') or ''}"
            if _SEEK_RE.search(blob):
                hits.append(a)
        if not hits:
            return None
        hits.sort(key=lambda x: -float(x.get("score", 0) or 0))
        return hits[0]

    def should_probe(self, state: dict, canonical_id: str) -> bool:
        if self.stats["fields"] >= MAX_FIELDS_PER_WALK:
            return False
        key = state.get("structure_str") or canonical_id
        if key in self.probed:
            return False
        return bool(self.find_fields(state))

    # ── 검색어 ───────────────────────────────────────────────────────────
    @staticmethod
    def screen_texts(state: dict, k: int = 15, package: str = "") -> list[str]:
        """화면 상단부터 보이는 텍스트 (제목·힌트·안내문) — 검색 대상 종류를 LLM 이 알 수 있게.
        상태바·런처(com.android.systemui 등) 텍스트는 뺀다 — 실측: 'Edge 패널', '배터리 18퍼센트' 가 프롬프트를 채웠다."""
        out = []
        views = sorted(state.get("views") or [], key=lambda v: (parse_bounds(v.get("bounds")) or (0, 0, 0, 0))[1::-1])
        for v in views:
            pkg = v.get("package") or ""
            if pkg and (pkg.startswith("com.android.systemui") or (package and pkg != package)):
                continue
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
        texts = self.screen_texts(state, package=getattr(self.w, "package", "") or "")
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
                # 0) 검색창이 아직 화면에 있는지 — 행 탭·Back 뒤에 다른 화면(퀵오더 시트, 이벤트)에 서 있으면 거기에
                #    검색어를 치면 안 된다 (358002fe 2차: 상세 뒤 Back 이 가드에 막혀 '난곡사거리점' 이 이벤트 상세로 감)
                if i > 1 and not self._return_to_search(field):
                    logger.info("[search] lost the search screen after %d queries — stop probing this field", i - 1)
                    self.stats["lost"] = self.stats.get("lost", 0) + 1
                    break
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
                        self._back_safely()
                        used += 1
                        self.w.wait_for_stable(timeout=2.0)
            except Exception as e:  # noqa: BLE001
                self.stats["failed"] += 1
                logger.warning("[search] probe step failed: %s", str(e)[:160])
        # 원래 화면으로: 결과 화면이면 Back 한 번
        self._back_safely()
        used += 1
        self.w.wait_for_stable(timeout=2.0)
        logger.info("[search] done on %s: %s", canonical_id, self.stats)
        return used

    # ── 화면 복귀 ────────────────────────────────────────────────────────
    def _live_views(self) -> list[dict]:
        """지금 화면의 뷰 트리 — 캡처·전이 기록 없이 확인만."""
        try:
            xml = u2_helper.dump_hierarchy(self.w.device_serial, timeout=6.0)
            if not xml or "<hierarchy" not in xml:
                return []
            tmp = Path(tempfile.gettempdir()) / f"wf_probe_{re.sub(r'[^A-Za-z0-9]', '_', str(self.w.device_serial))}.xml"
            tmp.write_text(xml, encoding="utf-8")
            return parse_ui_xml(tmp)
        except Exception:  # noqa: BLE001
            return []

    def _field_on_screen(self, field: dict, views: list[dict]) -> bool:
        """같은 resource_id 이거나 좌표가 절반 이상 겹치는 입력 위젯이 보이면 검색 화면이다."""
        if not views:
            return False
        fb = field.get("bounds") or [0, 0, 0, 0]
        for w in extract_widget_table(views, getattr(self.w, "package", "") or ""):
            if not w.get("editable"):
                continue
            if field.get("resource_id") and w.get("resource_id") == field.get("resource_id"):
                return True
            b = w.get("bounds") or [0, 0, 0, 0]
            ix = max(0, min(fb[2], b[2]) - max(fb[0], b[0]))
            iy = max(0, min(fb[3], b[3]) - max(fb[1], b[1]))
            area = max(1, (fb[2] - fb[0]) * (fb[3] - fb[1]))
            if ix * iy >= 0.5 * area:
                return True
        return False

    @staticmethod
    def _header_back_button(views: list[dict]) -> dict | None:
        """상단 12%·좌측 15% 안의 클릭 가능한 뷰, 또는 desc 가 뒤로/이전/닫기 인 뷰."""
        ys = [b for b in (parse_bounds(v.get("bounds")) for v in views) if b]
        if not ys:
            return None
        top, screen_h = min(b[1] for b in ys), max(b[3] for b in ys)
        screen_w = max(b[2] for b in ys)
        for v in views:
            b = parse_bounds(v.get("bounds"))
            if not b or not v.get("clickable"):
                continue
            desc = f"{v.get('content_desc') or ''} {v.get('text') or ''}"
            if _BACK_DESC.search(desc) and b[1] < top + 0.2 * screen_h:
                return v
            if b[1] < top + 0.12 * screen_h and b[2] <= 0.15 * screen_w and (b[2] - b[0]) < 0.12 * screen_w:
                return v
        return None

    def _back_safely(self) -> None:
        """뒤로가기: 워커 가드가 허용하면 그걸로, 아니면 헤더 뒤로 버튼 → KEYCODE_BACK (앱을 벗어나면 재실행)."""
        try:
            if self.w._press_back():
                return
        except Exception:  # noqa: BLE001
            pass
        btn = self._header_back_button(self._live_views())
        if btn is not None:
            self.w._tap_view(btn)
            time.sleep(0.8)
            return
        # 헤더 버튼도 없으면 KEYCODE_BACK — 단, 이 앱에서 한 번이라도 앱을 벗어났으면 다시 시도하지 않는다
        # (3차 실측: 퀵오더 흐름에서 매장 선택 뒤 시트가 닫혀 있어 Back 4번이 전부 앱 종료→재실행이었다)
        if self._back_exits_app:
            return
        serial = self.w.device_serial
        u2_helper.press_key(serial, 4)   # KEYCODE_BACK
        time.sleep(0.8)
        pkg = getattr(self.w, "package", "") or ""
        if pkg and u2_helper.current_package(serial) not in ("", pkg):
            self._back_exits_app = True
            logger.info("[search] back left the app — relaunching (raw Back disabled for this walk)")
            relaunch = getattr(self.w, "_relaunch_keep_tried", None)
            if relaunch:
                relaunch()

    def _return_to_search(self, field: dict, tries: int = 3) -> bool:
        """검색창이 보일 때까지 뒤로가기 (최대 tries 회). 못 돌아오면 False."""
        for _ in range(tries):
            views = self._live_views()
            if self._field_on_screen(field, views):
                return True
            if self._back_exits_app and self._header_back_button(views) is None:
                break                     # 돌아갈 수단이 없다 — 더 눌러봐야 앱만 종료된다
            self._back_safely()
            self.w.wait_for_stable(timeout=2.0)
        return self._field_on_screen(field, self._live_views())

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
        """결과 화면에서 첫 번째 결과 항목.

        2026-09-13 실측 보정:
          - 검색창 바로 아래 필터 칩("주차")이 먼저 잡혀 필터를 켜던 문제 → "검색결과 N건" 헤더 아래부터 본다
            (헤더가 없으면 검색창 아래 60px 밑부터; 좁고 짧은 글자는 칩으로 보고 제외).
          - WebView 결과 카드는 접근성 트리에서 clickable 이 아니다(텍스트 리프만 있음) → 클릭 플래그를 요구하지 않고
            항목처럼 생긴 텍스트(짧은 개체명, 칩보다 넓거나 높음)를 탭한다.
        """
        views = state.get("views") or []
        widgets = extract_widget_table(views, getattr(self.w, "package", "") or "")
        fb = parse_bounds(field.get("bounds")) or (0, 0, 0, 0)
        screen_w = max((parse_bounds(v.get("bounds")) or (0, 0, 0, 0))[2] for v in views) if views else 1440
        header_y = None
        for w in widgets:
            lab = w.get("label") or ""
            if _RESULT_HEADER_RE.search(lab) and w["bounds"][1] >= fb[3]:
                header_y = w["bounds"][3] if header_y is None else min(header_y, w["bounds"][3])
        start_y = header_y if header_y is not None else fb[3] + 60
        rows = []
        for w in widgets:
            lab = (w.get("label") or "").strip()
            if not lab or w.get("editable") or w["bounds"][1] < start_y:
                continue
            if lab in _GENERIC_ENTITY or _FORM_HINT.search(lab) or _SENTENCE_RE.search(lab) or _RESULT_HEADER_RE.search(lab):
                continue
            width = w["bounds"][2] - w["bounds"][0]
            if width < 0.25 * screen_w and len(lab) <= 4:   # 필터 칩/탭 (짧은 글자, 좁은 폭)
                continue
            rows.append(w)
        rows.sort(key=lambda w: (w["bounds"][1], w["bounds"][0]))
        return rows[0] if rows else None
