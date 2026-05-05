"""Unit tests for C (signature_stabilizer) + D (semantic_merge) coalesce approaches +
fragment extraction (DeskClock-style R8-obfuscated tab apps)."""

from pathlib import Path

from stage3_walk.signature_stabilizer import (
    stabilize_resource_id, stabilize_content_desc, is_dynamic_class,
    compute_structure_str, compute_state_str,
)
from stage3_walk.view_tree_parser import extract_fragment
from stage6_screenmap.semantic_merge import (
    _label_similarity, _normalize_label, semantic_merge, _is_mergeable,
)
import stage6_screenmap.semantic_merge as _sm_mod

FIXTURES = Path(__file__).parent / "fixtures"


# ─── Fragment extraction (DeskClock R8-obfuscated multi-tab) ──────

def test_fragment_extract_stopwatch_tab():
    """Real dumpsys captured after tapping the Stopwatch bottom-nav tab —
    must return 'STOPWATCH' (tag), not 'TaskFragment' (system noise) or
    'ays' (obfuscated class of the first listed fragment)."""
    out = (FIXTURES / "deskclock_dumpsys_stopwatch_tab.txt").read_text(encoding="utf-8", errors="replace")
    assert extract_fragment(out) == "STOPWATCH"


def test_fragment_extract_bedtime_tab():
    out = (FIXTURES / "deskclock_dumpsys_bedtime_tab.txt").read_text(encoding="utf-8", errors="replace")
    assert extract_fragment(out) == "BEDTIME"


def test_fragment_extract_clocks_tab():
    out = (FIXTURES / "deskclock_dumpsys_clocks_tab.txt").read_text(encoding="utf-8", errors="replace")
    assert extract_fragment(out) == "CLOCKS"


def test_fragment_extract_ignores_system_fragment_classes():
    """Dumpsys containing only 'TaskFragment' noise must return '', not
    TaskFragment (which is a WindowManager internal, not a real Fragment)."""
    fake = (
        "TASK null id=2 userId=0\n"
        "  ACTIVITY com.foo/.Bar\n"
        "    FragmentManager misc state:\n"
        "      mLastReportedActivityWindowInfo=ActivityWindowInfo{"
        "isEmbedded=false, taskBounds=Rect(0, 0 - 1080, 2400), "
        "taskFragmentBounds=Rect(0, 0 - 1080, 2400)}\n"
    )
    assert extract_fragment(fake) == ""


def test_fragment_extract_empty_input():
    assert extract_fragment("") == ""
    assert extract_fragment("no fragment info here") == ""


# ─── C: signature_stabilizer ───────────────────────────────────


# ─── C: signature_stabilizer ───────────────────────────────────

def test_resource_id_numeric_suffix_stripped():
    assert stabilize_resource_id("timer_item_3") == "timer_item_*"
    assert stabilize_resource_id("alarm_row_12") == "alarm_row_*"
    assert stabilize_resource_id("id:0002") == "id:*"
    assert stabilize_resource_id("btn_ok") == "btn_ok"
    assert stabilize_resource_id("") == ""


def test_content_desc_time_masking():
    assert stabilize_content_desc("3:45 PM") == "* PM"
    assert stabilize_content_desc("Elapsed 12:05:30") == "Elapsed *"
    assert stabilize_content_desc("3시 45분 30초") == "*"
    assert stabilize_content_desc("Button") == "Button"


def test_dynamic_class_filter():
    assert is_dynamic_class("android.widget.TextClock")
    assert is_dynamic_class("android.widget.Chronometer")
    assert not is_dynamic_class("android.widget.TextView")


def test_structure_str_stable_across_ticks():
    """A live clock ticking must not bump structure_str."""
    views_t0 = [
        {"class": "android.widget.TextClock", "text": "12:34:56", "clickable": False},
        {"class": "android.widget.Button", "resource_id": "btn_start", "clickable": True},
    ]
    views_t1 = [
        {"class": "android.widget.TextClock", "text": "12:34:57", "clickable": False},
        {"class": "android.widget.Button", "resource_id": "btn_start", "clickable": True},
    ]
    s0 = compute_structure_str("DeskClock", "StopwatchFragment", views_t0)
    s1 = compute_structure_str("DeskClock", "StopwatchFragment", views_t1)
    assert s0 == s1


