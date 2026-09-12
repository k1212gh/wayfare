"""TapWalker + Frontier / TarpitEscaper 결합 테스트 (기기 없이 mock).

시나리오 그래프: A -go_b-> B -go_c-> C. C 에 미시도 액션.
현재 A 에서 모든 액션을 시도한 상태 → frontier 가 C 까지 경로 재현.
"""

from __future__ import annotations

from collections import defaultdict
from unittest.mock import MagicMock

from stage3_walk.frontier import Frontier
from stage3_walk.view_tree_readers.xml_view_tree import XMLViewTreeReader


def _view(text, clickable=True):
    return {"resource_id": "", "text": text, "content_desc": "", "class": "android.widget.Button",
            "parent_class": "ViewGroup", "clickable": clickable, "scrollable": False,
            "visible": True, "bounds": "[0,0][100,50]"}


def _action(desc, view=None):
    return {"desc": desc, "action": "click", "score": 5.0, "view": view or _view(desc)}


SCREEN_ACTIONS = {
    "A": [_action("click go_b")],
    "B": [_action("click go_c"), _action("click back_a")],
    "C": [_action("click c_untried")],
    "D": [],
}


def _walker(frontier=True, capture_sequence=None, escaper=None):
    from stage3_walk.tap_walker import TapWalker
    w = TapWalker.__new__(TapWalker)
    w.device_serial = "fake"
    w.extractor = XMLViewTreeReader()
    w.tried_actions = defaultdict(set)
    w.trap_stats = defaultdict(int)
    w.action_history = []
    w.transitions = []
    w.states = []
    w.external_blacklist = set()
    w._learned_action_descs = set()
    w._permanent_blocked_canonicals = set()
    w._allow_external = True
    w.action_target_diversity = defaultdict(set)
    w.hash_stats = {"new_screens": 0}
    w.frontier = Frontier() if frontier else None
    w.tarpit_escaper = escaper
    w.executed = []
    w._execute_action = lambda a, s: w.executed.append(a["desc"])
    w.wait_for_stable = lambda timeout=2.0: None
    w._get_scored_actions = lambda state: list(SCREEN_ACTIONS.get(state["canonical_id"], []))
    seq = list(capture_sequence or [])
    w._capture_screen = lambda idx: ({"canonical_id": seq.pop(0), "views": [], "activity": "X"} if seq else None)
    w._canonicalize_screen = lambda screen: screen["canonical_id"]
    return w


def _seed_graph(w):
    fr = w.frontier
    for c, acts in SCREEN_ACTIONS.items():
        fr.observe_actions(c, acts)
    fr.observe_transition("A", "click go_b", "B")
    fr.observe_transition("B", "click go_c", "C")
    fr.observe_transition("B", "click back_a", "A")
    w.tried_actions["A"] = {"click go_b"}
    w.tried_actions["B"] = {"click go_c", "click back_a"}


def test_navigation_reaches_target_and_counts_events():
    w = _walker(capture_sequence=["B", "C"])
    _seed_graph(w)
    state = {"canonical_id": "A", "views": []}
    out = w._try_frontier_navigation(state, "A", event_count=10)
    assert out == 12
    assert w.executed == ["click go_b", "click go_c"]
    assert w.frontier.stats["nav_success"] == 1
    assert w.frontier.stats["nav_steps"] == 2
    assert w.trap_stats["frontier_nav"] == 1
    # 재현 중 전이도 기록됨
    assert [(t["from_screen"], t["to_screen"]) for t in w.transitions] == [("A", "B"), ("B", "C")]


def test_navigation_diverges_marks_failure_but_returns_events():
    w = _walker(capture_sequence=["D"])   # go_b 눌렀는데 D 로 감
    _seed_graph(w)
    out = w._try_frontier_navigation({"canonical_id": "A", "views": []}, "A", event_count=0)
    assert out == 1
    assert w.frontier.stats["nav_failed"] == 1
    assert w.frontier.target_failures["C"] == 1
    # 새로 관측된 엣지 A -go_b-> D 로 갱신
    assert w.frontier.edges["A"]["click go_b"] == "D"


def test_navigation_none_when_disabled_or_no_target():
    w = _walker(frontier=False)
    assert w._try_frontier_navigation({"canonical_id": "A"}, "A", 0) is None
    w2 = _walker()
    # 그래프 비어있음 → 목표 없음
    assert w2._try_frontier_navigation({"canonical_id": "A", "views": []}, "A", 0) is None
    assert w2.frontier.stats["no_target"] == 1


