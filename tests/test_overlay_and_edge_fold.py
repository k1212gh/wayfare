"""#4 — 오버레이 분리(is_dialog 병합 금지·overlay 엣지) + 같은 from→to 관측 엣지 접기."""
from __future__ import annotations

from stage6_screenmap.semantic_merge import _is_mergeable, _rewrite_edges
from stage6_screenmap.walk_transitions import _inject_walk_transitions
from stage3_walk.view_tree_parser import detect_dialog


def test_dialog_never_merges_with_underlying_screen():
    a = {"screen_id": "page_a", "label": "주문내역", "activity": "Main", "screenshot_md5": "x1"}
    b = {"screen_id": "page_b", "label": "주문내역", "activity": "Main", "screenshot_md5": "x2", "is_dialog": True}
    assert _is_mergeable(a, b, [], 0.85) is False
    c = {"screen_id": "page_c", "label": "주문내역", "activity": "Main", "functional_category": "dialog"}
    assert _is_mergeable(a, c, [], 0.85) is False
    d = {"screen_id": "page_d", "label": "주문내역", "activity": "Main", "screenshot_md5": "x3"}
    assert _is_mergeable(a, d, [], 0.85) is True   # 같은 라벨·둘 다 일반 화면은 여전히 병합


def test_detect_dialog_recognizes_bottom_sheet_window_ids():
    assert detect_dialog([{"class": "android.view.View", "resource_id": "app:id/touch_outside"}])
    assert detect_dialog([{"class": "android.widget.FrameLayout", "resource_id": "app:id/design_bottom_sheet"}])
    assert not detect_dialog([{"class": "android.widget.FrameLayout", "resource_id": "app:id/content"}])


def test_rewrite_edges_folds_walk_duplicates_with_frequency_and_selectors():
    edges = [
        {"from": "page_x", "to": "page_y", "kind": "fragment_nav", "source": "walk", "trigger_action": "click",
         "trigger_widget": "View@[1,1][2,2]", "selector": {"by": "bounds", "bounds": [1, 1, 2, 2]}, "frequency": 1},
        {"from": "page_z", "to": "page_y", "kind": "fragment_nav", "source": "walk", "trigger_action": "click",
         "trigger_widget": "배너@[3,3][4,4]", "selector": {"by": "text", "text": "배너"}, "frequency": 2},
        {"from": "page_x", "to": "page_y", "kind": "contains", "source": "static"},
    ]
    out = _rewrite_edges(edges, "page_z", "page_x")   # z 가 x 로 흡수 → walk 엣지 2개가 같은 x→y
    walk = [e for e in out if e.get("source") == "walk"]
    assert len(walk) == 1
    assert walk[0]["frequency"] == 3
    assert walk[0]["selector"]["by"] == "text"          # 더 좋은 셀렉터가 대표로
    assert len(walk[0]["selectors"]) == 2
    assert any(e.get("kind") == "contains" for e in out)  # 정적 엣지는 그대로


def test_inject_walk_transitions_folds_same_pair_and_marks_overlay():
    graph = {"nodes": [
        {"screen_id": "page_a", "activity": "co.app.Main", "node_type": "fragment", "parent_activity_id": "act_m",
         "widgets": [{"id": "desc_1", "content_desc": "즐겨찾기", "label": "즐겨찾기", "bounds": [10, 10, 20, 20], "class": "ImageView"}]},
        {"screen_id": "page_b", "activity": "co.app.Main", "node_type": "fragment", "parent_activity_id": "act_m"},
        {"screen_id": "page_d", "activity": "co.app.Main", "node_type": "fragment", "parent_activity_id": "act_m", "is_dialog": True},
    ], "edges": []}
    cards = [{"screen_id": "page_a", "structure_str": "sa", "state_strs": ["s1"]},
             {"screen_id": "page_b", "structure_str": "sb", "state_strs": ["s2"]},
             {"screen_id": "page_d", "structure_str": "sd", "state_strs": ["s3"]}]
    walk_screens = [{"state_str": "s1", "structure_str": "sa"}, {"state_str": "s2", "structure_str": "sb"}, {"state_str": "s3", "structure_str": "sd"}]
    transitions = [
        {"from_screen": "s1", "to_screen": "s2", "event_type": "click", "event_str": "click ImageView@[10,10][20,20]"},
        {"from_screen": "s1", "to_screen": "s2", "event_type": "click", "event_str": "click 배너@[30,30][40,40]"},
        {"from_screen": "s1", "to_screen": "s2", "event_type": "click", "event_str": "click 배너@[30,30][40,40]"},
        {"from_screen": "s2", "to_screen": "s3", "event_type": "click", "event_str": "click 확인@[5,5][6,6]"},
    ]
    _inject_walk_transitions(graph, transitions, cards, walk_screens)
    ab = [e for e in graph["edges"] if e["from"] == "page_a" and e["to"] == "page_b"]
    assert len(ab) == 1 and ab[0]["frequency"] == 3
    assert ab[0]["selector"]["by"] == "content_desc" and ab[0]["selector"]["content_desc"] == "즐겨찾기"
    assert len(ab[0]["selectors"]) == 2
    bd = next(e for e in graph["edges"] if e["from"] == "page_b" and e["to"] == "page_d")
    assert bd["kind"] == "overlay"
