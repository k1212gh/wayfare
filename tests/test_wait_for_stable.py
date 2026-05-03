"""wait_for_stable 단위 테스트 — Stage 3 의 fixed sleep → idle-detection 교체.

검증:
- 안정된 XML → stable_window 후 True 반환 (timeout 전)
- 매번 변하는 XML → timeout 까지 대기 후 False
- dump_hierarchy 실패 → timeout 까지 polling 후 False
- 빠른 수렴 시 sleep 보다 빠른 시간 안 종료
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from stage3_walk.mixins.capture import CaptureMixin


class _FakeWalker(CaptureMixin):
    """CaptureMixin 단독 테스트용 — wait_for_stable 만 호출."""
    def __init__(self, output_dir: Path):
        self.device_serial = "fake-serial"
        self.output_dir = output_dir


_STABLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node class="android.widget.LinearLayout" resource-id="root" clickable="false" bounds="[0,0][1080,1920]">
    <node class="android.widget.Button" resource-id="btn_save" clickable="true" bounds="[40,40][200,80]" />
  </node>
</hierarchy>"""


_VARIETY_IDS = ["save", "cancel", "edit", "delete", "share", "refresh", "back", "menu", "settings"]


def _changing_xml(idx: int) -> str:
    """매번 본질적으로 다른 resource-id 의 view — signature_stabilizer 가
    trailing-digit noise 로 흡수하지 않도록 의미 있는 단어를 돌림."""
    rid = _VARIETY_IDS[idx % len(_VARIETY_IDS)]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node class="android.widget.LinearLayout" resource-id="root" clickable="false" bounds="[0,0][1080,1920]">
    <node class="android.widget.Button" resource-id="{rid}_btn" clickable="true" bounds="[40,40][200,80]" />
  </node>
</hierarchy>"""


def test_wait_for_stable_returns_true_when_hash_unchanged(tmp_path: Path):
    """XML 이 stable_window 동안 안 변하면 True 반환."""
    walker = _FakeWalker(tmp_path)

    with patch("stage3_walk.u2_helper.dump_hierarchy", return_value=_STABLE_XML):
        t0 = time.time()
        result = walker.wait_for_stable(timeout=2.0, stable_window=0.3, poll_interval=0.1)
        elapsed = time.time() - t0

    assert result is True
    assert elapsed < 1.5, f"안정 XML 인데 너무 오래 걸림: {elapsed:.2f}s"


def test_wait_for_stable_timeout_when_constant_change(tmp_path: Path):
    """매번 다른 XML → timeout 까지 대기 후 False."""
    walker = _FakeWalker(tmp_path)

    counter = {"i": 0}
    def changing(*_a, **_kw):
        counter["i"] += 1
        return _changing_xml(counter["i"])

    with patch("stage3_walk.u2_helper.dump_hierarchy", side_effect=changing):
        t0 = time.time()
        result = walker.wait_for_stable(timeout=1.0, stable_window=0.3, poll_interval=0.15)
        elapsed = time.time() - t0

    assert result is False
    assert elapsed >= 1.0, f"timeout 까지 대기해야 — got {elapsed:.2f}s"
    assert elapsed < 2.0, f"timeout 한참 넘어감: {elapsed:.2f}s"


def test_wait_for_stable_dump_failure(tmp_path: Path):
    """dump 가 None / empty 반환 → polling 계속하다 timeout."""
    walker = _FakeWalker(tmp_path)

    with patch("stage3_walk.u2_helper.dump_hierarchy", return_value=None):
        t0 = time.time()
        result = walker.wait_for_stable(timeout=0.8, stable_window=0.3, poll_interval=0.15)
        elapsed = time.time() - t0

    assert result is False
    assert elapsed >= 0.8


def test_wait_for_stable_dump_exception(tmp_path: Path):
    """dump 가 예외 던짐 → 다음 poll 로 진행 후 timeout."""
    walker = _FakeWalker(tmp_path)

    def boom(*_a, **_kw):
        raise RuntimeError("device disconnected")

    with patch("stage3_walk.u2_helper.dump_hierarchy", side_effect=boom):
        t0 = time.time()
        result = walker.wait_for_stable(timeout=0.6, stable_window=0.3, poll_interval=0.15)
        elapsed = time.time() - t0

    # 예외 안 새고 timeout 까지 polling
    assert result is False
    assert elapsed >= 0.5


def test_wait_for_stable_settles_after_initial_change(tmp_path: Path):
    """처음 2 polls 는 변하다가 그 후 stable → stable_window 후 True."""
    walker = _FakeWalker(tmp_path)

    counter = {"i": 0}
    def settle_after_2(*_a, **_kw):
        counter["i"] += 1
        if counter["i"] <= 2:
            return _changing_xml(counter["i"])
        return _STABLE_XML  # 3번째부터는 같은 XML

    with patch("stage3_walk.u2_helper.dump_hierarchy", side_effect=settle_after_2):
        t0 = time.time()
        result = walker.wait_for_stable(timeout=2.0, stable_window=0.3, poll_interval=0.1)
        elapsed = time.time() - t0

    assert result is True
    # 처음 변동 + stable_window 합산 — timeout 전에 끝남
    assert elapsed < 1.8


def test_wait_for_stable_faster_than_old_fixed_sleep(tmp_path: Path):
    """안정된 화면이면 기존 time.sleep(0.8) 보다 빠르거나 비슷."""
    walker = _FakeWalker(tmp_path)

    with patch("stage3_walk.u2_helper.dump_hierarchy", return_value=_STABLE_XML):
        t0 = time.time()
        result = walker.wait_for_stable(timeout=2.0, stable_window=0.3, poll_interval=0.1)
        elapsed = time.time() - t0

    assert result is True
    # 기존 time.sleep(0.8) 자리 대체 — 비슷하거나 빠름
    assert elapsed < 1.2, (
        f"기존 sleep 0.8 대체인데 1.2s 도 못 끝냄: {elapsed:.2f}s. "
        "stable_window/poll_interval 튜닝 필요"
    )
