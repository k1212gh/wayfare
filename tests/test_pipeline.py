"""Integration tests for ScreenAtlas pipeline components."""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stage4_screens.view_tree_cleaner import clean_views, views_to_cleaned_xml
from stage4_screens.screen_clusterer import cluster_screens_to_pages
from stage4_screens.screen_card_builder import build_screen_cards
from stage5_annotate.grounding_checker import check_grounding
from stage6_screenmap.screenmap_builder import build_graph
from stage6_screenmap.screenmap_validator import validate_graph
from stage6_screenmap.screenmap_enricher import enrich_graph
from stage6_screenmap.screenmap_serializer import serialize_screenmap
from stage3_walk.activity_coverage import check_coverage


# ============================================================
# Fixtures: simulate DroidBot output
# ============================================================

MOCK_VIEWS_HOME = [
    {"resource_id": "com.example:id/btn_search", "class": "android.widget.ImageButton",
     "text": "", "content_desc": "Search", "clickable": True, "visible": True,
     "bounds": "[0,0][100,100]", "package": "com.example", "depth": 2},
    {"resource_id": "com.example:id/rv_feed", "class": "android.widget.RecyclerView",
     "text": "", "content_desc": "", "scrollable": True, "visible": True,
     "bounds": "[0,100][1080,1920]", "package": "com.example", "depth": 2},
    {"resource_id": "com.example:id/tab_profile", "class": "android.widget.TextView",
     "text": "Profile", "content_desc": "", "clickable": True, "visible": True,
     "bounds": "[800,1800][1080,1920]", "package": "com.example", "depth": 3},
]

MOCK_VIEWS_SEARCH = [
    {"resource_id": "com.example:id/et_query", "class": "android.widget.EditText",
     "text": "", "content_desc": "", "editable": True, "clickable": True, "visible": True,
     "bounds": "[0,0][1080,100]", "package": "com.example", "depth": 2},
    {"resource_id": "com.example:id/rv_results", "class": "android.widget.RecyclerView",
     "text": "", "content_desc": "", "scrollable": True, "visible": True,
     "bounds": "[0,100][1080,1920]", "package": "com.example", "depth": 2},
]

MOCK_VIEWS_PROFILE = [
    {"resource_id": "com.example:id/tv_username", "class": "android.widget.TextView",
     "text": "John", "content_desc": "", "clickable": False, "visible": True, "depth": 2},
    {"resource_id": "com.example:id/btn_settings", "class": "android.widget.Button",
     "text": "Settings", "content_desc": "", "clickable": True, "visible": True, "depth": 2},
]

MOCK_STATES = [
    {"state_str": "hash_home_1", "structure_str": "struct_home", "activity": "com.example.HomeActivity",
     "views": MOCK_VIEWS_HOME},
    {"state_str": "hash_home_2", "structure_str": "struct_home", "activity": "com.example.HomeActivity",
     "views": MOCK_VIEWS_HOME},  # same structure, different data
    {"state_str": "hash_search", "structure_str": "struct_search", "activity": "com.example.SearchActivity",
     "views": MOCK_VIEWS_SEARCH},
    {"state_str": "hash_profile", "structure_str": "struct_profile", "activity": "com.example.ProfileActivity",
     "views": MOCK_VIEWS_PROFILE},
    {"state_str": "hash_home_1", "structure_str": "struct_home", "activity": "com.example.HomeActivity",
     "views": MOCK_VIEWS_HOME},  # DUPLICATE — should be coalesceed
]

MOCK_TRANSITIONS = [
    {"from_screen": "hash_home_1", "to_screen": "hash_search", "event_type": "touch", "event_str": "click btn_search"},
    {"from_screen": "hash_home_1", "to_screen": "hash_profile", "event_type": "touch", "event_str": "click tab_profile"},
    {"from_screen": "hash_search", "to_screen": "hash_home_1", "event_type": "touch", "event_str": "press_back"},
    {"from_screen": "hash_profile", "to_screen": "hash_home_1", "event_type": "touch", "event_str": "press_back"},
    # Duplicate transition — should be coalesceed at page level
    {"from_screen": "hash_home_2", "to_screen": "hash_search", "event_type": "touch", "event_str": "click btn_search"},
]


def test_view_tree_cleaner():
    """Test that XML cleaner removes noise and keeps functional attributes."""
    cleaned = clean_views(MOCK_VIEWS_HOME)
    assert len(cleaned) > 0, "Should produce at least 1 cleaned view"

    for v in cleaned:
        assert "bounds" not in v, "bounds should be removed"
        assert "package" not in v, "package should be removed"

    # Test XML generation
    xml = views_to_cleaned_xml(cleaned)
    assert "<hierarchy>" in xml
    assert "btn_search" in xml
    assert "clickable" in xml
    print("[PASS] test_view_tree_cleaner")


