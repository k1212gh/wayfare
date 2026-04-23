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
from pathlib import Path

from .. import view_tree_parser

logger = logging.getLogger(__name__)


class CaptureMixin:
    """UI/screenshot capture for the walk main loop."""

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

            # structure_str now includes fragment — same activity different fragment = different screen
            # e.g. Spotify MainActivity + HomeFragment vs MainActivity + SearchFragment
            clickable_ids = sorted(
                v.get("resource_id", "") for v in views if v.get("clickable")
            )
            structure_str = hashlib.sha256(
                f"{activity}|{fragment}|{'|'.join(clickable_ids)}".encode()
            ).hexdigest()

            state_str = hashlib.sha256(
                f"{activity}|{fragment}|{json.dumps([v.get('text','') for v in views[:20]])}".encode()
            ).hexdigest()

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
