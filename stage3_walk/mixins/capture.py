"""Capture mixin — UI dump + screenshot per event, plus thin ``view_tree_parser`` wrappers.

The main ``_capture_screen`` is here; it writes raw + canonical screenshot/XML
under ``self.output_dir`` and appends to ``self.states``. Pure XML parsing is
delegated to :mod:`stage3_walk.view_tree_parser`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from pathlib import Path

from .. import signature_stabilizer, view_tree_parser

logger = logging.getLogger(__name__)

# Soft IME packages we want to keep out of screenshots + UI dumps.
# When EditText gains focus the IME pops up on top of the activity — without
# explicit dismissal, screencap and uiautomator dump both pick it up, which
# pollutes pHash coalesce (same screen w/wo keyboard → false split) and Vision
# labeling (Claude reads "keyboard input screen" → form/dialog inflation).
_IME_PKG_PREFIXES: tuple[str, ...] = (
    "com.google.android.inputmethod",        # Gboard
    "com.android.inputmethod",               # AOSP / emulator default
    "com.samsung.android.honeyboard",        # Samsung Honey Board
    "com.sec.android.inputmethod",           # legacy Samsung
    "com.swiftkey",                          # SwiftKey
    "com.touchtype.swiftkey",
    "com.LGE.AppBox",                        # LG
)


def _dismiss_ime_if_shown(device_serial: str) -> None:
    """Hide the soft keyboard before a screenshot, but only if it's actually
    shown — avoids stray KEYCODE_ESCAPE on activities that bind it."""
    try:
        probe = subprocess.run(
            ["adb", "-s", device_serial, "shell",
             "dumpsys input_method | grep mInputShown"],
            capture_output=True, timeout=3,
        )
        if b"mInputShown=true" not in (probe.stdout or b""):
            return
        subprocess.run(
            ["adb", "-s", device_serial, "shell", "input keyevent 111"],
            capture_output=True, timeout=3,
        )  # KEYCODE_ESCAPE — closes IME only, no nav side effect
        time.sleep(0.3)  # IME hide animation
    except Exception:
        pass


class CaptureMixin:
    """UI/screenshot capture for the walk main loop."""

    def wait_for_stable(
        self,
        timeout: float = 3.0,
        stable_window: float = 0.3,
        poll_interval: float = 0.15,
    ) -> bool:
        """Poll device's stabilized structure hash. Return when it stays the same
        for ``stable_window`` seconds, or False on timeout.

        대체 대상: 메인 walk 루프의 fixed ``time.sleep(0.5~3.0)`` 들. 빠른 화면은
        즉시 종료, 느린 화면은 timeout 까지 대기 — 평균 30-50% 시간 단축.

        ``stabilize`` 가 ticking clock / scroll position / RecyclerView item count
        drift 를 noise 로 처리하니 시계 화면 같은 곳에서도 false unstable 안 됨.

        Returns:
            True 가 stable 도달, False 가 timeout (호출자는 보통 그래도 진행).

        P0 (2026-05-06): framework 별 timeout 보정. Compose 는 main thread
        에서 reflow 하므로 빠른 dump+click 시 ANR 발생. 'isn't responding'
        다이얼로그 → +1.5s. RN 은 JS bridge 응답 더 느림 → +1s.
        """
        fw = getattr(self, "framework", "xml")
        if fw == "compose":
            timeout = max(timeout, 4.5)
            stable_window = max(stable_window, 0.5)
        elif fw == "react-native":
            timeout = max(timeout, 4.0)
            stable_window = max(stable_window, 0.4)
        from .. import u2_helper
        start = time.time()
        last_hash: str | None = None
        last_change = start
        tmp_xml = self.output_dir / ".wait_stable_tmp.xml"

        # 첫 dump 까지 기다리는 짧은 grace — 액션 직후 디바이스 응답 안 시작했을 수 있음
        time.sleep(min(poll_interval, 0.1))

        polls = 0
        while True:
            elapsed = time.time() - start
            if elapsed >= timeout:
                logger.debug("wait_for_stable timeout after %.1fs (%d polls)", elapsed, polls)
                return False
            try:
                xml = u2_helper.dump_hierarchy(self.device_serial, timeout=2.0)
                if not xml or "<hierarchy" not in xml:
                    time.sleep(poll_interval)
                    polls += 1
                    continue
                tmp_xml.write_text(xml, encoding="utf-8")
                views = view_tree_parser.parse_ui_xml(tmp_xml)
                # activity 갱신 cost 회피 — wait_for_stable 안에선 structure 만 필요
                structure = signature_stabilizer.compute_structure_str("", "", views)
                cur_hash = hashlib.sha256(structure.encode()).hexdigest()[:16]
            except Exception as e:
                logger.debug("wait_for_stable poll error: %s", e)
                time.sleep(poll_interval)
                polls += 1
                continue

            now = time.time()
            if cur_hash != last_hash:
                last_hash = cur_hash
                last_change = now
            elif now - last_change >= stable_window:
                logger.debug("wait_for_stable stable after %.2fs (%d polls)", now - start, polls + 1)
                return True
            time.sleep(poll_interval)
            polls += 1

    def _capture_screen(self, idx: int) -> dict | None:
        """Dump UI hierarchy and screenshot from device.

        File structure:
          dynamic/
          ├── screenshots/           ← 고유 화면별 대표 스크린샷
          │   ├── screen_000.png
          │   └── screen_001.png
          ├── xml/                   ← 고유 화면별 UI XML
          │   ├── screen_000.xml
          │   └── screen_001.xml
          ├── raw/                   ← 모든 이벤트의 원본 (디버깅용)
          │   ├── capture_0000.png
          │   └── capture_0000.xml
          └── states/                ← 상태 JSON
              └── state_0000.json
        """
        (self.output_dir / "screenshots").mkdir(exist_ok=True)
        (self.output_dir / "xml").mkdir(exist_ok=True)
        (self.output_dir / "raw").mkdir(exist_ok=True)
        (self.output_dir / "states").mkdir(exist_ok=True)

        try:
            # Soft IME would otherwise occlude the bottom of screenshots and
            # show up as inputmethod views in the XML — both poison coalesce.
            _dismiss_ime_if_shown(self.device_serial)

            # UI dump via uiautomator2 (no idle-state requirement — works on
            # animated screens that the CLI `uiautomator dump` can't handle:
            # live clocks, rotating banner ads, autoplay video thumbnails,
            # loading spinners, etc). Falls back to the CLI path internally
            # if u2 fails to connect.
            from .. import u2_helper
            raw_xml = self.output_dir / "raw" / f"capture_{idx:04d}.xml"
            xml_content = u2_helper.dump_hierarchy(self.device_serial, timeout=8.0)
            if xml_content and "<hierarchy" in xml_content:
                raw_xml.write_text(xml_content, encoding="utf-8")
                dump_success = True
            else:
                dump_success = False
                logger.info("UI dump returned empty — proceeding with screenshot only")

            raw_screen = self.output_dir / "raw" / f"capture_{idx:04d}.png"
            subprocess.run(["adb", "-s", self.device_serial, "shell",
                            "screencap -p //sdcard//sa_screen.png"],
                           capture_output=True, timeout=15)
            subprocess.run(["adb", "-s", self.device_serial, "pull",
                            "//sdcard//sa_screen.png", str(raw_screen)],
                           capture_output=True, timeout=15)

            views = self._parse_ui_xml(raw_xml)
            # Drop IME overlay views — dismiss above handles the common case,
            # but if XML dump raced ahead of the IME hide animation it can
            # still contain InputMethod nodes. Filtering here keeps the
            # structural hash stable across keyboard transitions.
            if views:
                views = [
                    v for v in views
                    if not (v.get("package", "") or "").startswith(_IME_PKG_PREFIXES)
                ]

            # Get current activity + top fragment. Spotify-class apps under load
            # can take 10+ seconds to respond to dumpsys, so use a generous timeout.
            try:
                activity_result = subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     "dumpsys activity top"],
                    capture_output=True, timeout=25,
                )
            except subprocess.TimeoutExpired:
                logger.warning("dumpsys activity top timed out; using empty activity context")
                activity_result = subprocess.CompletedProcess([], 1, b"", b"")
            stdout_text = activity_result.stdout.decode("utf-8", errors="replace") if activity_result.stdout else ""
            activity = self._extract_activity(stdout_text)
            fragment = self._extract_fragment(stdout_text)

            is_dialog = self._detect_dialog(views)

            # structure_str / state_str via signature_stabilizer — drops ticking
            # clock text, numeric RecyclerView suffixes, and animated View
            # classes so the same logical screen produces the same hash even
            # when a clock ticks or a list item count drifts. See
            # stage3_walk/signature_stabilizer.py for the stabilization rules.
            structure_str = signature_stabilizer.compute_structure_str(
                activity=activity, fragment=fragment, views=views,
            )
            state_str = signature_stabilizer.compute_state_str(
                activity=activity, fragment=fragment, views=views,
            )

            screen_name = f"screen_{hashlib.sha256(structure_str.encode()).hexdigest()[:8]}"

            canonical_screen = self.output_dir / "screenshots" / f"{screen_name}.png"
            if not canonical_screen.exists() and raw_screen.exists():
                import shutil
                shutil.copy2(raw_screen, canonical_screen)
                canonical_xml = self.output_dir / "xml" / f"{screen_name}.xml"
                if raw_xml.exists():
                    shutil.copy2(raw_xml, canonical_xml)

            state = {
                "state_str": state_str,
                "structure_str": structure_str,
                "activity": activity,
                "fragment": fragment,
                "is_dialog": is_dialog,
                "views": views,
                "screenshot_path": str(canonical_screen) if canonical_screen.exists() else str(raw_screen) if raw_screen.exists() else "",
            }
            self.states.append(state)

            state_json = self.output_dir / "states" / f"state_{idx:04d}.json"
            state_json.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

            return state

        except Exception as e:
            logger.warning("Failed to capture state: %s", e)
            return None

    # Pure UI-parsing helpers live in view_tree_parser.py; these 1-line wrappers
    # preserve the `self._foo(...)` call sites inside the class (and any
    # external caller that still uses them).

    def _parse_ui_xml(self, xml_path: Path) -> list[dict]:
        return view_tree_parser.parse_ui_xml(xml_path)

    def _extract_fragment(self, dumpsys_output: str) -> str:
        return view_tree_parser.extract_fragment(dumpsys_output)

    def _detect_dialog(self, views: list[dict]) -> bool:
        return view_tree_parser.detect_dialog(views)

    def _detect_popup_menu(self, views: list[dict]) -> bool:
        return view_tree_parser.detect_popup_menu(views)

    def _popup_items(self, views: list[dict]) -> list[dict]:
        return view_tree_parser.popup_items(views)

    def _extract_activity(self, dumpsys_output: str) -> str:
        return view_tree_parser.extract_activity(
            dumpsys_output, target_pkg=getattr(self, "package", "") or "",
        )