def test_structure_str_stable_across_recyclerview_drift():
    """Adding or removing a list item should NOT change the hash."""
    views_2 = [
        {"class": "android.widget.Button", "resource_id": "alarm_row_1", "clickable": True},
        {"class": "android.widget.Button", "resource_id": "alarm_row_2", "clickable": True},
        {"class": "android.widget.Button", "resource_id": "fab_add",     "clickable": True},
    ]
    views_3 = views_2 + [
        {"class": "android.widget.Button", "resource_id": "alarm_row_3", "clickable": True},
    ]
    s2 = compute_structure_str("DeskClock", "AlarmFragment", views_2)
    s3 = compute_structure_str("DeskClock", "AlarmFragment", views_3)
    assert s2 == s3


def test_structure_str_differentiates_new_control():
    """Adding a genuinely-new clickable (not a row) must change the hash."""
    views = [
        {"class": "android.widget.Button", "resource_id": "alarm_row_1", "clickable": True},
    ]
    views_plus = views + [
        {"class": "android.widget.Button", "resource_id": "settings_btn", "clickable": True},
    ]
    assert (
        compute_structure_str("DeskClock", "AlarmFragment", views)
        != compute_structure_str("DeskClock", "AlarmFragment", views_plus)
    )


def test_structure_str_differentiates_different_fragment():
    """Same views but different fragment should still differ."""
    views = [{"class": "android.widget.Button", "resource_id": "btn", "clickable": True}]
    assert (
        compute_structure_str("DeskClock", "AlarmFragment", views)
        != compute_structure_str("DeskClock", "TimerFragment", views)
    )


def test_state_str_stable_across_ticks():
    views_t0 = [
        {"class": "android.widget.TextClock", "text": "12:34:56", "clickable": False},
        {"class": "android.widget.TextView",  "text": "Stopwatch", "clickable": False},
    ]
    views_t1 = [
        {"class": "android.widget.TextClock", "text": "12:34:57", "clickable": False},
        {"class": "android.widget.TextView",  "text": "Stopwatch", "clickable": False},
    ]
    assert (
        compute_state_str("DeskClock", "StopwatchFragment", views_t0)
        == compute_state_str("DeskClock", "StopwatchFragment", views_t1)
    )


# ─── D: semantic_merge ────────────────────────────────────

def test_label_similarity_identical():
    assert _label_similarity("Settings Page", "Settings Page") == 1.0
    assert _label_similarity("settings page", "Settings Page") == 1.0  # case-insensitive


def test_label_normalization_collapses_punctuation():
    assert _normalize_label("Stopwatch — Running") == "stopwatch running"
    assert _normalize_label("Settings/Audio") == "settings audio"


def test_semantic_merge_catches_identical_labels():
    """3 'Settings Page' nodes with same activity should merge to 1."""
    screenmap = {
        "screen_map": {
            "graph": {
                "nodes": [
                    {"screen_id": "a", "activity": "X", "label": "Settings Page",
                     "node_type": "activity", "functional_category": "settings"},
                    {"screen_id": "b", "activity": "X", "label": "Settings Page",
                     "node_type": "activity", "functional_category": "settings"},
                    {"screen_id": "c", "activity": "X", "label": "Settings Page",
                     "node_type": "activity", "functional_category": "settings"},
                ],
                "edges": [
                    {"from": "a", "to": "a", "kind": "navigate"},   # self-loop; dropped
                    {"from": "a", "to": "b", "kind": "navigate"},
                    {"from": "c", "to": "a", "kind": "navigate"},
                ],
            },
        },
    }
    semantic_merge(screenmap)
    nodes = screenmap["screen_map"]["graph"]["nodes"]
    assert len(nodes) == 1
    # The surviving node tracks which ones got absorbed
    assert "merged_from" in nodes[0]
    assert len(nodes[0]["merged_from"]) == 2


def test_semantic_merge_respects_different_fragment():
    """Same label but different fragment_class should NOT merge."""
    screenmap = {
        "screen_map": {
            "graph": {
                "nodes": [
                    {"screen_id": "a", "activity": "X", "fragment_class": "HomeFragment",
                     "label": "Main Screen", "node_type": "fragment"},
                    {"screen_id": "b", "activity": "X", "fragment_class": "SearchFragment",
                     "label": "Main Screen", "node_type": "fragment"},
                ],
                "edges": [],
            },
        },
    }
    semantic_merge(screenmap)
    assert len(screenmap["screen_map"]["graph"]["nodes"]) == 2


def test_semantic_merge_does_not_cross_activities():
    """Same label, different activity → never merge."""
    screenmap = {
        "screen_map": {
            "graph": {
                "nodes": [
                    {"screen_id": "a", "activity": "MainActivity",  "label": "List"},
                    {"screen_id": "b", "activity": "OtherActivity", "label": "List"},
                ],
                "edges": [],
            },
        },
    }
    semantic_merge(screenmap)
    assert len(screenmap["screen_map"]["graph"]["nodes"]) == 2


