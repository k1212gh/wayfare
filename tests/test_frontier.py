"""Frontier 단위 테스트 — LLM-Explorer 식 전역 미탐색 큐 + BFS 경로 계획."""

from __future__ import annotations

from stage3_walk.frontier import Frontier


def _acts(*descs: str) -> list[dict]:
    return [{"desc": d, "action": "click", "score": 1.0} for d in descs]


def _graph() -> Frontier:
    """A -go_b-> B -go_c-> C, A -go_d-> D. C 와 D 에 미시도 액션."""
    fr = Frontier()
    fr.observe_actions("A", _acts("go_b", "go_d"))
    fr.observe_actions("B", _acts("go_c", "back_a"))
    fr.observe_actions("C", _acts("c_untried", "c_tried"))
    fr.observe_actions("D", _acts("d_untried"))
    fr.observe_transition("A", "go_b", "B")
    fr.observe_transition("B", "go_c", "C")
    fr.observe_transition("B", "back_a", "A")
    fr.observe_transition("A", "go_d", "D")
    return fr


def test_observe_and_untried_filter():
    fr = Frontier()
    fr.observe_actions("A", _acts("x", "y", "y", ""))  # 중복/빈 desc 무시
    assert sorted(fr.known_actions["A"]) == ["x", "y"]
    assert fr.untried("A", {"x"}) == ["y"]
    assert fr.untried("A", set(), blocked_descs={"y"}) == ["x"]
    assert fr.untried("missing", set()) == []


def test_self_loop_transition_ignored():
    fr = Frontier()
    fr.observe_transition("A", "tap", "A")
    fr.observe_transition("A", "", "B")
    assert fr.edges == {}


def test_bfs_path_shortest_and_missing():
    fr = _graph()
    assert fr.bfs_path("A", "C") == [("A", "go_b", "B"), ("B", "go_c", "C")]
    assert fr.bfs_path("A", "A") == []
    assert fr.bfs_path("C", "A") is None  # C 에서 나가는 엣지 없음


def test_bfs_respects_max_path_len():
    fr = Frontier(max_path_len=1)
    fr.observe_transition("A", "1", "B")
    fr.observe_transition("B", "2", "C")
    assert fr.bfs_path("A", "B") == [("A", "1", "B")]
    assert fr.bfs_path("A", "C") is None


def test_pick_target_nearest_with_untried():
    fr = _graph()
    tried = {"A": {"go_b", "go_d"}, "B": {"go_c", "back_a"}, "C": {"c_tried"}, "D": set()}
    # A 에서 거리 1 인 D 가 거리 2 인 C 보다 먼저
    target, path = fr.pick_target("A", tried)
    assert target == "D"
    assert path == [("A", "go_d", "D")]


def test_pick_target_skips_blocked_and_exhausted():
    fr = _graph()
    tried = {"A": {"go_b", "go_d"}, "B": {"go_c", "back_a"}, "C": {"c_tried"}, "D": set()}
    # D 차단 → C
    target, path = fr.pick_target("A", tried, blocked_canonicals={"D"})
    assert target == "C" and len(path) == 2
    # C 도 실패 누적으로 소진 → None
    fr.mark_failure("C")
    fr.mark_failure("C")
    assert fr.is_exhausted("C")
    assert fr.pick_target("A", tried, blocked_canonicals={"D"}) is None
    assert fr.stats["no_target"] == 1
    assert fr.stats["targets_exhausted"] == 1


def test_pick_target_blocked_descs_make_screen_not_a_target():
    fr = _graph()
    tried = {"A": {"go_b", "go_d"}, "B": {"go_c", "back_a"}, "C": {"c_tried"}, "D": set()}
    # D 의 유일한 미시도가 블랙리스트 → D 는 목표 아님 → C
    target, _ = fr.pick_target("A", tried, blocked_descs={"d_untried"})
    assert target == "C"


def test_summary_counts():
    fr = _graph()
    s = fr.summary({"C": {"c_tried"}})
    assert s["screens_known"] == 4
    assert s["edges_known"] == 4
    assert s["screens_with_untried"] == 4  # A/B/D 전부 미시도, C 도 1개 남음


def test_max_actions_per_screen_cap():
    fr = Frontier(max_actions_per_screen=2)
    fr.observe_actions("A", _acts("a", "b", "c"))
    assert len(fr.known_actions["A"]) == 2
