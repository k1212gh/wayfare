"""Phase B (2026-04-29) — task fixture loader + coverage evaluator 단위 테스트.

검증 대상:
- task_fixture.load_fixture(): YAML 로드 + 결측 처리
- task_fixture.evaluate_task_coverage(): expected_screens 매칭 / missing 추적
- metadata_refresh: app_name 추론 + task_coverage 채움 / fixture 없으면 None
"""

from __future__ import annotations

from stage6_screenmap.task_fixture import (
    evaluate_task_coverage,
    load_fixture,
    _node_matches,
)
from stage6_screenmap.metadata_refresh import refresh_metadata, _infer_app_name


# ─── _node_matches (단위) ──────────────────────────────────


def test_node_matches_activity_substr_any_of():
    node = {"activity": "com.android.deskclock.AlarmsActivity"}
    assert _node_matches(node, {"activity_substr": ["AlarmsActivity"]}) is True
    assert _node_matches(node, {"activity_substr": ["TimerActivity", "AlarmsActivity"]}) is True
    assert _node_matches(node, {"activity_substr": ["WrongActivity"]}) is False


def test_node_matches_functional_category_exact_any_of():
    node = {"activity": "x", "functional_category": "list"}
    assert _node_matches(node, {"functional_category": ["list"]}) is True
    assert _node_matches(node, {"functional_category": ["form", "list"]}) is True
    assert _node_matches(node, {"functional_category": ["home"]}) is False


def test_node_matches_either_signal():
    """둘 중 하나만 맞아도 OK (OR 매칭)."""
    node = {"activity": "Foo", "functional_category": "list"}
    rule = {"activity_substr": ["Bar"], "functional_category": ["list"]}
    assert _node_matches(node, rule) is True


def test_node_matches_empty_rule_false():
    assert _node_matches({"activity": "x"}, {}) is False


# ─── B (2026-05-03) — text_substr 매칭 ─────────────────────


def test_node_matches_text_substr_label():
    """label 에 text_substr 부분일치 → 매칭."""
    node = {"activity": "WebActivity", "label": "메뉴 옵션 선택"}
    assert _node_matches(node, {"text_substr": ["메뉴 옵션"]}) is True
    assert _node_matches(node, {"text_substr": ["옵션"]}) is True
    assert _node_matches(node, {"text_substr": ["없는단어"]}) is False


def test_node_matches_text_substr_screen_purpose():
    """screen_purpose 에 매칭 — Stage 5 LLM annotation 산물."""
    node = {"activity": "WebActivity",
            "screen_purpose": "사용자가 매장 위치를 검색하고 길찾기를 한다."}
    assert _node_matches(node, {"text_substr": ["매장"]}) is True
    assert _node_matches(node, {"text_substr": ["길찾기"]}) is True


def test_node_matches_text_substr_case_insensitive():
    node = {"label": "MENU 옵션"}
    assert _node_matches(node, {"text_substr": ["menu"]}) is True


def test_node_matches_text_substr_combines_with_other_rules():
    """text_substr 매칭은 OR semantics — activity_substr 안 맞아도 통과."""
    node = {"activity": "Foo", "label": "장바구니"}
    rule = {
        "activity_substr": ["CartActivity"],   # not match
        "text_substr": ["장바구니"],            # match
    }
    assert _node_matches(node, rule) is True


def test_node_matches_text_substr_empty_list_no_op():
    """text_substr 가 빈 리스트면 무영향 (다른 룰만 평가)."""
    node = {"activity": "AlarmsActivity"}
    rule = {"activity_substr": ["AlarmsActivity"], "text_substr": []}
    assert _node_matches(node, rule) is True


def test_node_matches_text_substr_no_text_fields_safe():
    """text_substr 룰이 있는데 노드에 text 필드 모두 없어도 crash 안 함."""
    node = {"activity": "A"}
    assert _node_matches(node, {"text_substr": ["메뉴"]}) is False


def test_megacoffee_fixture_text_substr_loads():
    """B 적용된 megacoffee.yaml 가 valid YAML + text_substr 룰 들어있는지."""
    fx = load_fixture("megacoffee")
    assert fx is not None
    by_id = {t["id"]: t for t in fx.get("tasks", [])}
    find_store = by_id["find_store"]
    rule = find_store["expected_screens"][0]["match_any"]
    assert "매장찾기" in rule.get("text_substr", []), \
        "find_store 에 매장찾기 text_substr 있어야"
    membership = by_id["view_membership"]
    rule = membership["expected_screens"][0]["match_any"]
    assert "마이페이지" in rule.get("text_substr", []), \
        "view_membership 에 마이페이지 text_substr 있어야"