def test_semantic_merge_tier2_requires_edge_overlap():
    """Similar-but-not-identical labels with DISJOINT edge kinds must NOT merge."""
    screenmap = {
        "screen_map": {
            "graph": {
                "nodes": [
                    {"screen_id": "a", "activity": "X", "label": "Home screen"},
                    {"screen_id": "b", "activity": "X", "label": "Home screen v2"},
                ],
                "edges": [
                    {"from": "a", "to": "z", "kind": "navigate"},
                    {"from": "b", "to": "y", "kind": "contains"},
                ],
            },
        },
    }
    semantic_merge(screenmap, threshold=0.80)   # their ratio ~0.88, above 0.8
    # Edge kinds don't overlap → no merge
    assert len(screenmap["screen_map"]["graph"]["nodes"]) == 2


def test_semantic_merge_preserves_screenshot():
    """When merging, if only one node has a screenshot_ref, keep it."""
    screenmap = {
        "screen_map": {
            "graph": {
                "nodes": [
                    {"screen_id": "a", "activity": "X", "label": "Same", "screenshot_ref": None},
                    {"screen_id": "b", "activity": "X", "label": "Same", "screenshot_ref": "shot.jpg"},
                ],
                "edges": [],
            },
        },
    }
    semantic_merge(screenmap)
    nodes = screenmap["screen_map"]["graph"]["nodes"]
    assert len(nodes) == 1
    assert nodes[0]["screenshot_ref"] == "shot.jpg"


def test_semantic_merge_rewrites_edges():
    """After merging b into a, edges that pointed to b should now point to a,
    and duplicate edges should be coalesced."""
    screenmap = {
        "screen_map": {
            "graph": {
                "nodes": [
                    {"screen_id": "a", "activity": "X", "label": "Same"},
                    {"screen_id": "b", "activity": "X", "label": "Same"},
                    {"screen_id": "c", "activity": "Y", "label": "Other"},
                ],
                "edges": [
                    {"from": "c", "to": "a", "kind": "navigate", "trigger_action": "tap"},
                    {"from": "c", "to": "b", "kind": "navigate", "trigger_action": "tap"},
                ],
            },
        },
    }
    semantic_merge(screenmap)
    edges = screenmap["screen_map"]["graph"]["edges"]
    # Both edges collapse to (c → a, navigate, tap) — coalesce
    assert len(edges) == 1
    assert edges[0]["to"] == "a"


# ─── P0-9 (2026-05-04) — Tier A label guard ──────────────


def _seed_phash(monkeypatch, mapping: dict[str, str]) -> None:
    """_compute_phash 가 디스크 안 거치고 mapping 의 hex 반환하게 한다."""
    monkeypatch.setattr(_sm_mod, "_compute_phash",
                        lambda path: mapping.get(path, ""))


def test_tier_a_blocks_when_labels_differ_despite_phash_close(monkeypatch):
    """webview-dominant 시나리오 — structure_str 같고 phash 거리 0 인데
    label 명백히 다른 두 노드는 Tier A 가 거부, Tier 1/2 fallthrough.
    7fe3f44a 잡의 \"주문 영수증\" vs \"이벤트 상세\" 같은 케이스."""
    same_struct = "X|Y|Z"
    same_phash = "0" * 16  # phash distance = 0
    a = {"screen_id": "a", "activity": "WebActivity", "node_type": "activity",
         "label": "주문 영수증", "structure_str": same_struct,
         "screenshot_ref": "/tmp/a.png"}
    b = {"screen_id": "b", "activity": "WebActivity", "node_type": "activity",
         "label": "이벤트 상세", "structure_str": same_struct,
         "screenshot_ref": "/tmp/b.png"}
    _seed_phash(monkeypatch, {"/tmp/a.png": same_phash, "/tmp/b.png": same_phash})
    # label 다름 → Tier 1 거부, Tier 2 도 sim 낮아 거부 → 전체 거부
    assert _is_mergeable(a, b, edges=[], threshold=0.85) is False


