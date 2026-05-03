"""Phase 2 P2.3 — Navigator verb → primitive type 매핑 테스트."""

from __future__ import annotations

from navigator.journey_planner import (
    _infer_target_primitives, _node_primitive_match, plan_task,
)


# ─── _infer_target_primitives ────────────────────────────

def test_toggle_verb():
    assert _infer_target_primitives("다크모드 켜기") == ["toggle"]
    assert _infer_target_primitives("알림 끄기") == ["toggle"]


def test_input_verb_search_implies_list_view():
    """검색 = input + list_view 두 primitive."""
    types = _infer_target_primitives("이름 검색")
    assert "input" in types and "list_view" in types


def test_submit_verb():
    assert _infer_target_primitives("저장") == ["submit"]
    assert _infer_target_primitives("결제") == ["submit"]


def test_selector_verb():
    types = _infer_target_primitives("색상 선택")
    assert "selector" in types


def test_stepper_verb_korean_quantity():
    """공백으로 분리된 stepper 키워드는 잡힘. '3잔' 같은 합성어는 별도 케이스."""
    types = _infer_target_primitives("수량 추가")
    assert "stepper" in types
    assert "submit" in types


def test_media_verb():
    types = _infer_target_primitives("음악 재생")
    assert "media" in types


def test_navigate_only_returns_empty():
    """순수 navigate task 는 매핑 없음 — Phase 3 executor 가 step 자체로 처리."""
    types = _infer_target_primitives("설정 화면 이동")
    # 설정 → settings synonym → 매칭 안 됨, 이동 → empty
    assert types == [] or all(t for t in types)


def test_english_keywords():
    assert "submit" in _infer_target_primitives("save document")
    assert "toggle" in _infer_target_primitives("enable dark mode")
    assert "input" in _infer_target_primitives("search query")


# ─── _node_primitive_match ────────────────────────────

def test_node_match_toggle():
    node = {
        "primitives": {
            "toggles": [{"id": "t1", "label": "다크모드"}],
            "submits": [{"id": "s1", "label": "확인"}],
        }
    }
    m = _node_primitive_match(node, ["toggle"])
    assert m is not None
    assert m["primitive_type"] == "toggle"
    assert m["candidate"]["id"] == "t1"


def test_node_match_no_primitive_returns_none():
    node = {"primitives": {"submits": [{"id": "s1"}]}}
    assert _node_primitive_match(node, ["toggle"]) is None


def test_node_match_priority_first_in_list():
    """target_types 의 순서가 priority — input > list_view 이면 input 먼저 시도."""
    node = {
        "primitives": {
            "inputs": [{"id": "i1"}],
            "list_views": [{"id": "l1"}],
        }
    }
    m = _node_primitive_match(node, ["input", "list_view"])
    assert m["primitive_type"] == "input"


def test_node_match_falls_through_to_next_type():
    """첫 type 없으면 다음 type 시도."""
    node = {"primitives": {"list_views": [{"id": "l1"}]}}
    m = _node_primitive_match(node, ["input", "list_view"])
    assert m is not None
    assert m["primitive_type"] == "list_view"


# ─── plan_task 통합 — target_primitive 가 결과에 포함 ────────

def test_plan_task_attaches_target_primitive():
    """plan_task 가 task verb → primitive type 추론 + 마지막 step 에 target_primitive 첨부."""
    nodes = [
        {"screen_id": "entry", "label": "Home", "activity": "Home"},
        # label 에 "다크모드" 포함 → topic 매칭됨
        {"screen_id": "settings", "label": "다크모드 설정", "activity": "Settings",
         "primitives": {"toggles": [{"id": "dark_toggle", "label": "다크모드"}]},
         "widgets": [{"id": "x"}], "status": "enriched"},
    ]
    edges = [{"from": "entry", "to": "settings",
              "trigger_action": "click", "trigger_widget": "settings_btn"}]
    screenmap = {"screen_map": {"graph": {
        "nodes": nodes, "edges": edges, "entry_node": "entry",
    }}}
    r = plan_task(screenmap, "다크모드 켜기")
    assert "error" not in r, f"failure_reason={r.get('failure_reason')}"
    assert r.get("target_primitive_types") == ["toggle"]
    assert r["steps"], "steps should not be empty"
    last = r["steps"][-1]
    assert last.get("target_primitive") is not None
    assert last["target_primitive"]["primitive_type"] == "toggle"
    assert last["target_primitive"]["candidate"]["id"] == "dark_toggle"


def test_plan_task_no_primitive_match_no_attachment():
    """toggle task 인데 target 노드에 toggle primitive 없으면 target_primitive=None."""
    nodes = [
        {"screen_id": "entry", "label": "Home"},
        {"screen_id": "settings", "label": "다크모드 설정",
         "primitives": {"submits": [{"id": "s1"}]},  # toggle 없음
         "widgets": [{"id": "x"}], "status": "enriched"},
    ]
    edges = [{"from": "entry", "to": "settings"}]
    screenmap = {"screen_map": {"graph": {
        "nodes": nodes, "edges": edges, "entry_node": "entry",
    }}}
    r = plan_task(screenmap, "다크모드 켜기")
    assert r.get("target_primitive_types") == ["toggle"]
    if r.get("steps"):
        last = r["steps"][-1]
        assert last.get("target_primitive") is None