def test_screen_coalesce_in_clustering():
    """Test that duplicate state_strs are coalesceed before clustering."""
    # MOCK_STATES has 5 entries but hash_home_1 appears twice
    pages = cluster_screens_to_pages(MOCK_STATES, MOCK_TRANSITIONS)

    # struct_home should appear as 1 page (not duplicated)
    home_pages = [p for p in pages if p["activity"] == "com.example.HomeActivity"]
    assert len(home_pages) == 1, f"Expected 1 home page, got {len(home_pages)}"

    # Home page should have 2 unique state_strs (hash_home_1, hash_home_2), not 3
    assert len(home_pages[0]["state_strs"]) == 2, \
        f"Expected 2 state_strs, got {len(home_pages[0]['state_strs'])}"

    print("[PASS] test_screen_coalesce_in_clustering")


def test_structure_str_clustering():
    """Test that states with same structure_str cluster into one page."""
    pages = cluster_screens_to_pages(MOCK_STATES, MOCK_TRANSITIONS)

    # 3 unique structure_strs → 3 pages
    assert len(pages) == 3, f"Expected 3 pages, got {len(pages)}"

    # SHA256[:12] format check
    for p in pages:
        assert p["page_id"].startswith("page_"), f"Bad page_id: {p['page_id']}"
        assert len(p["page_id"]) == 5 + 12, f"Bad page_id length: {p['page_id']}"

    print("[PASS] test_structure_str_clustering")


def test_union_widgets():
    """Test that elements are collected from ALL states in a cluster."""
    # Pre-clean views like run_stage4 does
    cleaned_screens = []
    for s in MOCK_STATES:
        cs = dict(s)
        cs["cleaned_views"] = clean_views(s["views"])
        cleaned_screens.append(cs)

    pages = cluster_screens_to_pages(cleaned_screens, MOCK_TRANSITIONS)
    home_page = next(p for p in pages if p["activity"] == "com.example.HomeActivity")

    # Home has 3 interactive elements (btn_search, rv_feed, tab_profile)
    widget_ids = {e["widget_id"] for e in home_page["elements"]}
    assert "btn_search" in widget_ids, f"btn_search should be in elements, got: {widget_ids}"
    assert "rv_feed" in widget_ids, f"rv_feed should be in elements, got: {widget_ids}"
    assert "tab_profile" in widget_ids, f"tab_profile should be in elements, got: {widget_ids}"
    print("[PASS] test_union_widgets")


def test_page_transitions_coalesce():
    """Test that duplicate transitions (same page pair) are coalesceed."""
    pages = cluster_screens_to_pages(MOCK_STATES, MOCK_TRANSITIONS)

    home_page = next(p for p in pages if p["activity"] == "com.example.HomeActivity")
    search_page = next(p for p in pages if p["activity"] == "com.example.SearchActivity")

    # home→search should appear only once despite 2 raw transitions (from hash_home_1 and hash_home_2)
    home_to_search = [
        t for t in home_page["outgoing_transitions"]
        if t["to_page"] == search_page["page_id"]
    ]
    assert len(home_to_search) == 1, f"Expected 1 home→search transition, got {len(home_to_search)}"
    print("[PASS] test_page_transitions_coalesce")


def test_grounding_checker():
    """Test grounding detection."""
    valid_ids = {"btn_search", "rv_feed"}

    analysis = {
        "screen_purpose": "Home screen",
        "functional_category": "home",
        "key_widgets": [
            {"widget_id": "btn_search", "role": "search"},
            {"widget_id": "fake_button", "role": "fake"},  # hallucinated
            {"widget_id": "", "role": "empty"},  # empty id
        ],
        "confidence": "high",
    }

    result = check_grounding(analysis, valid_ids)

    # fake_button and empty should be removed
    remaining_ids = [e["widget_id"] for e in result["key_widgets"]]
    assert "btn_search" in remaining_ids
    assert "fake_button" not in remaining_ids
    assert "" not in remaining_ids

    # Confidence downgraded
    assert result["confidence"] != "high"
    print("[PASS] test_grounding_checker")


def test_grounding_empty_valid_ids():
    """Test that grounding checker works even with empty valid_ids."""
    analysis = {
        "key_widgets": [
            {"widget_id": "btn_ok", "role": "confirm"},
            {"widget_id": "", "role": "empty"},
        ],
        "functional_category": "INVALID_CATEGORY",
        "confidence": "super_high",
    }

    result = check_grounding(analysis, set())

    # Empty eid should be removed
    remaining = [e["widget_id"] for e in result["key_widgets"]]
    assert "" not in remaining

    # Invalid category should be corrected
    assert result["functional_category"] == "other"
    assert result["confidence"] == "medium"
    print("[PASS] test_grounding_empty_valid_ids")


