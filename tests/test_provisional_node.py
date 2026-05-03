"""Pass 1 → Pass 2 흐름 테스트 — provisional 노드 마킹.

검증:
  1. screenmap_builder 가 unit/analysis/sg_node 의 needs_vision_in_revisit → node.is_provisional
  2. 마킹 안 된 unit → node.is_provisional = False (default, additive)
  3. 3 source 어디서든 매치되면 True (OR)
"""

from __future__ import annotations

from stage6_screenmap.screenmap_builder import _build_node


def _unit(screen_id="s1", **kw):
    base = {
        "screen_id": screen_id,
        "activity_name": "com.app.A",
        "screenshot": "",
        "structure_str": "hash1",
    }
    base.update(kw)
    return base


def test_default_is_provisional_false():
    """기본 — 마킹 안 한 unit → is_provisional=False (backward compat)."""
    node = _build_node(
        sg_node={"screen_id": "s1"},
        analysis={},
        unit=_unit(),
    )
    assert node["is_provisional"] is False


def test_unit_flag_propagates_to_node():
    """unit.needs_vision_in_revisit=True → node.is_provisional=True."""
    node = _build_node(
        sg_node={"screen_id": "s1"},
        analysis={},
        unit=_unit(needs_vision_in_revisit=True),
    )
    assert node["is_provisional"] is True


def test_analysis_flag_also_propagates():
    """analysis.needs_vision_in_revisit 도 매치 (LLM 라벨링 단계 마킹 가능)."""
    node = _build_node(
        sg_node={"screen_id": "s1"},
        analysis={"needs_vision_in_revisit": True},
        unit=_unit(),
    )
    assert node["is_provisional"] is True


def test_sgnode_flag_also_propagates():
    node = _build_node(
        sg_node={"screen_id": "s1", "needs_vision_in_revisit": True},
        analysis={},
        unit=_unit(),
    )
    assert node["is_provisional"] is True


def test_other_node_fields_preserved():
    """is_provisional 추가가 다른 필드 영향 안 줘야."""
    node = _build_node(
        sg_node={"screen_id": "s1"},
        analysis={"functional_category": "form", "screen_purpose": "login"},
        unit=_unit(activity_name="com.app.LoginActivity",
                   needs_vision_in_revisit=True),
    )
    assert node["is_provisional"] is True
    assert node["activity"] == "com.app.LoginActivity"
    assert node["functional_category"] == "form"
    assert node["screen_purpose"] == "login"
