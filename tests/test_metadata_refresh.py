"""Phase 1 P0.1 — metadata refresh 단위 테스트.

검증 대상: stage6_screenmap.metadata_refresh.refresh_metadata()
- semantic_merge 후 stale 된 metadata 가 정정되는지
- actionable / plannable / severity / activity_coverage 정확히 계산되는지
- static_info 없으면 activity_coverage 미설정
- ScreenMap wrapper / inner graph 양쪽 다 처리
"""

from __future__ import annotations

from stage6_screenmap.metadata_refresh import refresh_metadata, _is_actionable, _is_plannable


def _make_screenmap(nodes, edges, entry="n1", stale_meta=None):
    md = stale_meta or {}
    return {
        "screen_map": {
            "metadata": md,
            "graph": {"entry_node": entry, "nodes": nodes, "edges": edges},
        }
    }


def test_refresh_corrects_stale_total_nodes():
    """PLAN.md 가 보고한 핵심 버그 — stale metadata 정정."""
    nodes = [
        {"screen_id": "n1", "activity": "A"},
        {"screen_id": "n2", "activity": "B"},
        {"screen_id": "n3", "activity": "C"},
    ]
    edges = [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}]
    # stale: 999 노드라 잘못 적혀있는 상태 (b7b152fd 의 100/233 같은 케이스)
    screenmap = _make_screenmap(nodes, edges, stale_meta={"total_nodes": 999, "total_edges": 999})

    md = refresh_metadata(screenmap)

    assert md["total_nodes"] == 3, f"stale 999 가 3 으로 정정 — got {md['total_nodes']}"
    assert md["total_edges"] == 2


def test_actionable_counts_widgets_options_affordances():
    """3가지 신호 중 하나라도 있으면 actionable."""
    nodes = [
        {"screen_id": "n1", "activity": "A", "widgets": [{"id": "btn"}]},
        {"screen_id": "n2", "activity": "B", "chip_groups": [{"type": "radio"}]},
        {"screen_id": "n3", "activity": "C", "primary_affordances": ["로그인 버튼"]},
        {"screen_id": "n4", "activity": "D"},  # 셋 다 없음 → 비-actionable
    ]
    screenmap = _make_screenmap(nodes, [], entry="n1")

    md = refresh_metadata(screenmap)

    assert md["actionable_nodes"] == 3, f"got {md['actionable_nodes']}"


def test_plannable_requires_actionable_plus_label_plus_status():
    """plannable = actionable + (probed|enriched) + (label or screen_purpose)."""
    nodes = [
        # plannable: 모든 조건 충족
        {"screen_id": "n1", "activity": "A", "widgets": [{"id": "x"}],
         "status": "enriched", "label": "Home"},
        # actionable 하지만 status declared (탐색 안 됨)
        {"screen_id": "n2", "activity": "B", "widgets": [{"id": "y"}],
         "status": "declared", "label": "Settings"},
        # status 는 OK 지만 라벨 없음
        {"screen_id": "n3", "activity": "C", "widgets": [{"id": "z"}],
         "status": "probed"},
        # 비-actionable
        {"screen_id": "n4", "activity": "D", "status": "enriched", "label": "x"},
    ]
    screenmap = _make_screenmap(nodes, [], entry="n1")

    md = refresh_metadata(screenmap)

    assert md["plannable_nodes"] == 1, f"only n1 is plannable — got {md['plannable_nodes']}"


def test_severity_distribution():
    """validation issue 의 severity 분류 카운트."""
    nodes = [
        {"screen_id": "n1", "activity": "A"},
        {"screen_id": "n2", "activity": "B"},  # entry n1 에서 도달 가능
    ]
    edges = [{"from": "n1", "to": "n2"}]
    screenmap = _make_screenmap(nodes, edges, entry="n1")

    md = refresh_metadata(screenmap)

    sev = md["issue_severity"]
    assert isinstance(sev, dict)
    assert "high" in sev and "medium" in sev and "low" in sev
    # n1 / n2 는 dead_end 일 수 있어 medium issues 발생 가능
    assert sev["high"] >= 0


def test_orphan_marked_as_high_severity():
    """entry 에서 도달 불가능한 노드 = orphan = high severity."""
    nodes = [
        {"screen_id": "n1", "activity": "A"},
        {"screen_id": "n2", "activity": "B"},  # n1 에서 도달 불가
    ]
    edges = []  # 아무 엣지 없음 → n2 unreachable
    screenmap = _make_screenmap(nodes, edges, entry="n1")

    md = refresh_metadata(screenmap)

    assert md["orphan_nodes"] == 1, f"got {md['orphan_nodes']}"
    assert md["issue_severity"]["high"] >= 1, "unreachable issue 가 high 로 분류"


def test_reachable_count_matches_total_minus_orphan():
    """reachable_count = total_nodes - orphan_nodes 항등식."""
    nodes = [
        {"screen_id": "n1", "activity": "A"},
        {"screen_id": "n2", "activity": "B"},
        {"screen_id": "n3", "activity": "C"},  # orphan
    ]
    edges = [{"from": "n1", "to": "n2"}]
    screenmap = _make_screenmap(nodes, edges, entry="n1")

    md = refresh_metadata(screenmap)

    assert md["reachable_count"] == md["total_nodes"] - md["orphan_nodes"]


def test_activity_coverage_with_static_info():
    """declared activity 4개 중 ScreenMap 가 covered 한 비율."""
    nodes = [
        {"screen_id": "n1", "activity": "com.x.A", "status": "enriched"},
        {"screen_id": "n2", "activity": "com.x.B", "status": "probed"},
        {"screen_id": "n3", "activity": "com.x.C", "status": "declared"},  # not covered
    ]
    static = {"activities": [
        {"name": "com.x.A"}, {"name": "com.x.B"},
        {"name": "com.x.C"}, {"name": "com.x.D"},  # 4 declared
    ]}
    screenmap = _make_screenmap(nodes, [{"from": "n1", "to": "n2"}], entry="n1")

    md = refresh_metadata(screenmap, static_info=static)

    # covered = {com.x.A (enriched), com.x.B (probed)} = 2 / 4 declared
    assert md["activity_coverage"] == 0.5


def test_activity_coverage_omitted_without_static_info():
    """static_info 없으면 activity_coverage 키 추가 안 함."""
    nodes = [{"screen_id": "n1", "activity": "A"}]
    screenmap = _make_screenmap(nodes, [], entry="n1")

    md = refresh_metadata(screenmap, static_info=None)

    assert "activity_coverage" not in md


def test_helpers_actionable_and_plannable():
    """순수 함수 _is_actionable / _is_plannable 단위 검증."""
    n_act = {"widgets": [{"id": "btn"}]}
    n_inact = {"activity": "X"}
    n_plan = {"widgets": [{"id": "btn"}], "status": "enriched", "label": "Home"}
    n_almost = {"widgets": [{"id": "btn"}], "status": "declared", "label": "Home"}

    assert _is_actionable(n_act) is True
    assert _is_actionable(n_inact) is False
    assert _is_plannable(n_plan) is True
    assert _is_plannable(n_almost) is False  # status declared 면 plannable 아님


def test_refresh_handles_inner_graph_dict():
    """screen_map wrapper 없이 graph dict 만 들어와도 동작."""
    graph_only = {
        "graph": {
            "entry_node": "n1",
            "nodes": [{"screen_id": "n1", "activity": "A"}],
            "edges": [],
        }
    }

    md = refresh_metadata(graph_only)

    assert md["total_nodes"] == 1
