"""ListView detector — 동일 sibling pattern 묶음 detect + score penalty."""

from __future__ import annotations

from stage3_walk.list_view_detector import (
    detect_list_views,
    list_view_redundancy_penalty,
    view_to_group,
    MIN_LIST_VIEW_SIZE,
)


def _v(idx, cls, parent_index=None, parent_class="", clickable=True,
       bounds=(0, 0, 100, 80), text=""):
    return {
        "_idx": idx, "class": cls, "parent_index": parent_index,
        "parent_class": parent_class, "clickable": clickable,
        "bounds": list(bounds), "text": text,
    }


# ─── detect_list_views ───

def test_recyclerview_children_grouped_when_three_or_more():
    views = [
        _v(0, "androidx.recyclerview.widget.RecyclerView", clickable=False),
        _v(1, "android.widget.TextView", parent_index=0),
        _v(2, "android.widget.TextView", parent_index=0),
        _v(3, "android.widget.TextView", parent_index=0),
    ]
    groups = detect_list_views(views)
    assert len(groups) == 1
    assert groups[0]["pattern"] == "recyclerview"
    assert set(groups[0]["item_indices"]) == {1, 2, 3}


def test_recyclerview_with_two_children_is_not_list_view():
    views = [
        _v(0, "RecyclerView", clickable=False),
        _v(1, "TextView", parent_index=0),
        _v(2, "TextView", parent_index=0),
    ]
    assert detect_list_views(views) == []


def test_sibling_uniform_webview_grid():
    """WebView 내부 같은 class + 같은 height 클릭 가능 sibling N>=3 → list_view."""
    views = [
        _v(0, "WebView", clickable=False),
        _v(1, "MenuCard", parent_index=0, bounds=(0, 100, 200, 180)),
        _v(2, "MenuCard", parent_index=0, bounds=(200, 100, 400, 180)),
        _v(3, "MenuCard", parent_index=0, bounds=(400, 100, 600, 180)),
        _v(4, "MenuCard", parent_index=0, bounds=(0, 200, 200, 280)),
    ]
    groups = detect_list_views(views)
    assert len(groups) == 1
    assert groups[0]["pattern"] == "sibling_uniform"
    assert set(groups[0]["item_indices"]) == {1, 2, 3, 4}


def test_recyclerview_priority_over_sibling():
    """RecyclerView 자식이면 sibling pattern 으로 중복 안 잡혀야."""
    views = [
        _v(0, "RecyclerView", clickable=False),
        _v(1, "Item", parent_index=0, bounds=(0, 100, 100, 180)),
        _v(2, "Item", parent_index=0, bounds=(0, 200, 100, 280)),
        _v(3, "Item", parent_index=0, bounds=(0, 300, 100, 380)),
    ]
    groups = detect_list_views(views)
    # 한 그룹만 — RecyclerView 자식들이 sibling 패턴 으로 다시 잡히지 않음
    assert len(groups) == 1


def test_grid_view_detected():
    views = [
        _v(0, "GridView", clickable=False),
        _v(1, "ImageView", parent_index=0),
        _v(2, "ImageView", parent_index=0),
        _v(3, "ImageView", parent_index=0),
    ]
    groups = detect_list_views(views)
    assert len(groups) == 1
    assert "Grid" in groups[0]["container_class"]


def test_empty_views():
    assert detect_list_views([]) == []


def test_no_list_view_when_below_threshold():
    views = [_v(0, "RecyclerView"), _v(1, "TextView", parent_index=0)]
    assert detect_list_views(views) == []
    assert MIN_LIST_VIEW_SIZE >= 3


def test_string_bounds_does_not_crash():
    """view_tree_parser 가 어떤 view 의 bounds 를 '[x1,y1][x2,y2]' string 으로 줄 때
    list_view_detector 가 TypeError 안 내고 처리. (2026-05-01 메가커피 426cc2ea
    Stage 3 fail 회귀)."""
    views = [
        {"class": "WebView", "parent_index": None, "clickable": False,
         "bounds": "[0,0][1080,2400]"},
        {"class": "MenuCard", "parent_index": 0, "clickable": True,
         "bounds": "[0,100][200,180]"},
        {"class": "MenuCard", "parent_index": 0, "clickable": True,
         "bounds": "[200,100][400,180]"},
        {"class": "MenuCard", "parent_index": 0, "clickable": True,
         "bounds": "[400,100][600,180]"},
    ]
    # crash 없이 list_view detect — string bounds 에서 height 정상 파싱 = 80px
    groups = detect_list_views(views)
    assert len(groups) == 1
    assert set(groups[0]["item_indices"]) == {1, 2, 3}


# ─── view_to_group / penalty ───

def test_view_to_group_lookup():
    groups = [{"group_id": "g1", "item_indices": [3, 4, 5]}]
    assert view_to_group(groups, 4)["group_id"] == "g1"
    assert view_to_group(groups, 9) is None


def test_penalty_first_visit_zero():
    groups = [{"group_id": "g1", "item_indices": [3, 4, 5]}]
    assert list_view_redundancy_penalty(3, groups, {}) == 0.0


def test_penalty_second_visit_minus_three():
    groups = [{"group_id": "g1", "item_indices": [3, 4, 5]}]
    visits = {"g1": 1}
    assert list_view_redundancy_penalty(3, groups, visits) == -3.0


def test_penalty_after_three_visits_minus_six():
    groups = [{"group_id": "g1", "item_indices": [3, 4, 5]}]
    visits = {"g1": 3}
    assert list_view_redundancy_penalty(3, groups, visits) == -6.0


def test_penalty_for_view_outside_list_view_zero():
    groups = [{"group_id": "g1", "item_indices": [3, 4, 5]}]
    visits = {"g1": 5}
    # view 9 는 어떤 그룹에도 안 속함
    assert list_view_redundancy_penalty(9, groups, visits) == 0.0
