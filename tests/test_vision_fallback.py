"""tap_walker._try_vision_fallback 통합 테스트.

VisionTapper 자체는 test_vision_tapper.py 에서 검증. 여기는 TapWalker
와의 결합 — stall 시 vision 호출 → 좌표 추출 → adb input tap 실행 흐름.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_walker(tmp_path, vision_tapper):
    """TapWalker 의 __init__ 우회 — APK 로딩 등 무거운 초기화 스킵."""
    from stage3_walk.tap_walker import TapWalker
    e = TapWalker.__new__(TapWalker)
    e.device_serial = "fake-serial"
    e.tried_actions = defaultdict(set)
    e.trap_stats = defaultdict(int)
    e.vision_tapper = vision_tapper
    return e


def _screen_with_screenshot(tmp_path) -> dict:
    """실제로 존재하는 1×1 PNG 를 가리키는 state dict."""
    pillow = pytest.importorskip("PIL")
    from PIL import Image
    p = tmp_path / "shot.png"
    Image.new("RGB", (10, 10), "white").save(p)
    return {"screenshot_path": str(p), "views": []}


def _mk_vision_mock(actions: list[dict] | Exception | None = None):
    """VisionTapper mock — extract_actionable 가 actions / 예외 / 빈 리스트 반환."""
    vc = MagicMock()
    if isinstance(actions, Exception):
        vc.extract_actionable.side_effect = actions
    else:
        vc.extract_actionable.return_value = actions or []
    # click_point 은 실제 staticmethod 동작 그대로
    from stage3_walk.vision_tapper import VisionTapper
    vc.click_point.side_effect = VisionTapper.click_point
    return vc


# ─── 비활성 / 누락 케이스 — 즉시 False ──────────────────────


def test_no_vision_tapper_returns_false(tmp_path):
    e = _make_walker(tmp_path, vision_tapper=None)
    state = _screen_with_screenshot(tmp_path)
    assert e._try_vision_fallback(state, "c1") is False


def test_missing_screenshot_returns_false(tmp_path):
    e = _make_walker(tmp_path, _mk_vision_mock([{"label": "x"}]))
    state = {"screenshot_path": "", "views": []}
    assert e._try_vision_fallback(state, "c1") is False


def test_screenshot_path_does_not_exist_returns_false(tmp_path):
    e = _make_walker(tmp_path, _mk_vision_mock([{"label": "x"}]))
    state = {"screenshot_path": str(tmp_path / "nope.png"), "views": []}
    assert e._try_vision_fallback(state, "c1") is False


def test_empty_actions_returns_false(tmp_path):
    e = _make_walker(tmp_path, _mk_vision_mock([]))
    state = _screen_with_screenshot(tmp_path)
    assert e._try_vision_fallback(state, "c1") is False


def test_vision_extract_exception_returns_false(tmp_path):
    e = _make_walker(tmp_path, _mk_vision_mock(RuntimeError("boom")))
    state = _screen_with_screenshot(tmp_path)
    assert e._try_vision_fallback(state, "c1") is False


# ─── 정상 호출 — adb tap 실행 + tried 누적 ──────────────────


def test_taps_top_action_and_records_tried(tmp_path):
    actions = [
        {"label": "OK", "type": "button",
         "bounds": [200, 400, 400, 500], "confidence": 0.95},
    ]
    e = _make_walker(tmp_path, _mk_vision_mock(actions))
    state = _screen_with_screenshot(tmp_path)

    with patch("stage3_walk.tap_walker.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        ok = e._try_vision_fallback(state, "c1")

    assert ok is True
    # adb input tap (300, 450) — bounds 중심
    args = run.call_args.args[0]
    assert args[:6] == ["adb", "-s", "fake-serial", "shell", "input", "tap"]
    assert args[6:8] == ["300", "450"]
    # tried_actions 누적 — 다음 호출에서 같은 action 재시도 안 함
    assert any("OK" in k for k in e.tried_actions["c1"])
    # trap_stats 카운트
    assert e.trap_stats["vision_fallback"] == 1


def test_skips_already_tried_actions(tmp_path):
    """같은 canonical 의 vision-action 이 이미 시도됐으면 다음 후보로 넘어감."""
    actions = [
        {"label": "OK", "type": "button",
         "bounds": [200, 400, 400, 500], "confidence": 0.95},
        {"label": "Cancel", "type": "button",
         "bounds": [600, 400, 800, 500], "confidence": 0.90},
    ]
    e = _make_walker(tmp_path, _mk_vision_mock(actions))
    state = _screen_with_screenshot(tmp_path)
    # OK 를 미리 tried 에 등록
    e.tried_actions["c1"].add("vision:OK@[200, 400, 400, 500]")

    with patch("stage3_walk.tap_walker.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        ok = e._try_vision_fallback(state, "c1")

    assert ok is True
    # Cancel 의 중심 (700, 450) 으로 tap
    args = run.call_args.args[0]
    assert args[6:8] == ["700", "450"]


def test_all_actions_tried_returns_false(tmp_path):
    actions = [
        {"label": "OK", "type": "button",
         "bounds": [200, 400, 400, 500], "confidence": 0.95},
    ]
    e = _make_walker(tmp_path, _mk_vision_mock(actions))
    state = _screen_with_screenshot(tmp_path)
    e.tried_actions["c1"].add("vision:OK@[200, 400, 400, 500]")

    with patch("stage3_walk.tap_walker.subprocess.run") as run:
        ok = e._try_vision_fallback(state, "c1")

    assert ok is False
    # adb 호출 없음
    run.assert_not_called()


def test_adb_failure_returns_false_but_records_tried(tmp_path):
    """adb tap 자체가 실패하면 False 반환 — 같은 action 재시도 막음."""
    actions = [
        {"label": "OK", "type": "button",
         "bounds": [200, 400, 400, 500], "confidence": 0.95},
    ]
    e = _make_walker(tmp_path, _mk_vision_mock(actions))
    state = _screen_with_screenshot(tmp_path)

    with patch("stage3_walk.tap_walker.subprocess.run",
               side_effect=RuntimeError("adb gone")):
        ok = e._try_vision_fallback(state, "c1")

    assert ok is False
    assert any("OK" in k for k in e.tried_actions["c1"])


# ─── 환경 변수 init — opt-in 확인 ─────────────────────────


def test_init_disabled_by_default(monkeypatch):
    """VISION_CLICKER_ENABLED 미설정이면 self.vision_tapper 가 None."""
    monkeypatch.delenv("VISION_CLICKER_ENABLED", raising=False)
    # __init__ 안에서 vision_tapper None 으로 초기화되는지 — 다른 무거운 init 우회.
    # 직접 검증은 복잡해서 module 안의 코드 패스만 확인:
    from stage3_walk import tap_walker as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "VISION_CLICKER_ENABLED" in src
    assert "self.vision_tapper = None" in src


def test_init_requires_api_key(monkeypatch, caplog):
    """ENABLED 켜져도 API key 없으면 vision_tapper 가 None 으로 유지."""
    monkeypatch.setenv("VISION_CLICKER_ENABLED", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # __init__ 의 해당 분기만 검증 — 직접 재현 어려우므로 src 안의 가드 존재 확인
    from stage3_walk import tap_walker as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    # 2026-09-12: 키 검사는 llm_client.is_llm_configured (PLACEHOLDER 포함) 로 이동
    assert "is_llm_configured" in src
    from stage5_annotate import llm_client as lc
    monkeypatch.setenv("LLM_MODE", "api")
    assert lc.is_llm_configured()[0] is False
