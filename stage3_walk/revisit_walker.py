"""Pass 2 Walker — Stage 5 LLM 끝 후 재탐색.

원칙 (wayfare_completeness_principle 메모리):
  Pass 1 (Stage 3) 가 quality fail 화면 만나면 즉시 vision 호출하지 않고
  state.needs_vision_in_revisit=True 마킹만 → ScreenMap node.is_provisional=True.
  Pass 2 가 ScreenMap 풍부해진 (LLM description / completeness) 상태에서
  provisional 노드 entry 로 재탐색.

흐름:
  1. ScreenMap 로드 → provisional 노드 list 추출
  2. 각 provisional 노드 까지 path BFS (ScreenMap edges 따라)
  3. emulator 에서 path 액션 replay (action_history 의 event_str)
  4. 도달 시 vision_tapper.extract_actionable() — 좌표 받음
  5. top action click → 결과 capture → ScreenMap 새 노드/edges 합성

비용 통제:
  - vision_tapper 가 자체 budget cap (env: VISION_BUDGET=10)
  - state_str cache — 같은 화면 재진입 시 재사용
  - error 화면 (label='웹페이지 오류' 등) skip — 의미 없음

이 모듈은 Phase 1 — wireframe (ScreenMap load + provisional list + report) 만.
실제 path replay + vision click 통합은 Phase 2.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Skip 패턴 — vision 으로도 의미 없는 화면 (앱 자체 오류 등)
ERROR_LABEL_PATTERNS = (
    "웹페이지 오류", "웹페이지 로드 오류", "Page not found",
    "Network error", "Connection error", "오류", "Error",
)


class RevisitWalker:
    """ScreenMap 의 provisional 노드 entry 로 재탐색 — vision_tapper fallback 활용."""

    def __init__(self, screenmap_path: str | Path, vision_tapper: Any = None):
        self.screenmap_path = Path(screenmap_path)
        self.vision_tapper = vision_tapper
        self.screenmap: dict | None = None
        self.provisional_nodes: list[dict] = []
        self.skipped_error: list[dict] = []
        # 통계
        self.stats = {
            "provisional_total": 0,
            "skipped_error": 0,
            "actionable_candidates": 0,
            "vision_calls": 0,
            "new_edges_synthesized": 0,
        }

    def load(self) -> bool:
        """ScreenMap 로드 + provisional 노드 식별."""
        if not self.screenmap_path.exists():
            logger.warning("RevisitWalker: ScreenMap missing at %s", self.screenmap_path)
            return False
        try:
            self.screenmap = json.loads(self.screenmap_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("RevisitWalker: ScreenMap load failed: %s", e)
            return False
        nodes = self.screenmap.get("screen_map", {}).get("graph", {}).get("nodes", []) or []
        provisional = [n for n in nodes if n.get("is_provisional")]
        # Error 화면 분리 — vision 도 의미 없음
        clean = []
        for n in provisional:
            label = (n.get("label") or "").lower()
            if any(p.lower() in label for p in ERROR_LABEL_PATTERNS):
                self.skipped_error.append(n)
            else:
                clean.append(n)
        self.provisional_nodes = clean
        self.stats["provisional_total"] = len(provisional)
        self.stats["skipped_error"] = len(self.skipped_error)
        logger.info(
            "RevisitWalker: loaded — provisional %d (clean %d, error %d)",
            len(provisional), len(clean), len(self.skipped_error),
        )
        return True

    def find_entry_path(self, target_screen_id: str) -> list[dict]:
        """ScreenMap entry 에서 target 까지 BFS path. 각 step = edge dict.

        Returns: [edge1, edge2, ...] — path replay 시 순차 실행할 edges.
        빈 list = unreachable.
        """
        if not self.screenmap:
            return []
        graph = self.screenmap.get("screen_map", {}).get("graph", {}) or {}
        nodes = graph.get("nodes", []) or []
        edges = graph.get("edges", []) or []
        entry = graph.get("entry_node", "") or "system:external_entry"

        # Adjacency
        from collections import defaultdict, deque
        adj: dict[str, list[dict]] = defaultdict(list)
        for e in edges:
            adj[e.get("from", "")].append(e)

        # BFS
        visited = {entry}
        queue: deque[tuple[str, list[dict]]] = deque([(entry, [])])
        while queue:
            cur, path = queue.popleft()
            if cur == target_screen_id:
                return path
            for e in adj.get(cur, []):
                nxt = e.get("to", "")
                if nxt and nxt not in visited:
                    visited.add(nxt)
                    queue.append((nxt, path + [e]))
        return []

    def report(self) -> dict[str, Any]:
        """Phase 1 — provisional 노드 list + 각자 entry path 가능 여부.

        실제 vision 호출 / click 안 함. 보고만.
        """
        if not self.screenmap:
            self.load()
        report_items = []
        reachable = unreachable = 0
        for n in self.provisional_nodes:
            sid = n.get("screen_id", "")
            path = self.find_entry_path(sid)
            if path:
                reachable += 1
            else:
                unreachable += 1
            report_items.append({
                "screen_id": sid,
                "activity": (n.get("activity", "") or "").rsplit(".", 1)[-1],
                "label": (n.get("label") or "")[:40],
                "category": n.get("functional_category", ""),
                "path_length": len(path),
                "reachable": bool(path),
            })
        return {
            "stats": dict(self.stats, reachable=reachable, unreachable=unreachable),
            "skipped_error_labels": [
                (n.get("label") or "")[:40] for n in self.skipped_error
            ],
            "items": report_items,
        }

    def write_report(self, output_path: str | Path) -> Path:
        """Report 를 json 파일로 저장."""
        report = self.report()
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("RevisitWalker report written: %s", out)
        return out
