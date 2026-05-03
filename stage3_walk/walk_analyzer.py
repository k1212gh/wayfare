"""Walk diagnostics — walk 끝난 후 자동 생성되는 리포트.

목적: 추측 ('TimePicker dismiss 됐나?') 말고 evidence ('+FAB tap 0회 vs
overflow 18회') 로 root cause 잡기. 사용자가 "왜 더 깊이 안 가지?" 물을 때
즉시 답할 수 있도록.

8 진단 항목:
  1. action_distribution    — 시도된 event_str top N (어느 액션 자주 누름)
  2. fragment_distribution  — fragment 별 state 갯수 (편향 감지)
  3. entry_coverage         — FAB / drawer / overflow / tab 각 추출/시도 횟수
  4. missed_entry_candidates — 화면에 있었지만 actionable 추출 실패한 의심 view
  5. visit_concentration    — 가장 많이 방문된 canonical 의 비율 (stall 가시화)
  6. stall_events           — back / soft_restart / dialog dismiss 횟수
  7. action_distribution_unactionable — clickable=False 인데 의심 패턴 매칭 view
  8. activity_coverage      — manifest declared 중 launched/captured/enriched 분포

Usage:
    from stage3_walk.walk_analyzer import analyze_walk
    report = analyze_walk(Path("workspace/abc"))
    # report["summary"]["root_cause_hints"] 에 evidence-based 가설 N 개
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 의심 패턴 — clickable=False 인데 actionable 일 가능성 (Material/Compose wrapper)
_SUSPICIOUS_NON_CLICKABLE_PATTERNS = (
    "fab", "floatingaction", "extendedfab",
    "iconbutton", "card", "listitem",
    "menuitem", "button",
)
_SUSPICIOUS_DESC_PATTERNS = (
    "add", "create", "new", "compose",
    "play", "start",
    "send", "submit", "save", "done",
)

# Entry type → keyword/class hint (recall 측정용)
_ENTRY_TYPES = {
    "fab": ("fab", "floatingaction", "extendedfab"),
    "drawer": ("drawer", "hamburger", "menu_icon", "drawer_toggle"),
    "overflow": ("overflow", "moreoptions", "more options", "more_vert",
                 "kebab", "three_dot"),
    "bottom_tab": ("bottomnav", "bottomtab", "tab_menu"),
    "settings_entry": ("setting", "preference", "gear", "cog"),
}


def _load_states(tour_dir: Path) -> list[dict]:
    state_dir = tour_dir / "dynamic" / "states"
    if not state_dir.exists():
        return []
    states = []
    for f in sorted(state_dir.glob("state_*.json")):
        try:
            states.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return states


def _load_walk(tour_dir: Path) -> dict:
    p = tour_dir / "dynamic" / "walk.json"
    if not p.exists():
        return {"states": [], "transitions": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"states": [], "transitions": []}


def _load_manifest_scan(tour_dir: Path) -> dict:
    p = tour_dir / "dynamic" / "manifest_scan.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_static(tour_dir: Path) -> dict:
    p = tour_dir / "static" / "analysis.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _classify_entry(view: dict) -> list[str]:
    """view 가 어떤 entry type 후보인지 (복수 가능)."""
    fields = " ".join(str(view.get(k, "")) for k in
                      ("class", "resource_id", "content_desc", "parent_class")).lower()
    matches = []
    for et, kws in _ENTRY_TYPES.items():
        if any(kw in fields for kw in kws):
            matches.append(et)
    return matches


def _entry_coverage(states: list[dict], transitions: list[dict]) -> dict:
    """Entry type 별 추출/시도 분포."""
    # 어느 entry 가 화면에 보였나 (중복 카운트)
    seen: Counter[str] = Counter()
    seen_clickable: Counter[str] = Counter()
    for s in states:
        seen_in_screen: set[str] = set()
        clickable_in_screen: set[str] = set()
        for v in s.get("views", []):
            for et in _classify_entry(v):
                seen_in_screen.add(et)
                if v.get("clickable"):
                    clickable_in_screen.add(et)
        for et in seen_in_screen:
            seen[et] += 1
        for et in clickable_in_screen:
            seen_clickable[et] += 1

    # 어느 entry 가 실제 tap 됐나 (event_str 의 keyword 매칭)
    tapped: Counter[str] = Counter()
    for t in transitions:
        ev = (t.get("event_str") or "").lower()
        for et, kws in _ENTRY_TYPES.items():
            if any(kw in ev for kw in kws):
                tapped[et] += 1

    out = {}
    for et in _ENTRY_TYPES:
        out[et] = {
            "screens_with_view": seen.get(et, 0),
            "screens_with_clickable": seen_clickable.get(et, 0),
            "tapped": tapped.get(et, 0),
        }
    return out


def _missed_entry_candidates(states: list[dict], top: int = 10) -> list[dict]:
    """clickable=False 지만 의심스러운 패턴 매칭하는 view — 즉 actionable 추출
    실패 후보. Material/Compose wrapper 가 자주 여기로 빠짐."""
    by_signature: Counter[str] = Counter()
    samples: dict[str, dict] = {}
    for s in states:
        for v in s.get("views", []):
            if v.get("clickable"):
                continue
            cls = (v.get("class") or "").lower()
            rid = (v.get("resource_id") or "").lower()
            desc = (v.get("content_desc") or "").lower()
            text = (v.get("text") or "").lower()
            haystack = f"{cls} {rid} {desc} {text}"
            matched_pat = next(
                (p for p in _SUSPICIOUS_NON_CLICKABLE_PATTERNS if p in haystack),
                None,
            )
            matched_desc = next(
                (p for p in _SUSPICIOUS_DESC_PATTERNS if p in haystack),
                None,
            )
            if matched_pat or (matched_desc and (cls or rid)):
                key = (v.get("class") or "") + "|" + (v.get("resource_id") or "")[:30]
                by_signature[key] += 1
                if key not in samples:
                    samples[key] = {
                        "class": v.get("class", ""),
                        "resource_id": v.get("resource_id", ""),
                        "content_desc": v.get("content_desc", ""),
                        "text": v.get("text", "")[:30],
                        "bounds": v.get("bounds", ""),
                        "matched_pattern": matched_pat or matched_desc,
                    }
    return [
        {**samples[sig], "occurrences": cnt}
        for sig, cnt in by_signature.most_common(top)
    ]


def _action_distribution(transitions: list[dict], top: int = 20) -> list[dict]:
    counts = Counter(t.get("event_str", "?") for t in transitions)
    return [{"event": k, "count": v} for k, v in counts.most_common(top)]


def _fragment_distribution(states: list[dict]) -> dict:
    counts = Counter(s.get("fragment", "") or "(unknown)" for s in states)
    total = sum(counts.values()) or 1
    return {
        "by_fragment": [
            {"fragment": k, "count": v, "ratio": round(v / total, 3)}
            for k, v in counts.most_common()
        ],
        "total_screens": total,
        "unique_fragments": len(counts),
    }


def _visit_concentration(transitions: list[dict]) -> dict:
    from_screens = Counter(t.get("from_screen", "?") for t in transitions)
    total = sum(from_screens.values()) or 1
    top = from_screens.most_common(5)
    return {
        "top_5_concentration_ratio": round(sum(c for _, c in top) / total, 3),
        "top_5": [{"state": s, "from_count": c} for s, c in top],
        "unique_from_screens": len(from_screens),
    }


def _stall_events(transitions: list[dict]) -> dict:
    """back press / soft_restart / 같은 액션 N번 반복 추정."""
    back_count = sum(
        1 for t in transitions
        if "back" in (t.get("event_str") or "").lower()
        or t.get("event_type") == "back"
    )
    # 같은 event_str 가 연속 N번 — stall 신호
    consecutive_max = 0
    cur = 0
    last = None
    for t in transitions:
        ev = t.get("event_str", "")
        if ev == last:
            cur += 1
            consecutive_max = max(consecutive_max, cur)
        else:
            cur = 1
            last = ev
    return {
        "back_presses": back_count,
        "max_consecutive_same_action": consecutive_max,
    }


def _activity_coverage(states: list[dict], static: dict) -> dict:
    declared = {a.get("name") for a in static.get("activities", []) if a.get("name")}
    visited_activities = {s.get("activity") for s in states if s.get("activity")}
    return {
        "declared": len(declared),
        "visited": len(visited_activities & declared) if declared else None,
        "unvisited_declared": (
            sorted([a for a in declared if a not in visited_activities])[:10]
            if declared else []
        ),
    }


def _root_cause_hints(report: dict) -> list[str]:
    """리포트의 다른 항목 보고 가설 도출. evidence 기반 N개 문장."""
    hints: list[str] = []
    frag = report.get("fragment_distribution", {})
    frag_top = (frag.get("by_fragment") or [{}])[0] if frag.get("by_fragment") else {}
    if frag_top.get("ratio", 0) > 0.4:
        hints.append(
            f"편향: '{frag_top.get('fragment')}' 가 {round(frag_top.get('ratio',0)*100)}% "
            f"({frag_top.get('count')}/{frag.get('total_screens')}) — walker 가 한 fragment 에 갇힘. "
            "score_action 의 NAV_DESC_STRONG 키워드 (특히 'settings') 영향 의심."
        )

    entry = report.get("entry_coverage", {})
    for et, info in entry.items():
        seen = info.get("screens_with_view", 0)
        clk = info.get("screens_with_clickable", 0)
        tap = info.get("tapped", 0)
        if seen >= 3 and clk == 0:
            hints.append(
                f"Entry '{et}': {seen} state 에서 view 보이는데 clickable=true 0개 → "
                "**actionable 추출 실패** (Material/Compose wrapper 의심). "
                "is_actionable() 또는 _find_*_views 강화 필요."
            )
        elif seen >= 5 and tap == 0:
            hints.append(
                f"Entry '{et}': {seen} state 에서 보이고 clickable {clk} 개인데 tap 0회 — "
                "score_action 가 다른 액션에 우선순위 → 이 entry 가 묻혀있음."
            )

    visit = report.get("visit_concentration", {})
    if visit.get("top_5_concentration_ratio", 0) > 0.5:
        hints.append(
            f"Stall 의심: top 5 state 에서 액션 {round(visit['top_5_concentration_ratio']*100)}% "
            "발사 — 같은 화면 계속 누르고 있음. canonical hash 너무 strict 또는 "
            "tried_actions 회복 패턴 부재."
        )

    missed = report.get("missed_entry_candidates", [])
    if missed:
        sigs = [m.get("matched_pattern") or "?" for m in missed[:3]]
        hints.append(
            f"의심 추출 누락: clickable=False 지만 패턴 매칭한 view {len(missed)} 종류 "
            f"(top patterns: {sigs}). 자식 view 의 onClickListener 추출 필요."
        )

    cov = report.get("activity_coverage", {})
    if cov.get("declared") and cov.get("visited") is not None:
        ratio = cov["visited"] / cov["declared"]
        if ratio < 0.3:
            hints.append(
                f"Activity coverage: {cov['visited']}/{cov['declared']} ({round(ratio*100)}%) "
                "— 대부분 declared activity 미방문. walk budget 부족 또는 "
                "entry 발견 실패."
            )

    if not hints:
        hints.append("뚜렷한 anomaly 없음 — 상세 항목 보고 판단.")
    return hints


def analyze_walk(tour_dir: Path) -> dict[str, Any]:
    """Run all 8 diagnostics + summary on a tour directory."""
    states = _load_states(tour_dir)
    exp = _load_walk(tour_dir)
    transitions = exp.get("transitions", [])
    static = _load_static(tour_dir)

    report = {
        "tour_dir": str(tour_dir),
        "totals": {
            "raw_screens": len(states),
            "transitions": len(transitions),
            "walk_screens": len(exp.get("states", [])),
        },
        "action_distribution": _action_distribution(transitions),
        "fragment_distribution": _fragment_distribution(states),
        "entry_coverage": _entry_coverage(states, transitions),
        "missed_entry_candidates": _missed_entry_candidates(states),
        "visit_concentration": _visit_concentration(transitions),
        "stall_events": _stall_events(transitions),
        "activity_coverage": _activity_coverage(states, static),
    }
    report["root_cause_hints"] = _root_cause_hints(report)
    return report


def write_diagnostics(tour_dir: Path) -> Path:
    """analyze + dump to dynamic/diagnostics.json. Stage 3 끝에서 자동 호출용."""
    report = analyze_walk(tour_dir)
    out = tour_dir / "dynamic" / "diagnostics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(
        "[diagnostics] %s — %d hints written to %s",
        tour_dir.name, len(report["root_cause_hints"]), out,
    )
    return out