def test_extract_keywords_includes_text_substr_terms():
    """C (2026-05-03): extract_keywords 가 expected_screens.match_any.text_substr
    까지 봐야 — fixture text_substr 에만 있는 단어 (메가오더 등) 도 walk
    keyword 화이트리스트에 들어가야 한다."""
    from stage6_screenmap.task_fixture import extract_keywords
    fx = load_fixture("megacoffee")
    kws = set(extract_keywords(fx))
    # text_substr 에만 있는 단어들 — description/goal 에 없음
    assert "메가오더" in kws, "text_substr 의 메가오더 추출 안 됨"
    assert "매장찾기" in kws
    assert "마이페이지" in kws
    assert "스탬프" in kws


def test_megacoffee_webview_node_matches_via_text():
    """메가커피 잡 8cbd896a 의 실 노드 — WebActivity sub-node 가
    text_substr 로 task 매칭되는지 검증.
    label='스탬프 적립 현황' → view_membership 의 '스탬프 적립' 매칭."""
    node = {
        "activity": "co.kr.waldlust.megacoffee.ui.webkit.WebActivity",
        "functional_category": "detail",
        "label": "스탬프 적립 현황",
        "screen_purpose": "사용자가 현재 보유한 스탬프 개수와 무료 쿠폰 획득",
    }
    fx = load_fixture("megacoffee")
    assert fx is not None
    by_id = {t["id"]: t for t in fx.get("tasks", [])}
    rule = by_id["view_membership"]["expected_screens"][0]["match_any"]
    # activity_substr 는 안 맞음 (MembershipActivity 아님)
    assert not any(s in node["activity"] for s in rule.get("activity_substr", []))
    # 그러나 text_substr 로 매칭됨
    assert _node_matches(node, rule) is True


# ─── evaluate_task_coverage ────────────────────────────────


def test_evaluate_all_feasible():
    nodes = [
        {"activity": "com.x.AlarmsActivity", "functional_category": "list"},
        {"activity": "com.x.AlarmEditActivity", "functional_category": "form"},
    ]
    fixture = {
        "tasks": [{
            "id": "add_alarm",
            "expected_screens": [
                {"description": "list", "match_any": {"activity_substr": ["AlarmsActivity"]}},
                {"description": "edit", "match_any": {"activity_substr": ["AlarmEditActivity"]}},
            ],
        }],
    }
    r = evaluate_task_coverage(nodes, fixture)
    assert r["total"] == 1 and r["feasible"] == 1 and r["ratio"] == 1.0
    assert r["tasks"][0]["feasible"] is True
    assert r["tasks"][0]["missing"] == []


def test_evaluate_partial_missing_screen():
    nodes = [{"activity": "com.x.AlarmsActivity"}]  # edit 화면 없음
    fixture = {
        "tasks": [{
            "id": "add_alarm",
            "expected_screens": [
                {"description": "list", "match_any": {"activity_substr": ["AlarmsActivity"]}},
                {"description": "edit", "match_any": {"activity_substr": ["AlarmEditActivity"]}},
            ],
        }],
    }
    r = evaluate_task_coverage(nodes, fixture)
    assert r["feasible"] == 0 and r["ratio"] == 0.0
    assert r["tasks"][0]["missing"] == ["edit"]


def test_evaluate_mixed():
    nodes = [
        {"activity": "com.x.AlarmsActivity"},
        {"activity": "com.x.OtherActivity"},
    ]
    fixture = {
        "tasks": [
            {"id": "t1", "expected_screens": [
                {"description": "a", "match_any": {"activity_substr": ["AlarmsActivity"]}},
            ]},
            {"id": "t2", "expected_screens": [
                {"description": "b", "match_any": {"activity_substr": ["MissingActivity"]}},
            ]},
        ],
    }
    r = evaluate_task_coverage(nodes, fixture)
    assert r["total"] == 2 and r["feasible"] == 1 and r["ratio"] == 0.5


def test_evaluate_empty_fixture():
    r = evaluate_task_coverage([], {"tasks": []})
    assert r["total"] == 0 and r["ratio"] is None


# ─── load_fixture ──────────────────────────────────────────


def test_load_fixture_returns_none_for_unknown():
    assert load_fixture("nonexistent_app_12345") is None


def test_load_fixture_none_for_empty_app():
    assert load_fixture(None) is None
    assert load_fixture("") is None


