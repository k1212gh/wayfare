"""Device-session mixin — adb helpers, foreground probes, app-bounds guard.

Every method here talks to adb via subprocess. Failures are always tolerated
(walk keeps going) because transient adb hiccups are routine on busy
emulators and crashing the pipeline over them would be wrong.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time

from .. import view_tree_parser

logger = logging.getLogger(__name__)


class DeviceSessionMixin:
    """adb/device interactions used throughout the walk loop."""

    _RUNTIME_PERMISSIONS = (
        "android.permission.READ_EXTERNAL_STORAGE",
        "android.permission.WRITE_EXTERNAL_STORAGE",
        "android.permission.READ_MEDIA_AUDIO",
        "android.permission.READ_MEDIA_IMAGES",
        "android.permission.READ_MEDIA_VIDEO",
        "android.permission.RECORD_AUDIO",
        "android.permission.CAMERA",
        "android.permission.ACCESS_FINE_LOCATION",
        "android.permission.ACCESS_COARSE_LOCATION",
        "android.permission.READ_CONTACTS",
        "android.permission.POST_NOTIFICATIONS",
        "android.permission.BLUETOOTH_CONNECT",
        "android.permission.BLUETOOTH_SCAN",
        "android.permission.NEARBY_WIFI_DEVICES",
        # Phone-related — Banapresso onboarding blocked at "manage phone calls"
        # dialog because none of these were pre-granted. Adding them lets
        # 한국 commerce/login apps that gate on phone identity get past intro.
        "android.permission.CALL_PHONE",
        "android.permission.READ_PHONE_STATE",
        "android.permission.READ_PHONE_NUMBERS",
        # Calendar / SMS / activity-recognition often surface during signup
        # too — pre-granting is cheap and harmless if app doesn't declare them.
        "android.permission.WRITE_CALENDAR",
        "android.permission.READ_CALENDAR",
        "android.permission.SEND_SMS",
        "android.permission.RECEIVE_SMS",
        "android.permission.READ_SMS",
        "android.permission.ACTIVITY_RECOGNITION",
    )

    # ───── Emulator / device probing ──────────────────────────────

    def _is_emulator_device(self) -> bool:
        """Detect emulator vs real device. Serial prefix is the first signal;
        fall back to `getprop ro.kernel.qemu`."""
        if self.device_serial.startswith("emulator-"):
            return True
        try:
            r = subprocess.run(
                ["adb", "-s", self.device_serial, "shell", "getprop", "ro.kernel.qemu"],
                capture_output=True, timeout=5,
            )
            out = (r.stdout or b"").decode("utf-8", errors="replace").strip()
            if out == "1":
                return True
            # Alternative property used by some emulators
            r2 = subprocess.run(
                ["adb", "-s", self.device_serial, "shell", "getprop", "ro.boot.qemu"],
                capture_output=True, timeout=5,
            )
            return (r2.stdout or b"").decode("utf-8", errors="replace").strip() == "1"
        except Exception:
            # Unknown — prefer safe path (don't reinstall)
            return False

    def _grant_runtime_permissions(self, package: str) -> None:
        """Best-effort pre-grant of common runtime permissions via adb.
        Skips silently when a permission isn't declared by this app."""
        granted = 0
        for perm in self._RUNTIME_PERMISSIONS:
            try:
                r = subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     "pm", "grant", package, perm],
                    capture_output=True, timeout=5,
                )
                if r.returncode == 0:
                    granted += 1
            except Exception:
                pass
        if granted:
            logger.info("Pre-granted %d runtime permissions for %s", granted, package)

    def _load_declared_activities(self) -> set[str]:
        """Return every Activity FQN declared in the APK's AndroidManifest.

        Used by the foreground guard to recognize legacy-namespace activities
        (e.g. DeskClock's `com.android.deskclock.*` activities belonging to
        the `com.google.android.deskclock` package). Without this, every
        tick is mistakenly flagged as 'out of app'.

        Source: stage 2 static analysis output (if ready) else empty set.
        """
        acts: set[str] = set()
        try:
            static_path = self.output_dir.parent / "static" / "analysis.json"
            if static_path.exists():
                data = json.loads(static_path.read_text(encoding="utf-8"))
                for a in data.get("activities", []) or []:
                    name = a.get("name") or ""
                    if name:
                        acts.add(name)
        except Exception as e:
            logger.debug("could not load declared activities: %s", e)
        return acts

    # ───── Foreground / task-stack probes ─────────────────────────

    def _current_activity(self) -> str:
        """Lightweight foreground-activity probe (dumpsys)."""
        try:
            r = subprocess.run(
                ["adb", "-s", self.device_serial, "shell",
                 "dumpsys", "activity", "activities"],
                capture_output=True, timeout=5,
            )
            return view_tree_parser.extract_activity(
                (r.stdout or b"").decode("utf-8", errors="replace"),
                target_pkg=getattr(self, "package", "") or "",
            )
        except Exception:
            return ""

    def _task_stack_depth(self, package: str) -> int | None:
        """Count activity records in the foreground task stack for `package`.
        Returns None if the query fails (caller should err on the side of caution)."""
        if not package:
            return None
        try:
            r = subprocess.run(
                ["adb", "-s", self.device_serial, "shell",
                 "dumpsys", "activity", "activities"],
                capture_output=True, timeout=5,
            )
            out = (r.stdout or b"").decode("utf-8", errors="replace")
            pattern = re.compile(r"ActivityRecord\{[^}]*\s" + re.escape(package) + r"/", re.IGNORECASE)
            matches = pattern.findall(out)
            return len(matches) if matches else None
        except Exception:
            return None

    def _foreground_short(self) -> str | None:
        """Return the short name of the currently-foreground activity, or None.

        Parses `dumpsys activity activities` for topResumedActivity (or the
        legacy mResumedActivity format). Used by `_scan_capture` to track
        foreground changes during multi-polling (self-finishing detection).
        """
        try:
            r = subprocess.run(
                ["adb", "-s", self.device_serial, "shell",
                 "dumpsys activity activities"],
                capture_output=True, timeout=3,
            )
            out = (r.stdout or b"").decode("utf-8", errors="replace")
        except Exception:
            return None
        m = re.search(r"topResumedActivity=ActivityRecord\{[^}]*?\s\S+/([\w\.\$]+)", out)
        if not m:
            m = re.search(r"mResumedActivity:\s*ActivityRecord\{[^}]*?\s\S+/([\w\.\$]+)", out)
        if not m:
            return None
        return m.group(1).rsplit(".", 1)[-1]

    def _foreground_package(self) -> str:
        """Return the package name of the currently-foreground activity, or "".

        Parses `dumpsys activity activities` for `pkg/.ClassName` and extracts
        the `pkg` portion. Used by `_check_app_bounds` to detect when a tap
        sent us into an external app.
        """
        try:
            r = subprocess.run(
                ["adb", "-s", self.device_serial, "shell",
                 "dumpsys activity activities"],
                capture_output=True, timeout=3,
            )
            out = (r.stdout or b"").decode("utf-8", errors="replace")
        except Exception:
            return ""
        m = re.search(r"topResumedActivity=ActivityRecord\{[^}]*?\s(\S+)/[\w\.\$]+", out)
        if not m:
            m = re.search(r"mResumedActivity:\s*ActivityRecord\{[^}]*?\s(\S+)/[\w\.\$]+", out)
        return m.group(1) if m else ""

    # ───── Back / restart / bounds recovery ───────────────────────

    def _press_back(self) -> bool:
        """Press Back only when it's safe (won't exit the app).

        Returns True if Back was executed, False if skipped (caller may try
        another strategy — e.g., restart instead of Back).
        """
        current = self._current_activity()
        if current and getattr(self, "main_activity", None) and current == self.main_activity:
            logger.info("Skip Back: on main activity (%s) — would exit app", current)
            return False

        depth = self._task_stack_depth(getattr(self, "package", ""))
        if depth is not None and depth <= 1:
            logger.info("Skip Back: task stack depth=%d (Back would exit)", depth)
            return False

        subprocess.run(["adb", "-s", self.device_serial, "shell",
                        "input", "keyevent", "KEYCODE_BACK"],
                       capture_output=True, timeout=5)
        time.sleep(0.5)
        return True

    def _input_text(self, text: str) -> bool:
        """P0-7 (2026-05-04): EditText 에 키보드 입력 dispatch.

        adb shell input text 는 공백을 %s 로 받고 한글 등 non-ASCII 는
        직접 못 친다. 한글은 clipboard paste 로 우회.

        Returns True if dispatched, False on error/empty.
        """
        if not text:
            return False
        try:
            # ASCII-only 빠른 경로: input text 직접
            if text.isascii():
                # 공백은 %s, single quote escape
                escaped = text.replace(" ", "%s").replace("'", "\\'")
                subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     "input", "text", escaped],
                    capture_output=True, timeout=5,
                )
            else:
                # 한글 등: clipboard 사용 (am broadcast 로 paste)
                # 1) clipboard 채우기 — base64 encode
                import base64
                b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
                # write to a pull-able location; 가장 단순한 방법은
                # adb input keyevent + char-by-char 가 아니라 clipboard 사용
                subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     f"echo {b64} | base64 -d | tr -d '\\n' > /sdcard/.clip.tmp"],
                    capture_output=True, timeout=5, shell=False,
                )
                subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     "am", "broadcast", "-a", "clipper.set",
                     "-e", "text", text[:200]],
                    capture_output=True, timeout=5,
                )
                # paste keyevent
                subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     "input", "keyevent", "279"],   # KEYCODE_PASTE
                    capture_output=True, timeout=5,
                )
            time.sleep(0.4)
            return True
        except Exception as e:
            logger.debug("[input_text] failed: %s", e)
            return False

    def _soft_restart(self, package: str, main_activity: str) -> None:
        """Home + am start + clear tried_actions. Used when Back would exit the app.

        All adb calls are fault-tolerant — busy emulators sometimes miss a
        single `am start` and raise TimeoutExpired. That's a transient,
        recoverable glitch; the next main-loop iteration will retry via the
        foreground guard. Crashing the entire pipeline is wrong.
        """
        try:
            subprocess.run(["adb", "-s", self.device_serial, "shell",
                            "input", "keyevent", "KEYCODE_HOME"],
                           capture_output=True, timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("[soft-restart] HOME key timed out, continuing")
        time.sleep(1)
        try:
            subprocess.run(["adb", "-s", self.device_serial, "shell",
                            "am", "start", "-n", f"{package}/{main_activity}"],
                           capture_output=True, timeout=20)
        except subprocess.TimeoutExpired:
            logger.warning("[soft-restart] am start timed out — continuing, fg guard will retry")
        self.tried_actions.clear()
        time.sleep(2)

    def _check_app_bounds(self) -> None:
        """Bounce back to the target app if a tap sent us elsewhere.

        Strategy: if foreground package differs from target, press BACK up to
        3 times with short waits. If that doesn't recover (deep external
        flow), force-relaunch the target via `am start -n` as a last resort.

        No-op if `self.target_package` is unset or `self._allow_external` is
        True.
        """
        if not self.target_package or self._allow_external:
            return
        fg = self._foreground_package()
        if not fg or fg == self.target_package:
            return

        logger.info("[guard] Left target app (%s -> %s); bouncing back",
                    self.target_package, fg)
        self.trap_stats["app_exit_bounced"] += 1
        # 2026-09-12 (메가커피 실측): 네이티브 외부 앱 이탈(삼성페이 등) 의 trigger 도 학습.
        # WebView 외부 링크는 outbound_intent_guard 가 P0-10h 로 학습하지만 이 경로에는
        # 없어서, 강제 재실행 후 결제 화면이 새 canonical 로 잡히면 "결제하기" (submit
        # 보너스 8점) 를 다시 눌러 삼성페이에 5회 진입했다. 직전 액션 desc 를 블랙리스트
        # + 디스크 학습 → score -10 으로 재클릭 차단, 다음 잡에도 반영.
        learn = getattr(self, "_learn_action_desc", None)
        hist = getattr(self, "action_history", None) or []
        if learn and hist:
            last = hist[-1].get("desc") or hist[-1].get("event_desc") or ""
            if last:
                learn(last)
                self.trap_stats["app_exit_learned"] += 1

        for attempt in range(3):
            try:
                subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     "input", "keyevent", "KEYCODE_BACK"],
                    capture_output=True, timeout=3,
                )
            except Exception:
                break
            time.sleep(0.4)
            if self._foreground_package() == self.target_package:
                logger.info("[guard]   recovered after %d back press(es)", attempt + 1)
                return

        logger.info("[guard]   back presses failed; force-relaunching %s",
                    self.target_package)
        self.trap_stats["app_exit_relaunched"] += 1
        try:
            subprocess.run(
                ["adb", "-s", self.device_serial, "shell",
                 "monkey", "-p", self.target_package,
                 "-c", "android.intent.category.LAUNCHER", "1"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
        time.sleep(1.0)
