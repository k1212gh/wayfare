"""검색 프로브 (#1) — 감지·검색어 생성·실행 흐름·지도 표시."""
from __future__ import annotations

import json

from stage3_walk import search_probe as spm
from stage3_walk.search_probe import SearchProbe
from stage6_screenmap.walk_transitions import _annotate_dynamic_nodes

APP = "co.app"


def _v(cls, bounds, text="", desc="", rid="", clickable=False):
    return {"class": f"android.widget.{cls}", "bounds": bounds, "text": text, "content_desc": desc,
            "resource_id": f"{APP}:id/{rid}" if rid else "", "clickable": clickable, "package": APP, "visible": True}


SEARCH_STATE = {"structure_str": "s-search", "activity": "co.app.Main", "views": [
    _v("View", "[70,457][1370,614]", clickable=True),
    _v("TextView", "[224,511][861,570]", text="매장이나 지역명을 검색해 주세요."),
    _v("TextView", "[126,1080][558,1147]", text="화성조암시장점"),
    _v("View", "[70,1000][1370,1400]", clickable=True),
]}
FORM_STATE = {"structure_str": "s-form", "activity": "co.app.Main", "views": [
    _v("EditText", "[100,300][1300,400]", text="이름"), _v("EditText", "[100,450][1300,550]", text="생년월일"),
    _v("EditText", "[100,600][1300,700]", text="휴대폰 번호"), _v("Button", "[100,800][1300,900]", text="등록", clickable=True),
]}
AUTH_STATE = {"structure_str": "s-auth", "activity": "co.app.Main", "views": [
    _v("EditText", "[100,300][1300,400]", text="비밀번호"),
]}


class FakeWalker:
    def __init__(self, captures):
        self.package = APP
        self.device_serial = "FAKE"
        self.states = []
        self.transitions = []
        self.hash_stats = {"new_screens": 0}
        self.taps = []
        self.backs = 0
        self._captures = list(captures)

        class H:
            known_fingerprints = {}

            def compute_fingerprint(self, views, act, shot):
                return json.dumps([v.get("text") for v in views])

            def find_match(self, fp):
                return next((k for k, v in self.known_fingerprints.items() if v == fp), None)

            def register(self, cid, fp):
                self.known_fingerprints[cid] = fp
        self.hasher = H()

    def _tap_view(self, view):
        self.taps.append(view["bounds"])

    def _press_back(self):
        self.backs += 1
        return True

    def wait_for_stable(self, timeout=2.0):
        pass

    def _capture_screen(self, idx):
        if not self._captures:
            return None
        st = dict(self._captures.pop(0))
        self.states.append(st)
        return st


class FakeClient:
    def query_json(self, system, user, max_tokens=300):
        assert "매장이나 지역명" in user
        return {"queries": [{"text": "화성조암시장점", "expect": "results"}, {"text": "강남", "expect": "results"}, {"text": "zzqx", "expect": "empty"}]}


def test_find_fields_distinguishes_search_from_forms():
    w = FakeWalker([])
    p = SearchProbe(w)
    p.client = None
    assert [f["label"] for f in p.find_fields(SEARCH_STATE)] == ["매장이나 지역명을 검색해 주세요."]
    assert p.find_fields(FORM_STATE) == []
    assert p.find_fields(AUTH_STATE) == []
    assert p.should_probe(SEARCH_STATE, "screen_001") and not p.should_probe(FORM_STATE, "screen_002")


def test_generate_queries_llm_then_fallbacks():
    w = FakeWalker([])
    p = SearchProbe(w)
    p.client = FakeClient()
    field = p.find_fields(SEARCH_STATE)[0]
    qs = p.generate_queries(SEARCH_STATE, field)
    assert [q["text"] for q in qs] == ["화성조암시장점", "강남", "zzqx"] and qs[2]["expect"] == "empty"
    p.client = None
    p.fixture = {"sample_inputs": {"search": "메뉴"}}
    p.observe(SEARCH_STATE)
    qs = p.generate_queries(SEARCH_STATE, field)
    assert qs[0]["text"] == "메뉴" and qs[-1]["expect"] == "empty" and len(qs) == 3