def test_load_real_deskclock_fixture():
    """tests/fixtures/deskclock.yaml 가 valid YAML + 10 task 있는지.
    기본 5 + 까다로운 5 (일부는 honest miss 기대)."""
    fx = load_fixture("deskclock")
    assert fx is not None, "deskclock.yaml 없음"
    assert fx.get("app") == "deskclock"
    tasks = fx.get("tasks", [])
    assert len(tasks) == 10, f"10 task 기대 — got {len(tasks)}"
    expected_ids = {
        # 기본 5
        "add_alarm", "toggle_alarm", "start_stopwatch",
        "set_timer", "open_screensaver_settings",
        # 까다로운 5
        "dismiss_ringing_alarm", "change_alarm_ringtone", "add_world_city",
        "set_bedtime_schedule", "delete_world_city",
    }
    assert {t["id"] for t in tasks} == expected_ids


# ─── metadata_refresh 통합 ─────────────────────────────────


def test_app_name_inferred_from_static_info_package():
    md = {}
    static = {"package_name": "com.android.deskclock"}
    assert _infer_app_name(md, static) == "deskclock"


def test_app_name_metadata_field_wins():
    md = {"app": "Mattermost"}
    static = {"package_name": "com.android.deskclock"}  # ignored
    assert _infer_app_name(md, static) == "mattermost"


def test_app_name_none_without_signals():
    assert _infer_app_name({}, None) is None
    assert _infer_app_name({}, {"package_name": ""}) is None


def test_refresh_metadata_fills_task_coverage_for_deskclock():
    """app_name='deskclock' 들어오면 fixture 매칭 결과로 task_coverage 채움.
    minimal node set — 기본 task 3개만 풀리고 7개 missing 기대."""
    nodes = [
        {"screen_id": "n1", "activity": "com.android.deskclock.alarms.AlarmsActivity",
         "functional_category": "list", "status": "enriched"},
        {"screen_id": "n2", "activity": "com.android.deskclock.TitanViewAlarmActivity",
         "functional_category": "form", "status": "enriched"},
        {"screen_id": "n3", "activity": "com.android.deskclock.DeskClock",
         "functional_category": "home", "status": "enriched"},
    ]
    screenmap = {"screen_map": {
        "graph": {"entry_node": "n1", "nodes": nodes, "edges": []},
    }}
    md = refresh_metadata(screenmap, app_name="deskclock")
    tc = md["completeness"]["task_coverage"]
    assert tc is not None, "deskclock fixture 매칭됐어야 함"
    assert tc["total"] == 10
    feasible_ids = {t["id"] for t in tc["tasks"] if t["feasible"]}
    # 이 minimal node set 으로는 — alarm list/edit/home 만 있으니 풀리는 task 가 제한적
    assert "add_alarm" in feasible_ids
    assert "toggle_alarm" in feasible_ids
    assert "start_stopwatch" in feasible_ids
    # 어려운 task 는 화면 부족으로 missing
    assert "delete_world_city" not in feasible_ids
    assert "set_bedtime_schedule" not in feasible_ids


# ─── 까다로운 task 의 매칭 정직성 (실제 DeskClock ScreenMap) ─────


def _load_real_deskclock_screenmap():
    """실제 b7b152fd workspace ScreenMap — 100 노드 / 32 activity."""
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "workspace" / "b7b152fd" / "output" / "screen_map.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def test_real_kg_hard_tasks_honest_split():
    """실제 DeskClock ScreenMap 에 적용 시 — 8 feasible / 2 missing 의 정직한 split.

    풀려야: add_alarm, toggle_alarm, start_stopwatch, set_timer,
            open_screensaver_settings, dismiss_ringing_alarm,
            change_alarm_ringtone, add_world_city
    Missing: set_bedtime_schedule (schedule 폼 미발견),
             delete_world_city (specific delete dialog 미발견)
    """
    screenmap = _load_real_deskclock_screenmap()
    if screenmap is None:
        import pytest
        pytest.skip("workspace/b7b152fd ScreenMap 없음 — 실기 환경 의존")

    md = refresh_metadata(screenmap, app_name="deskclock")
    tc = md["completeness"]["task_coverage"]

    assert tc["total"] == 10
    assert tc["feasible"] == 8, f"8 feasible 기대 — got {tc['feasible']}"
    assert tc["ratio"] == 0.8

    feasible = {t["id"] for t in tc["tasks"] if t["feasible"]}
    missing = {t["id"] for t in tc["tasks"] if not t["feasible"]}

    # 풀려야 하는 까다로운 task — ScreenMap 에 activity 존재
    assert {"dismiss_ringing_alarm", "change_alarm_ringtone", "add_world_city"} <= feasible

    # 정직하게 missing — ScreenMap 에 specific 화면 없음
    assert missing == {"set_bedtime_schedule", "delete_world_city"}


