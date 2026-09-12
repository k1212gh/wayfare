"""Frontier — 앱 전역 미탐색 액션 큐 + 관측 전이 그래프 경로 계획.

LLM-Explorer (Zhao et al., MobiCom 2025, arXiv 2505.10593) 의 AIG
(Abstract Interaction Graph) 액션 선택기를 차용한 구현.

  1. 현재 화면의 미시도 액션을 우선한다 (기존 score 의 +4.0 이 담당).
  2. 현재 화면에 미시도 액션이 없으면 앱 전체에서 미시도 액션이 남은
     화면(canonical) 중 가장 가까운 것을 고르고,
  3. 지금까지 관측한 전이 그래프 위 BFS 최단경로로 그 화면까지 이동한다.

기존 TapWalker 는 이 상황에서 Back → soft-restart 로 처음부터 다시 왔고,
그 결과 같은 상위 화면을 반복 방문하며 시간을 썼다. Frontier 는 '아는 길'
로 미탐색 지점까지 되돌아간다. LLM 호출 없음, 순수 그래프 연산.

활성화: 환경변수 WALK_FRONTIER=1 (기본 off — baseline 동작 보존).
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


class Frontier:
    """관측 기반 탐색 프런티어.

    known_actions[canonical][desc]   — 그 화면에서 발견한 액션 (desc 는
                                       ViewTreeReader.get_action_desc 결과)
    edges[from_canonical][desc]      — 그 액션이 마지막으로 이끈 화면
    target_failures[canonical]       — 경로 재현 실패 횟수 (과다 시 제외)
    """

    def __init__(
        self,
        max_path_len: int = 6,
        max_fail_per_target: int = 2,
        max_actions_per_screen: int = 80,
    ):
        self.max_path_len = max_path_len
        self.max_fail_per_target = max_fail_per_target
        self.max_actions_per_screen = max_actions_per_screen
        self.known_actions: dict[str, dict[str, dict]] = defaultdict(dict)
        self.edges: dict[str, dict[str, str]] = defaultdict(dict)
        self.target_failures: dict[str, int] = defaultdict(int)
        self.stats: dict[str, int] = {
            "nav_attempts": 0,
            "nav_success": 0,
            "nav_failed": 0,
            "nav_steps": 0,
            "targets_exhausted": 0,
            "no_target": 0,
        }

    # ── 관측 ──────────────────────────────────────────────────────

    def observe_actions(self, canonical: str, actions: list[dict]) -> None:
        """화면에서 점수화된 액션 목록을 기록 (desc 기준, 중복 무시)."""
        if not canonical:
            return
        bucket = self.known_actions[canonical]
        for a in actions:
            desc = a.get("desc") or ""
            if not desc or desc in bucket:
                continue
            if len(bucket) >= self.max_actions_per_screen:
                break
            bucket[desc] = {"action": a.get("action", "click"), "desc": desc}

    def observe_transition(self, frm: str, desc: str, to: str) -> None:
        """액션 실행 결과 전이를 기록. self-loop 는 경로 계획에 무의미하므로 제외."""
        if not frm or not to or not desc or frm == to:
            return
        self.edges[frm][desc] = to

    # ── 조회 ──────────────────────────────────────────────────────

    def untried(
        self,
        canonical: str,
        tried: set[str] | frozenset[str],
        blocked_descs: set[str] | frozenset[str] = frozenset(),
    ) -> list[str]:
        """해당 화면에서 아직 시도하지 않은(그리고 차단되지 않은) 액션 desc."""
        return [
            d for d in self.known_actions.get(canonical, {})
            if d not in tried and d not in blocked_descs
        ]

    def is_exhausted(self, canonical: str) -> bool:
        return self.target_failures.get(canonical, 0) >= self.max_fail_per_target

    def mark_failure(self, target: str) -> None:
        self.target_failures[target] += 1
        self.stats["nav_failed"] += 1
        if self.is_exhausted(target):
            self.stats["targets_exhausted"] += 1
            logger.info("[frontier] target %s exhausted after %d failures",
                        target, self.target_failures[target])

    def bfs_path(self, src: str, dst: str) -> list[tuple[str, str, str]] | None:
        """src → dst 최단경로. 반환: [(from, desc, to), ...]. 없으면 None."""
        if src == dst:
            return []
        prev: dict[str, tuple[str, str]] = {}
        seen = {src}
        q: deque[tuple[str, int]] = deque([(src, 0)])
        while q:
            node, depth = q.popleft()
            if depth >= self.max_path_len:
                continue
            for desc, nxt in self.edges.get(node, {}).items():
                if nxt in seen:
                    continue
                seen.add(nxt)
                prev[nxt] = (node, desc)
                if nxt == dst:
                    return self._rebuild(prev, src, dst)
                q.append((nxt, depth + 1))
        return None

    def pick_target(
        self,
        src: str,
        tried_actions: dict[str, set[str]],
        blocked_descs: set[str] | frozenset[str] = frozenset(),
        blocked_canonicals: set[str] | frozenset[str] = frozenset(),
    ) -> tuple[str, list[tuple[str, str, str]]] | None:
        """src 에서 도달 가능한 화면 중 미시도 액션이 남은 가장 가까운 화면.

        BFS 순서가 곧 거리 순서이므로 처음 만나는 후보가 최단 거리.
        반환: (target_canonical, path) 또는 None.
        """
        prev: dict[str, tuple[str, str]] = {}
        seen = {src}
        q: deque[tuple[str, int]] = deque([(src, 0)])
        while q:
            node, depth = q.popleft()
            if depth >= self.max_path_len:
                continue
            for desc, nxt in self.edges.get(node, {}).items():
                if nxt in seen:
                    continue
                seen.add(nxt)
                prev[nxt] = (node, desc)
                if (
                    nxt not in blocked_canonicals
                    and not self.is_exhausted(nxt)
                    and self.untried(nxt, tried_actions.get(nxt, set()), blocked_descs)
                ):
                    return nxt, self._rebuild(prev, src, nxt)
                q.append((nxt, depth + 1))
        self.stats["no_target"] += 1
        return None

    def screens_with_untried(
        self,
        tried_actions: dict[str, set[str]],
        blocked_descs: set[str] | frozenset[str] = frozenset(),
    ) -> list[str]:
        """미시도 액션이 남은 화면 목록 (진단용)."""
        return [
            c for c in self.known_actions
            if self.untried(c, tried_actions.get(c, set()), blocked_descs)
        ]

    def summary(self, tried_actions: dict[str, set[str]] | None = None) -> dict:
        out = dict(self.stats)
        out["screens_known"] = len(self.known_actions)
        out["edges_known"] = sum(len(v) for v in self.edges.values())
        if tried_actions is not None:
            out["screens_with_untried"] = len(self.screens_with_untried(tried_actions))
        return out

    # ── 내부 ──────────────────────────────────────────────────────

    @staticmethod
    def _rebuild(prev: dict[str, tuple[str, str]], src: str, dst: str) -> list[tuple[str, str, str]]:
        path: list[tuple[str, str, str]] = []
        cur = dst
        while cur != src:
            node, desc = prev[cur]
            path.append((node, desc, cur))
            cur = node
        path.reverse()
        return path
