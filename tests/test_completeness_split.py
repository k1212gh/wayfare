"""Phase A 1번 (2026-04-29) — completeness namespace 분리 단위 테스트.

검증 대상: stage6_screenmap.metadata_refresh._build_completeness()
- manifest_reachability: declared / launched / captured / enriched 카운트 + ratio
- overlay_count: dialog / bottom_sheet / snackbar / popup / toast 분리
- task_coverage: 지금은 None (fixture infra 도입 후 채움)
- backward-compat alias — activity_coverage 가 launched_ratio 와 일치
"""

from __future__ import annotations

from stage6_screenmap.metadata_refresh import refresh_metadata, _is_overlay_node


# ─── manifest_reachability ────────────────────────────────


def test_manifest_reachability_three_stages_distinct():
    """launched / captured / enriched 가 단계별 다른 카운트로 구분되는지."""
    nodes = [
        # declared: A, B, C, D
        # launched (probed/enriched/resolved): A, B, C
        # captured (widgets 있음): B, C
        # enriched (status=enriched): C
        {"screen_id": "n1", "activity": "com.app.A", "status": "probed"},
        {"screen_id": "n2", "activity": "com.app.B", "status": "probed",
         "widgets": [{"id": "x"}]},
        {"screen_id": "n3", "activity": "com.app.C", "status": "enriched",
         "widgets": [{"id": "y"}]},
        {"screen_id": "n4", "activity": "com.app.X", "status": "declared"},  # not declared
    ]
    static = {"activities": [
        {"name": "com.app.A"}, {"name": "com.app.B"},
        {"name": "com.app.C"}, {"name": "com.app.D"},
    ]}
    screenmap = {"screen_map": {
        "graph": {"entry_node": "n1", "nodes": nodes, "edges": []},
    }}
    md = refresh_metadata(screenmap, static_info=static)
    mr = md["completeness"]["manifest_reachability"]

    assert mr["declared"] == 4
    assert mr["launched"] == 3, f"A/B/C launched, X 는 declared 아님 — got {mr['launched']}"
    assert mr["captured"] == 2, f"widgets 있는 B/C — got {mr['captured']}"
    assert mr["enriched"] == 1, f"status=enriched 인 C — got {mr['enriched']}"
    assert mr["launched_ratio"] == 0.75
    assert mr["captured_ratio"] == 0.5
    assert mr["enriched_ratio"] == 0.25


def test_manifest_reachability_null_when_no_static():
    """RN/Compose 처럼 static_info 가 없으면 declared=null."""
    nodes = [{"screen_id": "n1", "activity": "com.app.A", "status": "enriched"}]
    screenmap = {"screen_map": {
        "graph": {"entry_node": "n1", "nodes": nodes, "edges": []},
    }}
    md = refresh_metadata(screenmap, static_info=None)
    mr = md["completeness"]["manifest_reachability"]

    assert mr["declared"] is None
    assert mr["launched"] is None
    assert mr["launched_ratio"] is None


# ─── overlay_count ────────────────────────────────────────


def test_overlay_node_classification():
    """label/activity 의 키워드로 5 종류 분류."""
    assert _is_overlay_node({"label": "Confirm Dialog"}) == "dialog"
    assert _is_overlay_node({"activity": "MyBottomSheetDialog"}) == "bottom_sheet"
    assert _is_overlay_node({"label": "에러 Snackbar"}) == "snackbar"
    assert _is_overlay_node({"label": "Toast notification"}) == "toast"
    assert _is_overlay_node({"label": "메뉴 Popup"}) == "popup"
    assert _is_overlay_node({"label": "Home"}) is None
    assert _is_overlay_node({"activity": "com.example.MainActivity"}) is None


