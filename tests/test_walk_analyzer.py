"""walk_analyzer 단위 테스트 — 진단 항목 정확성 검증.

검증:
- entry_coverage: FAB 가 view 보이고 clickable 하지만 tap 0 → "never tapped" hint
- entry_coverage: settings_entry 가 view 만 있고 clickable 0 → "extraction failed" hint
- fragment_distribution: 한 fragment 가 50%+ → "편향" hint
- root_cause_hints 가 evidence 기반으로 생성
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stage3_walk.walk_analyzer import (
    analyze_walk, write_diagnostics,
    _classify_entry, _entry_coverage,
    _fragment_distribution, _root_cause_hints,
    _missed_entry_candidates,
)


def _v(cls="View", parent="Root", clickable=False, rid="", text="", desc="", bounds="[0,0][100,50]"):
    return {
        "class": cls, "parent_class": parent, "clickable": clickable,
        "resource_id": rid, "text": text, "content_desc": desc, "bounds": bounds,
    }


def _state(activity="MainActivity", fragment="ALARMS", views=None):
    return {
        "state_str": f"s_{fragment}",
        "structure_str": f"s_{fragment}",
        "activity": activity,
        "fragment": fragment,
        "is_dialog": False,
        "views": views or [],
        "screenshot_path": "",
    }


def _tour_dir(tmp_path: Path, states: list[dict], transitions: list[dict],
             static: dict | None = None) -> Path:
    """Tmp workspace 하나 만들기 — walk_analyzer 입력 구조 재현."""
    tour = tmp_path / "tour_xyz"
    (tour / "dynamic" / "states").mkdir(parents=True)
    for i, s in enumerate(states):
        (tour / "dynamic" / "states" / f"state_{i:04d}.json").write_text(
            json.dumps(s, ensure_ascii=False), encoding="utf-8")
    (tour / "dynamic" / "walk.json").write_text(
        json.dumps({"states": states, "transitions": transitions}, ensure_ascii=False),
        encoding="utf-8")
    if static:
        (tour / "static").mkdir(parents=True)
        (tour / "static" / "analysis.json").write_text(
            json.dumps(static, ensure_ascii=False), encoding="utf-8")
    return tour


# ─── _classify_entry ────────────────────────────────────


def test_classify_entry_fab():
    assert "fab" in _classify_entry({"class": "FloatingActionButton"})
    assert "fab" in _classify_entry({"resource_id": "fab_add"})
    assert "fab" in _classify_entry({"parent_class": "ExtendedFAB"})


def test_classify_entry_overflow():
    assert "overflow" in _classify_entry({"resource_id": "overflow_action_button"})
    assert "overflow" in _classify_entry({"content_desc": "More options"})


def test_classify_entry_settings():
    assert "settings_entry" in _classify_entry({"content_desc": "Settings"})
    assert "settings_entry" in _classify_entry({"class": "PreferenceCategory"})


# ─── entry_coverage ─────────────────────────────────────


def test_entry_coverage_fab_seen_but_never_tapped():
    """FAB clickable view 가 여러 state 에 보이지만 transitions 에 tap 0개 → tapped=0."""
    fab_view = _v(cls="FloatingActionButton", clickable=True,
                  rid="fab_add", desc="Add alarm")
    states = [
        _state(views=[fab_view, _v(cls="Button", clickable=True, text="Other")]),
        _state(views=[fab_view, _v(cls="Button", clickable=True, text="Other")]),
        _state(views=[fab_view]),
    ]
    transitions = [
        {"from_screen": "s_0", "to_screen": "s_1", "event_type": "click",
         "event_str": "click Button@[10,10][50,30]"},
    ]
    cov = _entry_coverage(states, transitions)
    assert cov["fab"]["screens_with_view"] == 3
    assert cov["fab"]["screens_with_clickable"] == 3
    assert cov["fab"]["tapped"] == 0


def test_entry_coverage_fab_extraction_failed():
    """FAB view 보이지만 clickable=False → screens_with_clickable=0 = 추출 실패."""
    fab_view = _v(cls="ViewGroup", clickable=False, rid="fab_container")
    states = [_state(views=[fab_view]) for _ in range(5)]
    cov = _entry_coverage(states, [])
    assert cov["fab"]["screens_with_view"] == 5
    assert cov["fab"]["screens_with_clickable"] == 0


def test_entry_coverage_overflow_tapped():
    """Overflow 가 tap 됐을 때 tapped count 증가."""
    states = [_state(views=[_v(cls="ImageButton", rid="overflow_action_button",
                               clickable=True, desc="More options")])]
    transitions = [
        {"event_str": "click overflow_action_button@[100,0][150,50]"},
        {"event_str": "click overflow_action_button@[100,0][150,50]"},
    ]
    cov = _entry_coverage(states, transitions)
    assert cov["overflow"]["tapped"] == 2


# ─── fragment_distribution ──────────────────────────────


def test_fragment_distribution_bias_detected():
    """한 fragment 가 50%+ → ratio > 0.4."""
    states = (
        [_state(fragment="prefs_fragment") for _ in range(50)] +
        [_state(fragment="ALARMS") for _ in range(5)] +
        [_state(fragment="STOPWATCH") for _ in range(5)]
    )
    fd = _fragment_distribution(states)
    assert fd["total_screens"] == 60
    assert fd["by_fragment"][0]["fragment"] == "prefs_fragment"
    assert fd["by_fragment"][0]["ratio"] > 0.5


# ─── missed_entry_candidates ────────────────────────────


def test_missed_entry_candidates_finds_fab_container():
    """clickable=False 인데 'fab' 패턴 매칭 view → missed candidate 으로."""
    states = [_state(views=[_v(cls="ViewGroup", clickable=False, rid="fab_container")])
              for _ in range(10)]
    me = _missed_entry_candidates(states)
    assert len(me) >= 1
    assert any("fab" in (m["resource_id"] or "") for m in me)


# ─── root_cause_hints ───────────────────────────────────


def test_root_cause_hints_fab_never_tapped():
    """FAB seen+clickable but never tapped → hint 생성."""
    fab_view = _v(cls="FloatingActionButton", clickable=True, rid="fab",
                  desc="Add alarm")
    states = [_state(views=[fab_view]) for _ in range(10)]
    report = {
        "fragment_distribution": {"by_fragment": [], "total_screens": 10},
        "entry_coverage": _entry_coverage(states, []),
        "visit_concentration": {"top_5_concentration_ratio": 0.0, "top_5": []},
        "missed_entry_candidates": [],
        "activity_coverage": {},
    }
    hints = _root_cause_hints(report)
    assert any("fab" in h.lower() and "tap" in h.lower() for h in hints)


def test_root_cause_hints_settings_extraction_failure():
    """settings_entry 가 view 만 있고 clickable 0 → extraction failed hint."""
    settings_view = _v(cls="PreferenceCategory", clickable=False, desc="Settings")
    states = [_state(views=[settings_view]) for _ in range(5)]
    report = {
        "fragment_distribution": {"by_fragment": [], "total_screens": 5},
        "entry_coverage": _entry_coverage(states, []),
        "visit_concentration": {"top_5_concentration_ratio": 0.0, "top_5": []},
        "missed_entry_candidates": [],
        "activity_coverage": {},
    }
    hints = _root_cause_hints(report)
    assert any("settings" in h.lower() and "추출 실패" in h for h in hints)


def test_root_cause_hints_fragment_bias():
    """한 fragment 50%+ 이면 편향 hint."""
    states = ([_state(fragment="prefs_fragment") for _ in range(50)] +
              [_state(fragment="ALARMS") for _ in range(10)])
    report = {
        "fragment_distribution": _fragment_distribution(states),
        "entry_coverage": {},
        "visit_concentration": {"top_5_concentration_ratio": 0.0, "top_5": []},
        "missed_entry_candidates": [],
        "activity_coverage": {},
    }
    hints = _root_cause_hints(report)
    assert any("편향" in h or "갇힘" in h for h in hints)


# ─── analyze_walk end-to-end ────────────────────


def test_analyze_walk_e2e(tmp_path: Path):
    """Whole pipeline — fixture 만들고 analyze 후 hints 검증."""
    fab_view = _v(cls="FloatingActionButton", clickable=True, rid="fab",
                  desc="Add alarm")
    states = ([_state(fragment="prefs_fragment", views=[fab_view]) for _ in range(50)] +
              [_state(fragment="ALARMS", views=[fab_view]) for _ in range(10)])
    transitions = [
        {"from_screen": "s_0", "to_screen": "s_1", "event_type": "click",
         "event_str": "click overflow_button@[100,0][150,50]"}
        for _ in range(15)
    ]
    tour_dir = _tour_dir(tmp_path, states, transitions, static={"activities": []})

    report = analyze_walk(tour_dir)
    assert report["totals"]["raw_screens"] == 60
    assert report["totals"]["transitions"] == 15
    assert report["entry_coverage"]["fab"]["tapped"] == 0
    assert report["entry_coverage"]["overflow"]["tapped"] == 15
    assert len(report["root_cause_hints"]) >= 2
    assert any("prefs_fragment" in h or "편향" in h for h in report["root_cause_hints"])


def test_write_diagnostics_creates_file(tmp_path: Path):
    """write_diagnostics 가 dynamic/diagnostics.json 생성."""
    states = [_state(views=[])]
    tour_dir = _tour_dir(tmp_path, states, [])
    out = write_diagnostics(tour_dir)
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "root_cause_hints" in data
    assert "entry_coverage" in data