def test_run_records_type_submit_and_list_item_transitions(monkeypatch):
    results = {"structure_str": "s-results", "activity": "co.app.Main", "views": [
        _v("View", "[70,457][1370,614]", clickable=True), _v("TextView", "[224,511][861,570]", text="화성조암시장점"),
        _v("View", "[70,700][1370,900]", clickable=True), _v("TextView", "[126,720][558,790]", text="화성조암시장점 매장"),
    ]}
    detail = {"structure_str": "s-detail", "activity": "co.app.Main", "views": [_v("TextView", "[100,300][900,400]", text="매장 상세")]}
    empty = {"structure_str": "s-empty", "activity": "co.app.Main", "views": [_v("TextView", "[100,600][900,700]", text="검색 결과가 없습니다")]}
    # 검색어 2개(결과·상세) + 빈 결과 1개 → 캡처 순서: results, detail, results, detail, empty
    w = FakeWalker([results, detail, results, detail, empty])
    sent = []
    monkeypatch.setattr(spm, "extract_widget_table", spm.extract_widget_table)
    from stage3_walk import u2_helper
    monkeypatch.setattr(u2_helper, "send_text", lambda serial, text, clear=True: sent.append(text) or True)
    monkeypatch.setattr(u2_helper, "press_key", lambda serial, code: None)
    p = SearchProbe(w)
    p.client = FakeClient()
    used = p.run(SEARCH_STATE, "screen_001", 10)
    assert used > 0 and sent == ["화성조암시장점", "강남", "zzqx"]
    kinds = [(t["from_screen"], t.get("event_type"), t.get("input_value"), t.get("list_item")) for t in w.transitions]
    assert kinds[0][1] == "type_submit" and kinds[0][2] == "화성조암시장점"
    assert any(k[1] == "click" and k[3] is True for k in kinds)
    assert p.stats["queries"] == 3 and p.stats["details"] >= 1
    assert not p.should_probe(SEARCH_STATE, "screen_001")   # 같은 화면 재프로브 안 함


def test_annotate_dynamic_nodes_marks_search_results_and_item_action():
    graph = {"nodes": [{"screen_id": "page_s"}, {"screen_id": "page_r"}, {"screen_id": "page_d"}, {"screen_id": "page_e"}],
             "edges": [
                 {"from": "page_s", "to": "page_r", "trigger_action": "type_submit", "input_value": "강남", "expect": "results", "field": {"by": "text", "text": "검색"}},
                 {"from": "page_s", "to": "page_r", "trigger_action": "type_submit", "input_value": "아메리카노", "expect": "results"},
                 {"from": "page_s", "to": "page_e", "trigger_action": "type_submit", "input_value": "zzqx", "expect": "empty"},
                 {"from": "page_r", "to": "page_d", "trigger_action": "click", "list_item": True, "item_text": "화성조암시장점"},
             ]}
    _annotate_dynamic_nodes(graph)
    by = {n["screen_id"]: n for n in graph["nodes"]}
    assert by["page_r"]["dynamic"]["kind"] == "search_results" and by["page_r"]["dynamic"]["queries"] == ["강남", "아메리카노"]
    assert by["page_r"]["dynamic"]["query_field"]["text"] == "검색"
    assert by["page_r"]["dynamic"]["item_action"] == {"to": "page_d", "by": "text", "sample_items": ["화성조암시장점"]}
    assert by["page_e"]["dynamic"]["kind"] == "search_empty"
    assert "dynamic" not in by["page_s"]