def test_navigation_first_action_missing_returns_none_and_fails_target():
    w = _walker(capture_sequence=["B", "C"])
    _seed_graph(w)
    # 현재 캡처에 go_b 가 안 보이는 상황
    w._get_scored_actions = lambda state: [] if state["canonical_id"] == "A" else list(SCREEN_ACTIONS[state["canonical_id"]])
    out = w._try_frontier_navigation({"canonical_id": "A", "views": []}, "A", 5)
    assert out is None
    assert w.executed == []
    assert w.frontier.target_failures["C"] == 1


def test_tarpit_escape_taps_first_untried_pick():
    esc = MagicMock()
    v_tried, v_new = _view("go_b"), _view("menu")
    esc.suggest.return_value = [{"index": 0, "view": v_tried, "reason": "x"},
                                {"index": 1, "view": v_new, "reason": "메뉴"}]
    w = _walker(escaper=esc)
    w.tried_actions["A"] = {w.extractor.get_action_desc(v_tried)}
    state = {"canonical_id": "A", "views": [v_tried, v_new], "activity": "Main"}
    assert w._try_tarpit_escape(state, "A") is True
    assert w.executed == [w.extractor.get_action_desc(v_new)]
    assert w.trap_stats["tarpit_escape"] == 1
    assert w.extractor.get_action_desc(v_new) in w.tried_actions["A"]


def test_tarpit_escape_false_when_disabled_or_empty():
    assert _walker(escaper=None)._try_tarpit_escape({"canonical_id": "A", "views": [_view("x")]}, "A") is False
    esc = MagicMock()
    esc.suggest.return_value = []
    w = _walker(escaper=esc)
    assert w._try_tarpit_escape({"canonical_id": "A", "views": [_view("x")]}, "A") is False
    assert w._try_tarpit_escape({"canonical_id": "A", "views": []}, "A") is False


# ─── v3: reposition (목표 없을 때 Back / 재실행 후 재탐색) ───────────

def _walker_v3(capture_sequence, back_ok=True):
    w = _walker(capture_sequence=capture_sequence)
    w.package, w.main_activity = "pkg", "Main"
    w.backs, w.relaunches = 0, 0

    def _back():
        w.backs += 1
        return back_ok
    w._press_back = _back
    w._relaunch_keep_tried = lambda: setattr(w, "relaunches", w.relaunches + 1)
    return w


def test_reposition_back_then_navigate_to_target():
    # leaf L (나가는 엣지 없음) → Back 으로 A 도착 → A 에서 C 가 목표 → 경로 재현
    w = _walker_v3(capture_sequence=["A", "B", "C"])
    _seed_graph(w)
    out = w._try_frontier_navigation({"canonical_id": "L", "views": []}, "L", 0, reposition=True)
    assert out == 3                       # back 1 + 경로 2 스텝
    assert w.backs == 1 and w.relaunches == 0
    assert w.frontier.stats["reposition_back"] == 1
    assert w.frontier.stats["nav_success"] == 1
    assert w.executed == ["click go_b", "click go_c"]


def test_reposition_falls_back_to_relaunch_when_back_unsafe():
    w = _walker_v3(capture_sequence=["A", "B", "C"], back_ok=False)
    _seed_graph(w)
    out = w._try_frontier_navigation({"canonical_id": "L", "views": []}, "L", 0, reposition=True)
    assert out == 3
    assert w.backs == 1 and w.relaunches == 1
    assert w.frontier.stats.get("reposition_relaunch") == 1
    assert w.frontier.stats["nav_success"] == 1


def test_reposition_no_target_anywhere_returns_moved_events():
    # 그래프에 미시도가 하나도 없으면 back+relaunch 둘 다 해도 목표 없음 → moved 이므로 int 반환
    w = _walker_v3(capture_sequence=["A", "A"])
    _seed_graph(w)
    w.tried_actions["C"] = {"click c_untried"}
    w.tried_actions["D"] = set()
    # D 는 A -go_d-> D 엣지가 없어서 도달 불가 → 목표 없음
    out = w._try_frontier_navigation({"canonical_id": "L", "views": []}, "L", 0, reposition=True)
    assert out == 2
    assert w.backs == 1 and w.relaunches == 1
    assert w.executed == []


def test_no_reposition_when_flag_off():
    w = _walker_v3(capture_sequence=["A"])
    _seed_graph(w)
    assert w._try_frontier_navigation({"canonical_id": "L", "views": []}, "L", 0) is None
    assert w.backs == 0