def test_overlay_count_aggregates():
    """여러 overlay 노드가 종류별 누적되는지."""
    nodes = [
        {"screen_id": "n1", "activity": "Main"},
        {"screen_id": "n2", "label": "확인 Dialog"},
        {"screen_id": "n3", "label": "Detail BottomSheetDialog"},
        {"screen_id": "n4", "label": "Error Snackbar"},
        {"screen_id": "n5", "label": "Settings"},  # 일반
    ]
    screenmap = {"screen_map": {
        "graph": {"entry_node": "n1", "nodes": nodes, "edges": []},
    }}
    md = refresh_metadata(screenmap)
    ov = md["completeness"]["overlay_count"]

    assert ov["dialog"] == 1
    assert ov["bottom_sheet"] == 1
    assert ov["snackbar"] == 1
    assert ov["total"] == 3


def test_overlay_count_zero_for_normal_app():
    """overlay 가 하나도 없는 앱 — 0 이지만 schema 는 항상 있음."""
    nodes = [
        {"screen_id": "n1", "activity": "com.app.Main"},
        {"screen_id": "n2", "activity": "com.app.Settings"},
    ]
    screenmap = {"screen_map": {
        "graph": {"entry_node": "n1", "nodes": nodes, "edges": []},
    }}
    md = refresh_metadata(screenmap)
    ov = md["completeness"]["overlay_count"]
    assert ov["total"] == 0
    assert all(ov[k] == 0 for k in ("dialog", "bottom_sheet", "snackbar", "popup", "toast"))


# ─── task_coverage ────────────────────────────────────────


def test_task_coverage_none_until_fixture_infra():
    """fixture 가 없으면 task_coverage = None (Phase B 작업)."""
    nodes = [{"screen_id": "n1"}]
    screenmap = {"screen_map": {
        "graph": {"entry_node": "n1", "nodes": nodes, "edges": []},
    }}
    md = refresh_metadata(screenmap)
    assert md["completeness"]["task_coverage"] is None


# ─── backward-compat alias ───────────────────────────────


def test_activity_coverage_alias_matches_launched_ratio():
    """이전 코드 (frontend/API) 가 읽던 activity_coverage 는
    이제 manifest_reachability.launched_ratio 의 별칭."""
    nodes = [
        {"screen_id": "n1", "activity": "com.app.A", "status": "probed"},
        {"screen_id": "n2", "activity": "com.app.B", "status": "enriched",
         "widgets": [{"id": "x"}]},
    ]
    static = {"activities": [
        {"name": "com.app.A"}, {"name": "com.app.B"},
        {"name": "com.app.C"}, {"name": "com.app.D"},
    ]}
    screenmap = {"screen_map": {
        "graph": {"entry_node": "n1", "nodes": nodes, "edges": []},
    }}
    md = refresh_metadata(screenmap, static_info=static)

    mr = md["completeness"]["manifest_reachability"]
    assert md["activity_coverage"] == mr["launched_ratio"] == 0.5
    assert md["activity_coverage_captured"] == mr["enriched_ratio"] == 0.25


def test_activity_coverage_absent_when_no_static():
    """static 없으면 alias 도 미설정 (frontend 가 null 안 표시)."""
    nodes = [{"screen_id": "n1", "activity": "com.app.A", "status": "enriched"}]
    screenmap = {"screen_map": {
        "graph": {"entry_node": "n1", "nodes": nodes, "edges": []},
    }}
    md = refresh_metadata(screenmap, static_info=None)
    # alias 는 정의 안 됨 (declared 없으면 set 안 함)
    assert "activity_coverage" not in md or md.get("activity_coverage") is None


# ─── completeness 가 항상 있어야 함 ─────────────────────


def test_completeness_always_present():
    """static_info 유무와 무관하게 completeness 필드는 항상 있음."""
    screenmap = {"screen_map": {
        "graph": {"entry_node": "n1", "nodes": [{"screen_id": "n1"}], "edges": []},
    }}
    md = refresh_metadata(screenmap)
    assert "completeness" in md
    assert "manifest_reachability" in md["completeness"]
    assert "overlay_count" in md["completeness"]
    assert "task_coverage" in md["completeness"]