def test_run_retries_with_feedback_when_results_are_empty(monkeypatch):
    empty = {"structure_str": "s-empty", "activity": "co.app.Main", "views": [
        _v("View", "[70,457][1370,614]", clickable=True), _v("TextView", "[224,511][861,570]", text="아메리카노"),
        _v("TextView", "[100,600][900,700]", text="검색 결과가 없습니다."), _v("TextView", "[100,700][900,780]", text="매장명, 주소로 검색해보세요."),
        _v("View", "[100,900][900,1000]", clickable=True), _v("TextView", "[120,920][800,980]", text="클립보드에 복사했어요"),
    ]}
    results = {"structure_str": "s-results", "activity": "co.app.Main", "views": [
        _v("View", "[70,457][1370,614]", clickable=True), _v("TextView", "[224,511][861,570]", text="강남"),
        _v("View", "[70,700][1370,900]", clickable=True), _v("TextView", "[126,720][558,790]", text="강남역점"),
    ]}
    detail = {"structure_str": "s-detail", "activity": "co.app.Main", "views": [_v("TextView", "[100,300][900,400]", text="매장 상세")]}
    # 1차: 아메리카노 → 빈 결과(재시도 유발), 자몽에이드 → 빈 결과, zzqx → 빈 결과, 재시도: 강남 → 결과+상세
    w = FakeWalker([empty, empty, empty, results, detail])
    calls = []

    class C:
        def query_json(self, system, user, max_tokens=300):
            calls.append(user)
            if "Previous attempt" in user:
                assert "매장명, 주소로" in user
                return {"queries": [{"text": "강남", "expect": "results"}, {"text": "서울", "expect": "results"}]}
            return {"queries": [{"text": "아메리카노", "expect": "results"}, {"text": "자몽에이드", "expect": "results"}, {"text": "zzqx", "expect": "empty"}]}
    from stage3_walk import u2_helper
    sent = []
    monkeypatch.setattr(u2_helper, "send_text", lambda serial, text, clear=True: sent.append(text) or True)
    monkeypatch.setattr(u2_helper, "press_key", lambda serial, code: None)
    p = SearchProbe(w)
    p.client = C()
    p.run(SEARCH_STATE, "screen_001", 0)
    assert sent[:4] == ["아메리카노", "자몽에이드", "zzqx", "강남"]      # 피드백 재시도 1회
    assert p.stats["retries"] == 1 and p.stats["empty"] == 3 and p.stats["details"] == 1
    empties = [t for t in w.transitions if t.get("outcome") == "empty"]
    assert len(empties) == 3        # 검색어마다 전이 기록 (Stage 6 에서 from→to 로 접힘)
    assert any(t.get("list_item") for t in w.transitions)
    # 빈 결과 화면에서는 행 탭을 하지 않는다 — 결과 행 탭은 1번뿐 (나머지는 검색창 탭)
    assert sum(1 for b in w.taps if b != "[70,457][1370,614]") == 1


def test_first_result_row_skips_filter_chips_and_picks_card():
    # WebView 결과: 카드는 clickable 이 아니고 텍스트 리프만 있다 (메가커피 실측)
    state = {"structure_str": "s-r", "activity": "co.app.Main", "views": [
        _v("View", "[70,457][1370,614]", clickable=True), _v("TextView", "[224,511][861,570]", text="커피"),
        _v("View", "[64,625][226,793]", clickable=True), _v("TextView", "[112,684][184,734]", text="주차"),        # 필터 칩
        _v("View", "[226,625][393,793]", clickable=True), _v("TextView", "[282,684][337,734]", text="DP"),
        _v("TextView", "[56,880][400,940]", text="검색결과 11건"),
        _v("TextView", "[126,1000][558,1067]", text="병점역점"),                                                 # 결과 카드 제목
        _v("TextView", "[126,1100][735,1160]", text="경기 화성시 떡전골로 96-4"),
    ]}
    w = FakeWalker([])
    p = SearchProbe(w)
    field = {"bounds": [70, 457, 1370, 614]}
    row = p._first_result_row(state, field)
    assert row is not None and row["label"] == "병점역점" and row["bounds"][1] == 1000
    # 헤더가 없으면 검색창 250px 아래부터 — 칩은 여전히 제외
    state2 = {"structure_str": "s-r2", "activity": "co.app.Main", "views": state["views"][:5] + [_v("TextView", "[126,1000][558,1067]", text="병점역점")]}
    assert p._first_result_row(state2, field)["label"] == "병점역점"
    # 결과가 없으면 None
    state3 = {"structure_str": "s-r3", "activity": "co.app.Main", "views": state["views"][:5]}
    assert p._first_result_row(state3, field) is None
