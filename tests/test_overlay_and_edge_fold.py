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
    # 화면 절반짜리 시트는 다이얼로그
    half = [{"class": "android.widget.FrameLayout", "bounds": "[0,0][1440,3040]"},
            {"class": "android.view.View", "resource_id": "app:id/touch_outside", "bounds": "[0,0][1440,3040]"},
            {"class": "android.widget.FrameLayout", "resource_id": "app:id/design_bottom_sheet", "bounds": "[0,1500][1440,3040]"}]
    assert detect_dialog(half)
    # 화면 90% 이상을 덮는 시트는 페이지 (메가커피 매장 정보/검색/상세 흐름) — 워커가 닫으려 들지 않게
    full = [{"class": "android.widget.FrameLayout", "bounds": "[0,0][1440,3040]"},
            {"class": "android.view.View", "resource_id": "app:id/touch_outside", "bounds": "[0,100][1440,3035]"},
            {"class": "android.widget.FrameLayout", "resource_id": "app:id/design_bottom_sheet", "bounds": "[0,100][1440,3035]"}]
    assert not detect_dialog(full)
    # 전체 시트라도 그 안에 AlertDialog 클래스가 있으면 다이얼로그
    assert detect_dialog(full + [{"class": "android.app.AlertDialog", "bounds": "[100,1000][1340,1600]"}])
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


def test_search_screen_results_and_empty_never_merge_and_queries_union():
    """#1 — 검색 화면·결과·빈 결과는 dynamic.kind/type_submit 로 구분해 병합하지 않고, 같은 종류끼리는 검색어를 합친다."""
    from stage6_screenmap.semantic_merge import _merge_dynamic
    search = {"screen_id": "page_s", "label": "매장 검색", "activity": "Main", "screenshot_md5": "m1"}
    results = {"screen_id": "page_r", "label": "매장 검색", "activity": "Main", "screenshot_md5": "m1",
               "dynamic": {"kind": "search_results", "queries": ["화성"]}}
    empty = {"screen_id": "page_e", "label": "매장 검색", "activity": "Main", "screenshot_md5": "m1",
             "dynamic": {"kind": "search_empty", "queries": ["zzqx"]}}
    edges = [{"from": "page_s", "to": "page_r", "trigger_action": "type_submit", "input_value": "화성"},
             {"from": "page_s", "to": "page_e", "trigger_action": "type_submit", "input_value": "zzqx"}]
    assert _is_mergeable(search, results, edges, 0.85) is False     # md5 가 같아도 검색 화면 ↔ 결과
    assert _is_mergeable(results, empty, edges, 0.85) is False      # 결과 ↔ 빈 결과
    results2 = {"screen_id": "page_r2", "label": "매장 검색", "activity": "Main", "screenshot_md5": "m1",
                "dynamic": {"kind": "search_results", "queries": ["화성조암시장점"], "item_action": {"to": "page_d", "by": "text", "sample_items": ["화성조암시장점"]}}}
    assert _is_mergeable(results, results2, edges, 0.85) is True    # 같은 종류(결과 페이지, 검색어만 다름) 는 병합
    _merge_dynamic(results, results2)
    assert results["dynamic"]["queries"] == ["화성", "화성조암시장점"]
    assert results["dynamic"]["item_action"]["sample_items"] == ["화성조암시장점"]


def test_cluster_splits_type_submit_targets_by_outcome():
    """Stage 4 — 구조 해시가 같아도 결과/빈 결과 상태는 다른 페이지가 된다."""
    from stage4_screens.screen_clusterer import cluster_screens_to_pages
    v = [{"class": "TextView", "bounds": "[0,100][1440,200]", "text": "매장 검색", "package": "co.app"}]
    states = [
        {"state_str": "s_search", "canonical_id": "c_search", "structure_str": "h1", "activity": "co.app.Main", "views": v},
        {"state_str": "s_res", "canonical_id": "c_res", "structure_str": "h1", "activity": "co.app.Main", "views": v},
        {"state_str": "s_empty", "canonical_id": "c_empty", "structure_str": "h1", "activity": "co.app.Main", "views": v},
    ]
    transitions = [
        {"from_screen": "c_search", "to_screen": "c_res", "event_type": "type_submit", "event_str": "type_submit \"화성\"@[0,0][1,1]", "input_value": "화성", "expect": "results"},
        {"from_screen": "c_search", "to_screen": "c_empty", "event_type": "type_submit", "event_str": "type_submit \"zzqx\"@[0,0][1,1]", "input_value": "zzqx", "expect": "empty", "outcome": "empty"},
    ]
    pages = cluster_screens_to_pages(states, transitions)
    ids = {p["state_strs"][0] if p.get("state_strs") else p["page_id"]: p["page_id"] for p in pages}
    by_outcome = {p.get("search_outcome") or "": p["page_id"] for p in pages}
    assert len({p["page_id"] for p in pages}) == 3                  # 검색 / 결과 / 빈 결과 페이지가 다 다르다
    assert set(by_outcome) == {"", "results", "empty"}