def test_tier_a_still_merges_when_one_label_empty(monkeypatch):
    """label 한쪽만 비었으면 (la or lb 가 falsy) Tier A 의 label guard 통과
    → 기존 동작 보존 (LLM 라벨 누락된 노드는 합쳐도 OK)."""
    a = {"screen_id": "a", "activity": "WebActivity", "node_type": "activity",
         "label": "주문 영수증", "structure_str": "X",
         "screenshot_ref": "/tmp/a.png"}
    b = {"screen_id": "b", "activity": "WebActivity", "node_type": "activity",
         "label": "", "structure_str": "X",
         "screenshot_ref": "/tmp/b.png"}
    _seed_phash(monkeypatch, {"/tmp/a.png": "0" * 16, "/tmp/b.png": "0" * 16})
    assert _is_mergeable(a, b, edges=[], threshold=0.85) is True


def test_tier_a_still_merges_when_labels_match(monkeypatch):
    """label 동일 + phash 같음 → Tier A 정상 merge 유지."""
    a = {"screen_id": "a", "activity": "X", "node_type": "activity",
         "label": "Settings", "structure_str": "S",
         "screenshot_ref": "/tmp/a.png"}
    b = {"screen_id": "b", "activity": "X", "node_type": "activity",
         "label": "Settings", "structure_str": "S",
         "screenshot_ref": "/tmp/b.png"}
    _seed_phash(monkeypatch, {"/tmp/a.png": "0" * 16, "/tmp/b.png": "0" * 16})
    assert _is_mergeable(a, b, edges=[], threshold=0.85) is True


def test_tier_a_label_guard_does_not_break_byte_identical_screenshot(monkeypatch):
    """같은 screenshot_ref (재사용) — _is_mergeable 의 ss_a == ss_b 분기는
    phash 검사 자체를 skip 하므로 label guard 와 무관. Tier 1/2 로 진행."""
    a = {"screen_id": "a", "activity": "X", "node_type": "activity",
         "label": "Home A", "screenshot_ref": "/tmp/same.png"}
    b = {"screen_id": "b", "activity": "X", "node_type": "activity",
         "label": "Home B", "screenshot_ref": "/tmp/same.png"}
    # ss_a == ss_b 라 phash 검사 skip — label 다르고 sim 낮으니 거부 (정상)
    assert _is_mergeable(a, b, edges=[], threshold=0.85) is False


def test_tier_a_label_guard_falls_through_when_normalized_labels_match(monkeypatch):
    """label 표면은 다르지만 normalize 후 같으면 (예: 'Settings ' vs ' SETTINGS ')
    Tier A 거부 안 하고 Tier 1 이 매칭."""
    a = {"screen_id": "a", "activity": "X", "node_type": "activity",
         "label": "Settings ", "structure_str": "S",
         "screenshot_ref": "/tmp/a.png"}
    b = {"screen_id": "b", "activity": "X", "node_type": "activity",
         "label": " SETTINGS ", "structure_str": "S",
         "screenshot_ref": "/tmp/b.png"}
    _seed_phash(monkeypatch, {"/tmp/a.png": "0" * 16, "/tmp/b.png": "0" * 16})
    # normalize 후 동일 → la_n == lb_n → Tier A 통과 (return True)
    assert _is_mergeable(a, b, edges=[], threshold=0.85) is True


def test_tier_a_webview_three_distinct_labels_stay_separate(monkeypatch):
    """7fe3f44a 회귀 시나리오 통합 — webview 3개가 모두 phash≈0 + structure 동일
    인데 label 다름 → 합쳐지지 않고 3개 그대로."""
    nodes = [
        {"screen_id": f"web_{i}", "activity": "WebActivity",
         "node_type": "activity", "label": label,
         "structure_str": "WEBVIEW|FRAME",
         "screenshot_ref": f"/tmp/web_{i}.png"}
        for i, label in enumerate(["주문 영수증", "이벤트 상세", "스탬프 적립 현황"])
    ]
    screenmap = {"screen_map": {"graph": {"nodes": nodes, "edges": []}}}
    _seed_phash(monkeypatch, {n["screenshot_ref"]: "0" * 16 for n in nodes})
    semantic_merge(screenmap)
    surviving = screenmap["screen_map"]["graph"]["nodes"]
    assert len(surviving) == 3, \
        f"webview 3 distinct-label nodes 가 합쳐짐: {len(surviving)} 남음"


def test_semantic_merge_never_touches_system_entry():
    screenmap = {
        "screen_map": {
            "graph": {
                "nodes": [
                    {"screen_id": "system:external_entry", "activity": "system:external_entry",
                     "label": "Entry"},
                    {"screen_id": "b", "activity": "system:external_entry", "label": "Entry"},
                ],
                "edges": [],
            },
        },
    }
    semantic_merge(screenmap)
    assert len(screenmap["screen_map"]["graph"]["nodes"]) == 2
