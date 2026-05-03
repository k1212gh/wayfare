"""Walk metrics — 매 walk 끝에 가중치/stall pattern 자동 기록.

목적: cycle 별 사람이 evidence 보고 score_action 가중치 조정 (Cycle 1, 2, 3
처럼) 의 데이터 기반. 다음 cycle 의 fix 근거를 자동으로 모음.

기록 항목 (workspace/{tour}/dynamic/walk_metrics.json):
  1. score_distribution     — 액션별 score (count/max/min/mean) + 가중치 분해
  2. stall_canonicals       — top from-state 의 액션 분포 + untried views 목록
  3. rid_tap_frequency      — clickable rid 의 등장 vs tap 비율 (0% tap = stall signal)
  4. fragment_dwell         — fragment 별 체류 events
  5. exhaustion_events      — 한 canonical 의 모든 actionable 시도 후 다음 액션
  6. cycle_recommendations  — 다음 가중치 조정 자동 제안 (예: "rid X 0% tap → bonus +2 검토")

사용:
    from .walk_metrics import WalkMetrics
    m = WalkMetrics()
    m.record_event(canonical, action_desc, score_breakdown, view)
    m.record_exhaustion(canonical, untried_views)
    m.write(tour_dir)
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class WalkMetrics:
    """Stage 3 의 score / stall / rid 패턴 기록기.

    tap_walker 메인 루프에서 매 액션 시도마다 ``record_event`` 호출.
    walk 끝에 ``write(tour_dir)`` 로 ``walk_metrics.json`` 저장.
    """

    def __init__(self):
        # 액션 desc → list[score breakdown]
        self.scores: dict[str, list[dict]] = defaultdict(list)
        # canonical → list[(event_idx, action_desc)]
        self.events_by_canonical: dict[str, list[tuple[int, str]]] = defaultdict(list)
        # rid → (seen_count, tapped_count)
        self.rid_seen: Counter = Counter()
        self.rid_tapped: Counter = Counter()
        # fragment → events count
        self.fragment_events: Counter = Counter()
        # exhaustion events
        self.exhaustions: list[dict] = []
        # 전체 events
        self.event_count = 0

    def record_event(
        self,
        event_idx: int,
        canonical: str,
        action_desc: str,
        score: float,
        view: dict | None = None,
        score_breakdown: dict | None = None,
    ) -> None:
        """매 액션 시도마다 호출."""
        self.event_count = max(self.event_count, event_idx + 1)
        # action_type 만 (bounds 제외) 으로 grouping
        action_type = action_desc.split("@")[0] if "@" in action_desc else action_desc
        self.scores[action_type].append({
            "event": event_idx,
            "canonical": canonical,
            "score": round(score, 2),
            "breakdown": score_breakdown or {},
        })
        self.events_by_canonical[canonical].append((event_idx, action_desc))

    def record_view_seen(self, view: dict) -> None:
        """캡처 시 본 view 의 rid 기록 (clickable 만 의미)."""
        if not view.get("clickable"):
            return
        rid = (view.get("resource_id") or "").lower()
        if rid:
            self.rid_seen[rid] += 1

    def record_view_tapped(self, view: dict) -> None:
        rid = (view.get("resource_id") or "").lower()
        if rid:
            self.rid_tapped[rid] += 1

    def record_fragment(self, fragment: str) -> None:
        self.fragment_events[fragment or "(none)"] += 1

    def record_exhaustion(
        self,
        canonical: str,
        untried_views: list[dict],
    ) -> None:
        """한 canonical 의 모든 actionable 시도 후 호출."""
        self.exhaustions.append({
            "event": self.event_count,
            "canonical": canonical,
            "untried_count": len(untried_views),
            "untried_samples": [
                {
                    "rid": v.get("resource_id", "")[:30],
                    "label": v.get("content_desc", "") or v.get("text", "")[:30],
                } for v in untried_views[:5]
            ],
        })

    # ─── 분석 ──────────────────────────────────────

    def _score_distribution(self) -> dict:
        out = {}
        for action_type, events in self.scores.items():
            scores = [e["score"] for e in events]
            if not scores:
                continue
            out[action_type] = {
                "count": len(scores),
                "max": max(scores),
                "min": min(scores),
                "mean": round(sum(scores) / len(scores), 2),
                "score_unchanged": (max(scores) - min(scores)) < 0.1,  # canonical reset 신호
            }
        return out

    def _stall_canonicals(self, top_n: int = 5) -> list[dict]:
        canonical_event_counts = {
            c: len(events) for c, events in self.events_by_canonical.items()
        }
        total = sum(canonical_event_counts.values()) or 1
        top = sorted(
            canonical_event_counts.items(), key=lambda x: -x[1]
        )[:top_n]
        return [
            {
                "canonical": c,
                "events": n,
                "ratio": round(n / total, 3),
                "is_stall": n / total > 0.2,
            } for c, n in top
        ]

    def _rid_tap_frequency(self, top_n: int = 20) -> list[dict]:
        out = []
        for rid, seen in self.rid_seen.most_common(top_n):
            tapped = self.rid_tapped.get(rid, 0)
            tap_ratio = tapped / seen if seen else 0
            out.append({
                "rid": rid[:40],
                "seen": seen,
                "tapped": tapped,
                "tap_ratio": round(tap_ratio, 3),
                "is_zero_tap": (seen >= 5 and tapped == 0),  # stall signal
            })
        return out

    def _cycle_recommendations(self, score_dist: dict, rid_freq: list[dict]) -> list[str]:
        """자동 제안 — 다음 cycle 의 가중치 fix 근거."""
        recs = []
        # 1. 같은 score max=min → canonical reset 또는 visit_count 미반영
        for at, s in score_dist.items():
            if s["count"] >= 5 and s["score_unchanged"]:
                recs.append(
                    f"⚠ '{at}' score {s['max']} 매번 동일 (count={s['count']}) — "
                    "canonical_id reset 의심 또는 visit_count 페널티 미반영"
                )
        # 2. seen 많지만 tap 0 — 가중치 부족
        zero_tap = [r for r in rid_freq if r["is_zero_tap"]]
        for r in zero_tap[:5]:
            recs.append(
                f"💡 rid '{r['rid']}' seen={r['seen']} tapped=0 → 보너스 +N 검토"
            )
        # 3. score 가장 높은 액션 비율
        top_score_action = max(score_dist.items(), key=lambda x: x[1]["mean"], default=(None, None))
        if top_score_action[0]:
            at, s = top_score_action
            if s["count"] / max(1, self.event_count) > 0.3:
                recs.append(
                    f"📊 '{at}' 가 액션의 {round(s['count']/self.event_count*100)}% — "
                    "score {s['mean']} 너무 압도. 강등 검토"
                )
        if not recs:
            recs.append("✅ 명확한 stall 패턴 없음")
        return recs

    def to_dict(self) -> dict[str, Any]:
        score_dist = self._score_distribution()
        rid_freq = self._rid_tap_frequency()
        return {
            "totals": {
                "events": self.event_count,
                "unique_canonicals": len(self.events_by_canonical),
                "exhaustions": len(self.exhaustions),
            },
            "score_distribution": score_dist,
            "stall_canonicals": self._stall_canonicals(),
            "rid_tap_frequency": rid_freq,
            "fragment_dwell": [
                {"fragment": f, "events": n}
                for f, n in self.fragment_events.most_common()
            ],
            "exhaustion_events": self.exhaustions[-20:],  # 최근 20만
            "cycle_recommendations": self._cycle_recommendations(score_dist, rid_freq),
        }

    def write(self, tour_dir: Path) -> Path:
        out = Path(tour_dir) / "dynamic" / "walk_metrics.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(
            "[walk_metrics] written %s — events=%d uniq_canonicals=%d "
            "exhaustions=%d recs=%d",
            out, self.event_count, len(self.events_by_canonical),
            len(self.exhaustions), len(self._cycle_recommendations(
                self._score_distribution(), self._rid_tap_frequency())),
        )
        return out
