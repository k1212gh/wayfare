"""TarpitEscaper 단위 테스트 — mock LLM client, 후보 필터, 파싱, 예산, 캐시."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from stage3_walk.tarpit_escaper import MAX_CANDIDATES, TarpitEscaper


def _v(text="", desc="", rid="", cls="android.widget.TextView", clickable=False, visible=True):
    return {"text": text, "content_desc": desc, "resource_id": rid, "class": cls,
            "clickable": clickable, "visible": visible, "bounds": "[0,0][100,50]"}


def _client(reply: str | Exception):
    c = MagicMock()
    if isinstance(reply, Exception):
        c.messages.create.side_effect = reply
    else:
        c.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(text=reply)])
    return c


VIEWS = [
    _v(text="홈", clickable=True),          # 0
    _v(),                                    # 1 — 라벨 없음 → 후보 제외
    _v(desc="메뉴", clickable=True),          # 2
    _v(text="숨김", visible=False),           # 3 — invisible → 제외
    _v(rid="com.app:id/btn_order", clickable=True),  # 4
]


def test_candidates_filter_and_cap():
    cands = TarpitEscaper.candidates(VIEWS)
    assert [i for i, _ in cands] == [0, 2, 4]
    many = [_v(text=str(i)) for i in range(MAX_CANDIDATES + 10)]
    assert len(TarpitEscaper.candidates(many)) == MAX_CANDIDATES


@pytest.mark.parametrize("text,expected", [
    ('{"screen_kind": "home", "picks": [{"index": 2, "reason": "menu"}, {"index": 4}]}', [2, 4]),
    ('```json\n{"picks": [{"index": 0, "reason": "x"}]}\n```', [0]),
    ('Sure! {"picks": [4]} thanks', [4]),
    ('not json at all', []),
    ('{"picks": "nope"}', []),
    ('', []),
])
def test_parse_picks(text, expected):
    assert [p["index"] for p in TarpitEscaper.parse_picks(text)] == expected


def test_suggest_maps_indices_to_views():
    esc = TarpitEscaper(client=_client('{"picks": [{"index": 2, "reason": "메뉴 진입"}, {"index": 99}]}'), budget=5)
    out = esc.suggest(VIEWS, "MainActivity", tried_descs={"click 홈"}, recent_descs=["click 홈"], canonical_id="c1")
    assert len(out) == 1
    assert out[0]["index"] == 2 and out[0]["view"]["content_desc"] == "메뉴"
    assert esc.calls_used == 1
    # 프롬프트에 tried / recent 가 들어갔는지
    prompt = esc.client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "click 홈" in prompt and "Widgets:" in prompt and "[2]" in prompt


def test_suggest_uses_cache_on_same_canonical():
    esc = TarpitEscaper(client=_client('{"picks": [{"index": 0}]}'), budget=5)
    esc.suggest(VIEWS, "A", set(), [], canonical_id="c1")
    esc.suggest(VIEWS, "A", set(), [], canonical_id="c1")
    assert esc.calls_used == 1
    assert esc.client.messages.create.call_count == 1


def test_suggest_budget_exhausted_returns_empty():
    esc = TarpitEscaper(client=_client('{"picks": [{"index": 0}]}'), budget=1)
    assert esc.suggest(VIEWS, "A", set(), [], canonical_id="c1")
    assert esc.suggest(VIEWS, "A", set(), [], canonical_id="c2") == []
    assert esc.calls_used == 1


def test_suggest_llm_failure_returns_empty():
    esc = TarpitEscaper(client=_client(RuntimeError("network")), budget=5)
    assert esc.suggest(VIEWS, "A", set(), [], canonical_id="c1") == []


def test_suggest_no_candidates_skips_call():
    esc = TarpitEscaper(client=_client('{"picks": [{"index": 0}]}'), budget=5)
    assert esc.suggest([_v(), _v(visible=False)], "A", set(), []) == []
    assert esc.calls_used == 0


def test_requires_real_key_without_client():
    with pytest.raises(ValueError):
        TarpitEscaper(api_key="sk-ant-PLACEHOLDER")
