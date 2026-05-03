"""VisionTapper 단위 테스트 — mock vision API + 좌표 검증 + budget."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


_SAMPLE_RESPONSE = json.dumps({
    "actionable_widgets": [
        {
            "label": "OK",
            "type": "button",
            "bounds": [782, 1013, 950, 1139],
            "confidence": 0.94,
            "expected_outcome": "Confirm time selection",
        },
        {
            "label": "Cancel",
            "type": "button",
            "bounds": [572, 1013, 761, 1139],
            "confidence": 0.92,
            "expected_outcome": "Discard time changes",
        },
        {
            "label": "12",
            "type": "input",
            "bounds": [172, 688, 424, 898],
            "confidence": 0.85,
            "expected_outcome": "Edit hour value",
        },
    ],
    "stall_signal": "Material TimePicker shown with default 12:00",
    "recommended_next": "OK button [782,1013] - commit time",
})


@pytest.fixture
def fake_screenshot(tmp_path):
    """1080×2400 흰 PNG 생성 (Pillow 필요)."""
    pillow = pytest.importorskip("PIL")
    from PIL import Image
    p = tmp_path / "shot.png"
    Image.new("RGB", (1080, 2400), "white").save(p)
    return p


def _mk_clicker(monkeypatch, response_text=_SAMPLE_RESPONSE, budget=10):
    pytest.importorskip("anthropic")
    monkeypatch.setenv("VISION_BUDGET", str(budget))
    from stage3_walk.vision_tapper import VisionTapper

    # anthropic.Anthropic 인스턴스 mock
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=response_text)]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg

    with patch("anthropic.Anthropic", return_value=mock_client):
        vc = VisionTapper(api_key="sk-test-key")
    return vc, mock_client


# ─── 정상 케이스 ────────────────────────────────


def test_extract_actionable_returns_validated_actions(monkeypatch, fake_screenshot):
    vc, _ = _mk_clicker(monkeypatch)
    actions = vc.extract_actionable(fake_screenshot, state_str="screen_a")
    assert len(actions) == 3
    # confidence 정렬 — 첫 번째가 가장 높음
    assert actions[0]["confidence"] >= actions[1]["confidence"]
    assert actions[0]["label"] == "OK"


def test_click_point_center():
    from stage3_walk.vision_tapper import VisionTapper
    action = {"bounds": [100, 200, 200, 300]}
    assert VisionTapper.click_point(action) == (150, 250)


# ─── Cache ────────────────────────────────────────


def test_cache_avoids_redundant_call(monkeypatch, fake_screenshot):
    vc, mock_client = _mk_clicker(monkeypatch)
    a1 = vc.extract_actionable(fake_screenshot, state_str="screen_a")
    a2 = vc.extract_actionable(fake_screenshot, state_str="screen_a")
    # 두번째는 cache hit — API 호출 1회만
    assert mock_client.messages.create.call_count == 1
    assert a1 == a2


def test_cache_per_state_str(monkeypatch, fake_screenshot):
    vc, mock_client = _mk_clicker(monkeypatch)
    vc.extract_actionable(fake_screenshot, state_str="screen_a")
    vc.extract_actionable(fake_screenshot, state_str="screen_b")
    # 다른 state — 두 번 호출
    assert mock_client.messages.create.call_count == 2


# ─── Budget cap ──────────────────────────────────


def test_budget_cap_blocks_over_limit(monkeypatch, fake_screenshot):
    vc, mock_client = _mk_clicker(monkeypatch, budget=2)
    vc.extract_actionable(fake_screenshot, state_str="s1")
    vc.extract_actionable(fake_screenshot, state_str="s2")
    # 3번째는 budget 초과로 빈 리스트
    actions = vc.extract_actionable(fake_screenshot, state_str="s3")
    assert actions == []
    assert mock_client.messages.create.call_count == 2


# ─── 좌표 검증 ─────────────────────────────────


def test_invalid_bounds_filtered_out(monkeypatch, fake_screenshot):
    bad = json.dumps({
        "actionable_widgets": [
            {"label": "OOB", "type": "button",
             "bounds": [2000, 3000, 2100, 3100],  # 화면 밖
             "confidence": 0.9, "expected_outcome": ""},
            {"label": "tiny", "type": "button",
             "bounds": [10, 10, 15, 15],  # 5x5 — 너무 작음
             "confidence": 0.9, "expected_outcome": ""},
            {"label": "ok", "type": "button",
             "bounds": [100, 100, 300, 200],  # 정상
             "confidence": 0.9, "expected_outcome": ""},
        ],
        "stall_signal": "",
        "recommended_next": "",
    })
    vc, _ = _mk_clicker(monkeypatch, response_text=bad)
    actions = vc.extract_actionable(fake_screenshot, state_str="s1")
    assert len(actions) == 1
    assert actions[0]["label"] == "ok"


def test_low_confidence_filtered_out(monkeypatch, fake_screenshot):
    low = json.dumps({
        "actionable_widgets": [
            {"label": "low", "type": "button",
             "bounds": [100, 100, 300, 200],
             "confidence": 0.5,
             "expected_outcome": ""},
            {"label": "high", "type": "button",
             "bounds": [100, 100, 300, 200],
             "confidence": 0.9,
             "expected_outcome": ""},
        ],
        "stall_signal": "", "recommended_next": "",
    })
    vc, _ = _mk_clicker(monkeypatch, response_text=low)
    actions = vc.extract_actionable(fake_screenshot, state_str="s1")
    assert len(actions) == 1
    assert actions[0]["label"] == "high"


# ─── 파싱 실패 ────────────────────────────────────


def test_invalid_json_returns_empty(monkeypatch, fake_screenshot):
    vc, _ = _mk_clicker(monkeypatch, response_text="not json at all!")
    actions = vc.extract_actionable(fake_screenshot, state_str="s1")
    assert actions == []


def test_markdown_codefence_stripped(monkeypatch, fake_screenshot):
    """LLM 이 ```json ... ``` 으로 wrap 한 응답 처리."""
    wrapped = "```json\n" + _SAMPLE_RESPONSE + "\n```"
    vc, _ = _mk_clicker(monkeypatch, response_text=wrapped)
    actions = vc.extract_actionable(fake_screenshot, state_str="s1")
    assert len(actions) == 3


# ─── 누락 / 빈 input ─────────────────────────────


def test_missing_screenshot_returns_empty(monkeypatch, tmp_path):
    vc, _ = _mk_clicker(monkeypatch)
    actions = vc.extract_actionable(tmp_path / "nonexistent.png", state_str="s1")
    assert actions == []


def test_empty_state_str_no_cache(monkeypatch, fake_screenshot):
    vc, mock_client = _mk_clicker(monkeypatch)
    vc.extract_actionable(fake_screenshot, state_str="")
    vc.extract_actionable(fake_screenshot, state_str="")
    # state_str 빈 string — cache key 안 됨, 매번 호출
    assert mock_client.messages.create.call_count == 2
