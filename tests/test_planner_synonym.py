"""Phase 1 P2.1 / P2.2 — navigator synonym map + failure_reason 단위 테스트."""

from __future__ import annotations

from navigator.journey_planner import _expand_keywords, _find_topic_nodes, plan_task


# ─── _expand_keywords ────────────────────────────────────────────

def test_korean_to_english_expansion():
    kws = _expand_keywords("알람")
    assert "알람" in kws
    assert "alarm" in kws  # 한국어 → 영어 synonym


def test_english_to_korean_expansion():
    """영어로 입력해도 한국어 synonym 까지 확장 (역방향)."""
    kws = _expand_keywords("alarm")
    assert "alarm" in kws
    assert "알람" in kws


def test_multi_word_task_each_word_expanded():
    kws = _expand_keywords("알람 설정")
    assert "알람" in kws and "alarm" in kws
    assert "설정" in kws and "setting" in kws


def test_dark_mode_synonyms():
    """SaaS 류 흔한 키워드."""
    kws = _expand_keywords("다크모드 켜기")
    assert "다크모드" in kws
    assert any("dark" in k for k in kws)
    assert "켜기" in kws
    assert "enable" in kws or "on" in kws


def test_share_synonyms():
    kws = _expand_keywords("사진 공유")
    assert "사진" in kws and "photo" in kws
    assert "공유" in kws and "share" in kws


def test_unknown_word_just_passed_through():
    """매핑에 없는 단어는 그대로 (확장 안 됨)."""
    kws = _expand_keywords("abracadabra")
    assert kws == ["abracadabra"]


def test_no_duplicates():
    """중복 제거 — 같은 단어 두 번 나와도 coalescee."""
    kws = _expand_keywords("알람 알람")
    assert kws.count("알람") == 1
    assert kws.count("alarm") == 1


# ─── _find_topic_nodes 매칭 ────────────────────────────────────

def _node(sid, label="", activity="", category="other", purpose=""):
    return {
        "screen_id": sid, "label": label, "activity": activity,
        "functional_category": category, "screen_purpose": purpose,
    }


def test_korean_task_matches_english_label():
    """task '알람' 으로 검색하면 label='Alarm' 노드도 매칭."""
    nodes = [
        _node("n1", label="Alarm", category="home"),
        _node("n2", label="Settings", category="settings"),
    ]
    topics = _find_topic_nodes(nodes, "알람")
    assert topics, "synonym 확장으로 'alarm' label 매칭"
    assert topics[0]["screen_id"] == "n1"


def test_english_task_matches_korean_label():
    """task 'settings' 로 검색하면 label='설정' 노드도 매칭."""
    nodes = [
        _node("n1", label="알람", category="home"),
        _node("n2", label="설정", category="settings"),
    ]
    topics = _find_topic_nodes(nodes, "settings")
    assert topics
    assert topics[0]["screen_id"] == "n2"


# ─── plan_task failure_reason 표준화 ────────────────────────────

def _empty_screenmap():
    return {"screen_map": {"graph": {"nodes": [], "edges": [], "entry_node": ""}}}


def test_failure_reason_no_topic_match():
    r = plan_task(_empty_screenmap(), "anything")
    assert r.get("failure_reason") == "no_topic_match"


def test_failure_reason_includes_expanded_keywords():
    """no_topic_match 시 디버깅용 expanded_keywords 도 함께 반환."""
    r = plan_task(_empty_screenmap(), "알람")
    assert r.get("failure_reason") == "no_topic_match"
    assert "expanded_keywords" in r
    assert "alarm" in r["expanded_keywords"]


def test_failure_reason_topic_found_but_no_path():
    """토픽 매칭은 됐지만 entry 와 단절된 경우."""
    nodes = [
        _node("entry", label="Home"),
        _node("orphan", label="Alarm"),  # 매칭은 되지만 entry 와 연결 안 됨
    ]
    screenmap = {"screen_map": {"graph": {
        "nodes": nodes, "edges": [], "entry_node": "entry",
    }}}
    r = plan_task(screenmap, "알람")
    assert r.get("failure_reason") == "topic_found_but_no_path"
    assert "topic_nodes" in r