def test_real_kg_missing_screens_traceable():
    """missing task 는 어떤 screen 이 빠졌는지 정확히 추적 가능해야."""
    screenmap = _load_real_deskclock_screenmap()
    if screenmap is None:
        import pytest
        pytest.skip("workspace/b7b152fd ScreenMap 없음")

    md = refresh_metadata(screenmap, app_name="deskclock")
    tc = md["completeness"]["task_coverage"]
    by_id = {t["id"]: t for t in tc["tasks"]}

    bedtime = by_id["set_bedtime_schedule"]
    assert bedtime["feasible"] is False
    # 어떤 expected_screen 이 missing 인지 description 확인
    assert any("schedule" in m.lower() for m in bedtime["missing"])

    delete = by_id["delete_world_city"]
    assert delete["feasible"] is False
    assert any("dialog" in m.lower() for m in delete["missing"])


# ─── evaluator 의 까다로운 edge case ─────


def test_partial_match_one_screen_missing_fails_task():
    """expected_screens 가 5개인데 1개만 missing 이어도 task 는 fail."""
    nodes = [
        {"activity": "A1"}, {"activity": "A2"}, {"activity": "A3"}, {"activity": "A4"},
        # A5 없음
    ]
    fixture = {"tasks": [{
        "id": "long_task",
        "expected_screens": [
            {"description": f"step{i}", "match_any": {"activity_substr": [f"A{i}"]}}
            for i in range(1, 6)
        ],
    }]}
    r = evaluate_task_coverage(nodes, fixture)
    assert r["feasible"] == 0
    assert r["tasks"][0]["missing"] == ["step5"]


def test_specific_activity_no_false_positive_via_category():
    """activity_substr 만 쓴 expected — functional_category 매칭 없음으로
    generic dialog 노드가 specific delete dialog 로 false positive 안 되는지."""
    nodes = [
        # generic dialog 노드 — functional_category=dialog 이지만 specific name 아님
        {"activity": "com.x.SomeOtherDialog", "functional_category": "dialog"},
    ]
    fixture = {"tasks": [{
        "id": "delete_specific",
        "expected_screens": [{
            "description": "delete confirm dialog",
            "match_any": {"activity_substr": ["DeleteCityDialog"]},
        }],
    }]}
    r = evaluate_task_coverage(nodes, fixture)
    assert r["feasible"] == 0, "다른 dialog 가 specific delete dialog 로 매칭되면 안 됨"


def test_match_any_or_semantics_either_signal_works():
    """activity_substr 매칭 안 되어도 functional_category 매칭되면 OK."""
    nodes = [{"activity": "com.x.UnrelatedName", "functional_category": "settings"}]
    fixture = {"tasks": [{
        "id": "open_settings",
        "expected_screens": [{
            "description": "settings",
            "match_any": {
                "activity_substr": ["NoMatch"],
                "functional_category": ["settings"],
            },
        }],
    }]}
    r = evaluate_task_coverage(nodes, fixture)
    assert r["feasible"] == 1


def test_case_insensitive_matching():
    """activity 대소문자 무관 매칭 — 실제 패키지명은 mixed case."""
    nodes = [{"activity": "com.X.AlarmsActivity"}]
    fixture = {"tasks": [{
        "id": "t",
        "expected_screens": [{
            "description": "x",
            "match_any": {"activity_substr": ["alarmsactivity"]},
        }],
    }]}
    r = evaluate_task_coverage(nodes, fixture)
    assert r["feasible"] == 1


def test_empty_expected_screens_not_feasible():
    """expected_screens 가 비면 task 자체가 ill-defined — feasible=False."""
    fixture = {"tasks": [{"id": "empty", "expected_screens": []}]}
    r = evaluate_task_coverage([{"activity": "anything"}], fixture)
    assert r["feasible"] == 0
    assert r["tasks"][0]["feasible"] is False


def test_refresh_metadata_task_coverage_none_when_no_fixture():
    """fixture 없는 app — task_coverage = None."""
    nodes = [{"screen_id": "n1", "activity": "com.x.A"}]
    screenmap = {"screen_map": {
        "graph": {"entry_node": "n1", "nodes": nodes, "edges": []},
    }}
    md = refresh_metadata(screenmap, app_name="unknown_app_xyz")
    assert md["completeness"]["task_coverage"] is None


def test_refresh_metadata_infers_app_from_package():
    """app_name 명시 안 해도 static_info.package 에서 추론."""
    nodes = [
        {"screen_id": "n1", "activity": "com.android.deskclock.alarms.AlarmsActivity",
         "functional_category": "list", "status": "enriched"},
    ]
    screenmap = {"screen_map": {
        "graph": {"entry_node": "n1", "nodes": nodes, "edges": []},
    }}
    static = {"package_name": "com.android.deskclock", "activities": []}
    md = refresh_metadata(screenmap, static_info=static)
    assert md["completeness"]["task_coverage"] is not None
    assert md["completeness"]["task_coverage"]["total"] == 10