def test_graph_build_and_validate():
    """Test full graph build → validate → serialize pipeline."""
    # Simulate screen analyses
    screen_analyses = [
        {"screen_id": "page_a", "activity_name": "HomeActivity", "screen_purpose": "Home",
         "functional_category": "home", "key_widgets": [], "confidence": "high"},
        {"screen_id": "page_b", "activity_name": "SearchActivity", "screen_purpose": "Search",
         "functional_category": "search", "key_widgets": [], "confidence": "high"},
        {"screen_id": "page_c", "activity_name": "ProfileActivity", "screen_purpose": "Profile",
         "functional_category": "profile", "key_widgets": [], "confidence": "medium"},
    ]

    subflows = [{
        "subflow_name": "main_flow",
        "nodes": [
            {"screen_id": "page_a", "label": "Home", "screen_params": {"inputs": [], "outputs": ["item_id"], "displays": ["feed"]}},
            {"screen_id": "page_b", "label": "Search", "screen_params": {"inputs": [], "outputs": ["query"], "displays": []}},
        ],
        "edges": [
            {"from": "page_a", "to": "page_b", "trigger_action": "click", "trigger_widget": "btn_search"},
            {"from": "page_b", "to": "page_a", "trigger_action": "press_back", "trigger_widget": ""},
        ],
    }]

    screen_cards = [
        {"screen_id": "page_a", "activity_name": "HomeActivity", "screenshot": "",
         "cleaned_xml": "<hierarchy/>", "available_actions": [],
         "navigation_context": {"from_screens": [], "reachable_screens": ["page_b", "page_c"]}},
        {"screen_id": "page_b", "activity_name": "SearchActivity", "screenshot": "",
         "cleaned_xml": "<hierarchy/>", "available_actions": [],
         "navigation_context": {"from_screens": ["page_a"], "reachable_screens": []}},
        {"screen_id": "page_c", "activity_name": "ProfileActivity", "screenshot": "",
         "cleaned_xml": "<hierarchy/>", "available_actions": [],
         "navigation_context": {"from_screens": ["page_a"], "reachable_screens": []}},
    ]

    # Build
    graph = build_graph(subflows, screen_analyses, screen_cards)
    assert len(graph["nodes"]) == 3
    assert graph["entry_node"] != ""

    # Check edge_ids are unique
    edge_ids = [e["edge_id"] for e in graph["edges"]]
    assert len(edge_ids) == len(set(edge_ids)), f"Duplicate edge_ids: {edge_ids}"

    # Check trigger_widget preserved
    click_edges = [e for e in graph["edges"] if e["trigger_action"] == "click"]
    assert any(e.get("trigger_widget") == "btn_search" for e in click_edges), \
        "trigger_widget should be preserved"

    # Validate
    report = validate_graph(graph)
    assert report["summary"]["total_nodes"] == 3

    # Enrich
    graph = enrich_graph(graph, {"activities": []})

    # Serialize
    screenmap = serialize_screenmap(graph, {"package_name": "com.example.app", "version_name": "1.0"}, report)
    assert "screen_map" in screenmap
    assert screenmap["screen_map"]["package_name"] == "com.example.app"
    assert screenmap["screen_map"]["metadata"]["total_nodes"] == 3

    # Check trigger_widget in serialized edges
    serialized_edges = screenmap["screen_map"]["graph"]["edges"]
    assert any(e.get("trigger_widget") == "btn_search" for e in serialized_edges)

    print("[PASS] test_graph_build_and_validate")


def test_coverage_suffix_match():
    """Test that coverage tracker handles relative activity names."""
    walk = {
        "activities_found": [
            "com.example.app.MainActivity",
            "com.example.app.SearchActivity",
        ]
    }
    static_activities = [
        ".MainActivity",  # relative
        "com.example.app.SearchActivity",  # full
        "com.example.app.SettingsActivity",  # not found
    ]

    report = check_coverage(walk, static_activities)
    assert report["covered_count"] == 2, f"Expected 2 covered, got {report['covered_count']}"
    assert report["missed_count"] == 1, f"Expected 1 missed, got {report['missed_count']}"
    print("[PASS] test_coverage_suffix_match")


if __name__ == "__main__":
    test_view_tree_cleaner()
    test_screen_coalesce_in_clustering()
    test_structure_str_clustering()
    test_union_widgets()
    test_page_transitions_coalesce()
    test_grounding_checker()
    test_grounding_empty_valid_ids()
    test_graph_build_and_validate()
    test_coverage_suffix_match()
    print()
    print("=== ALL 9 TESTS PASSED ===")
