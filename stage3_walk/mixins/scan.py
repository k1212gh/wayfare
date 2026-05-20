"""Scan mixin — post-walk activity probes.

Two scans, run after the interactive walk loop ends:

- :py:meth:`ScanMixin._manifest_scan` forces ``am start -W`` on every activity
  still marked ``declared`` in the wireframe ScreenMap, so Stage 6 can flip them to
  ``probed``.
- :py:meth:`ScanMixin._deep_link_scan` retries still-uncaptured activities
  via their ``intent_filter`` deep links, which often reach gated screens that
  ``am start -n`` can't.

Both scans capture UI via :py:meth:`_scan_capture`, a multi-polling capture
tuned for self-finishing activities (onCreate → finish()).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import time

from .. import view_tree_parser

logger = logging.getLogger(__name__)


class ScanMixin:
    """Manifest + deep-link scans and their shared capture helper."""

    def _deep_link_scan(self, package: str) -> dict:
        """Second scan using intent_filter deep links.

        `am start -n pkg/activity` fails for most gated activities (Compose
        splash, login check, premium gate). But many of those same activities
        accept a VIEW intent with a scheme they declared. Launching via URI
        carries the "this is a legit entry" signal that `-n` doesn't.

        For each still-stub activity (status=declared or probed without UI):
          - Look at its intent_filters for VIEW action entries
          - Build candidate URIs (scheme + host + first pathPrefix/pathPattern)
          - Try up to 2 URIs per activity, verify fg match, capture if ok
        """
        screenmap_path = self.output_dir.parent / "output" / "screen_map.json"
        if not screenmap_path.exists():
            return {}
        try:
            screenmap = json.loads(screenmap_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        nodes = screenmap.get("screen_map", {}).get("graph", {}).get("nodes", [])

        already_captured = {s.get("activity", "") for s in self.states
                            if s.get("activity") and s.get("screenshot_path")}

        candidates: list[tuple[str, list[str]]] = []  # (activity, [uris])
        for n in nodes:
            act = n.get("activity", "")
            if not act or n.get("screen_id") == "system:external_entry":
                continue
            if act in already_captured:
                continue
            filters = n.get("intent_filters") or []
            uris = self._build_deep_link_uris(filters)
            if uris:
                candidates.append((act, uris[:2]))

        if not candidates:
            logger.info("[deep-scan] no deep-link candidates to probe")
            return {}
        logger.info("[deep-scan] probing %d activities via deep links", len(candidates))

        results: dict[str, dict] = {}
        captured_count = 0
        for i, (act, uris) in enumerate(candidates):
            if self._cancel_flag.exists():
                break
            for uri in uris:
                try:
                    r = subprocess.run(
                        ["adb", "-s", self.device_serial, "shell",
                         "am", "start", "-a", "android.intent.action.VIEW",
                         "-d", uri, "-W", package],
                        capture_output=True, timeout=8,
                    )
                    out = (r.stdout or b"").decode("utf-8", errors="replace")
                    if "Starting: Intent" not in out:
                        continue
                    time.sleep(1.8)
                    real_fg = self._current_activity() or ""
                    act_matches = (
                        real_fg == act
                        or real_fg.endswith("." + act.rsplit(".", 1)[-1])
                    )
                    if not act_matches:
                        continue
                    captured = self._scan_capture(act, len(self.states))
                    if captured:
                        self.states.append(captured)
                        captured_count += 1
                        results[act] = {"launched": True, "via": "deep_link",
                                        "uri": uri, "captured": True}
                        break  # success — don't try other URIs for this activity
                except subprocess.TimeoutExpired:
                    continue
                except Exception as e:
                    logger.debug("[deep-scan] %s via %s: %s", act, uri, e)
            if (i + 1) % 5 == 0:
                logger.info("[deep-scan] %d/%d probed, %d captured",
                            i + 1, len(candidates), captured_count)

        out_path = self.output_dir / "deep_link_scan.json"
        try:
            out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        logger.info("[deep-scan] complete: %d captured via deep links", captured_count)
        return results

    def _build_deep_link_uris(self, filters: list) -> list[str]:
        return view_tree_parser.build_deep_link_uris(filters)

    def _scan_capture(self, activity: str, idx: int) -> dict | None:
        """Lightweight UI capture for manifest-scan launched activities.

        **Multi-polling strategy** at cumulative t+0.2s / t+0.7s / t+1.7s after
        `am start -W` returns. Gated onboarding/login/param-check activities
        often call `finish()` in onCreate and redirect within ~1s — a single
        dump attempt at t+0s catches the redirected page, not the gated one.

        For each attempt we do (dump hierarchy + screencap), score by (view
        count) + big bonus if foreground activity matches the scan target,
        and keep the best pair. Early-exit on the first clearly-good hit
        (foreground matches AND >=5 views).

        Returns a minimal state dict compatible with utg_parser output, or
        None if every attempt returned empty.
        """
        from .. import u2_helper

        xml_path = self.output_dir / "raw" / f"scan_{idx:04d}.xml"
        png_path = self.output_dir / "raw" / f"scan_{idx:04d}.png"
        xml_path.parent.mkdir(parents=True, exist_ok=True)
        target_short = activity.rsplit(".", 1)[-1]

        best_views: list[dict] = []
        best_xml = ""
        best_png_tmp: str | None = None
        best_score = -1

        # Self-finishing detection — record foreground at first & last attempts.
        # If first matches the target but last doesn't, the activity called
        # finish() during the polling window → guarded onboarding / param-gate.
        fg_first: str | None = None
        fg_last: str | None = None

        for step, wait in enumerate((0.2, 0.5, 1.0)):
            time.sleep(wait)
            fg_short = self._foreground_short()
            if step == 0:
                fg_first = fg_short
            fg_last = fg_short
            fg_ok = (fg_short == target_short)
            xml = u2_helper.dump_hierarchy(self.device_serial, timeout=3.0)
            if not xml or "<hierarchy" not in xml:
                continue

            tmp_xml = xml_path.with_name(f"scan_{idx:04d}.t{step}.xml")
            tmp_xml.write_text(xml, encoding="utf-8")
            views = self._parse_ui_xml(tmp_xml)

            tmp_png = png_path.with_name(f"scan_{idx:04d}.t{step}.png")
            try:
                from .capture import _dismiss_ime_if_shown
                _dismiss_ime_if_shown(self.device_serial)
                subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     "screencap -p /sdcard/sa_scan.png"],
                    capture_output=True, timeout=4,
                )
                subprocess.run(
                    ["adb", "-s", self.device_serial, "pull",
                     "/sdcard/sa_scan.png", str(tmp_png)],
                    capture_output=True, timeout=4,
                )
            except Exception:
                pass

            # Score: foreground match dominates; view richness is secondary.
            # Foreground mismatch means the activity already redirected —
            # we may still want the dump but only as a fallback.
            score = len(views) + (1000 if fg_ok else 0)
            if score > best_score:
                best_score = score
                best_views = views
                best_xml = xml
                best_png_tmp = str(tmp_png) if tmp_png.exists() else None

            if fg_ok and len(views) >= 5:
                logger.debug("[scan_capture] %s early-exit at step=%d (fg_ok, %d views)",
                             target_short, step, len(views))
                break

        if best_xml:
            xml_path.write_text(best_xml, encoding="utf-8")
        if best_png_tmp:
            try:
                shutil.move(best_png_tmp, png_path)
            except Exception:
                pass
        for step in range(3):
            for ext in ("xml", "png"):
                tmp = xml_path.with_name(f"scan_{idx:04d}.t{step}.{ext}")
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass

        screenshot_path = str(png_path) if png_path.exists() else ""
        views = best_views

        self_finishing = (
            fg_first == target_short and
            fg_last is not None and
            fg_last != target_short
        )

        # Keep if we have EITHER views OR screenshot. A screenshot alone
        # is still valuable — LLM vision can reason about the screen even
        # without a parsed element tree.
        if not views and not screenshot_path:
            return None

        # Build structure_str — scan states are activity-specific probes,
        # not content-specific walks. Mixing activity into the hash prevents
        # 80+ scan states from collapsing to a handful of hashes.
        def _view_skel(v: dict) -> str:
            return f"{v.get('class','')}/{v.get('resource_id','')}"
        skel_parts = sorted(_view_skel(v) for v in views) if views else ["noviews"]
        skel = f"scan:{activity}|" + "|".join(skel_parts)
        structure_str = hashlib.sha256(skel.encode("utf-8")).hexdigest()

        state_str = f"scan_{idx:04d}_{hashlib.sha256(activity.encode()).hexdigest()[:12]}"
        result = {
            "state_str": state_str,
            "structure_str": structure_str,
            "activity": activity,
            "views": views,
            "screenshot_path": screenshot_path,
            "source": "scan",
        }
        if self_finishing:
            result["self_finishing"] = True
            result["redirect_to"] = fg_last
        return result

    def _manifest_scan(self, package: str, per_launch_timeout: int = 8) -> dict:
        """End-of-walk scan: force-launch every still-declared activity.

        Uses `am start -W` which runs as shell uid (uid 2000) so non-exported
        activities can still be started. For each activity we record whether
        the launch succeeded and what foreground component it produced, so
        Stage 6 can flip node status from `declared` → `probed`.
        """
        logger.info("[scan] _manifest_scan invoked (pkg=%s)", package)
        # Drop an immediate marker so we can tell scan even started, regardless
        # of later failures.
        try:
            (self.output_dir / "scan_started.flag").write_text("1", encoding="utf-8")
        except Exception:
            pass
        screenmap_path = self.output_dir.parent / "output" / "screen_map.json"
        if not screenmap_path.exists():
            logger.info("[scan] wireframe ScreenMap not found, skipping scan")
            return {}
        try:
            screenmap = json.loads(screenmap_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("[scan] failed to read wireframe: %s", e)
            return {}
        nodes = screenmap.get("screen_map", {}).get("graph", {}).get("nodes", [])
        # Only scan still-declared activities. Use `capture_priority` from
        # activity_classifier:
        #   A = user-facing screen → scan aggressively
        #   B = plumbing / trampoline → SKIP
        #   C = deep-link only → skip (intent_filter suffices)
        declared = []
        skipped_b = 0
        skipped_c = 0
        for n in nodes:
            if n.get("status") != "declared":
                continue
            if not n.get("activity"):
                continue
            if n.get("screen_id", "") == "system:external_entry":
                continue
            prio = n.get("capture_priority", "A")
            if prio == "B":
                skipped_b += 1
                continue
            if prio == "C":
                skipped_c += 1
                continue
            declared.append(n.get("activity", ""))
        if skipped_b or skipped_c:
            logger.info("[scan] classification skip: %d plumbing (B), %d deeplink (C)",
                        skipped_b, skipped_c)
        if not declared:
            logger.info("[scan] no declared activities to probe")
            return {}
        logger.info("[scan] probing %d declared activities via am start -W", len(declared))

        # Pre-scan cleanup: force-stop other apps that could be sitting in
        # foreground and poison our captures.
        for poison in ("org.chromium.chrome", "com.android.chrome",
                       "com.google.android.apps.chrome", "com.android.settings",
                       "com.google.android.googlequicksearchbox"):
            subprocess.run(
                ["adb", "-s", self.device_serial, "shell",
                 "am", "force-stop", poison],
                capture_output=True, timeout=5,
            )

        # Parent task warm-up — many Activities (Settings, Preferences, nested
        # detail screens) require a valid parent task to render.
        main = getattr(self, "main_activity", None)
        if main:
            subprocess.run(
                ["adb", "-s", self.device_serial, "shell",
                 "am", "start", "-n", f"{package}/{main}"],
                capture_output=True, timeout=8,
            )
            time.sleep(1.5)

        results: dict[str, dict] = {}
        launched = 0
        scan_budget_s = 420  # 7 min hard cap
        scan_start = time.time()
        # Track which activities we've already captured UI for — includes
        # both the interactive-loop states and scan captures so far. Used by
        # the focus_mismatch tolerance below to avoid re-capturing the same
        # redirect landing page 15 times.
        captured_activities: set[str] = {
            s.get("activity", "") for s in self.states
            if s.get("activity") and s.get("screenshot_path")
        }
        for i, act in enumerate(declared):
            if self._cancel_flag.exists():
                logger.info("[scan] cancel flag set, stopping at %d/%d", i, len(declared))
                break
            if time.time() - scan_start > scan_budget_s:
                logger.info("[scan] budget exceeded (%ds) — stopping at %d/%d",
                            scan_budget_s, i, len(declared))
                break
            try:
                r = subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     "am", "start", "-W", "-n", f"{package}/{act}"],
                    capture_output=True, timeout=per_launch_timeout,
                )
                out = (r.stdout or b"").decode("utf-8", errors="replace") + \
                      (r.stderr or b"").decode("utf-8", errors="replace")
                # Hard-failure signals (Activity truly unreachable / permission denied).
                hard_fail = (
                    "Error type" in out
                    or "Security exception" in out
                    or "Unable to find activity" in out
                    or "does not exist" in out
                    or "Activity not started, unable to resolve" in out
                )
                starting_ok = "Starting: Intent" in out
                if hard_fail or (not starting_ok and r.returncode != 0):
                    results[act] = {"launched": False, "reason": out.strip()[:240]}
                    continue
                fg = act
                for line in out.splitlines():
                    if line.startswith("Activity:"):
                        raw = line.split(":", 1)[1].strip()
                        if "/" in raw:
                            p, a = raw.split("/", 1)
                            if a.startswith("."):
                                a = p + a
                            fg = a
                        break
                soft_fail = r.returncode != 0
                # Wait for settle, then VERIFY the real foreground. am start -W
                # returns the launched activity name even when it finished and
                # Android returned to a different foreground.
                time.sleep(1.5)
                real_fg = self._current_activity() or ""
                act_matches = (
                    real_fg == act
                    or real_fg.endswith("." + act.rsplit(".", 1)[-1])
                )
                captured = None
                redirect_captured = False
                if act_matches:
                    captured = self._scan_capture(act, len(self.states))
                    if captured:
                        self.states.append(captured)
                        captured_activities.add(act)
                elif (
                    real_fg
                    and real_fg.startswith(package)
                    and real_fg not in captured_activities
                ):
                    # focus_mismatch tolerance: the launch redirected us to ANOTHER
                    # screen inside the target app that we haven't captured yet
                    # (e.g. CitySelectionActivity → DeskClock, or SettingsActivity
                    # → TitanViewAlarmsActivity). That landing is a legitimate
                    # app screen; keep it, tagged with redirect metadata.
                    redirect = self._scan_capture(real_fg, len(self.states))
                    if redirect:
                        redirect["source"] = "scan_redirect"
                        redirect["attempted_target"] = act
                        self.states.append(redirect)
                        captured_activities.add(real_fg)
                        captured = redirect
                        redirect_captured = True
                results[act] = {
                    "launched": True,
                    "foreground": fg,
                    "real_foreground": real_fg,
                    "soft_fail": soft_fail,
                    "captured": bool(captured),
                    "focus_mismatch": not act_matches,
                    "redirect_captured": redirect_captured,
                }
                launched += 1
                time.sleep(0.15)
            except subprocess.TimeoutExpired:
                results[act] = {"launched": False, "reason": "timeout"}
            except Exception as e:
                results[act] = {"launched": False, "reason": str(e)[:120]}
            if (i + 1) % 10 == 0:
                logger.info("[scan] %d/%d probed, %d launched", i + 1, len(declared), launched)

        # 2nd pass — retry activities that launched but failed UI capture.
        retry_targets = [
            a for a, r in results.items()
            if r.get("launched") and not r.get("captured")
        ]
        if retry_targets:
            logger.info("[scan] retry pass: %d activities missed UI capture", len(retry_targets))
            retried = 0
            for i, act in enumerate(retry_targets):
                if self._cancel_flag.exists():
                    break
                try:
                    r = subprocess.run(
                        ["adb", "-s", self.device_serial, "shell",
                         "am", "start", "-W", "-n", f"{package}/{act}"],
                        capture_output=True, timeout=per_launch_timeout,
                    )
                    out = (r.stdout or b"").decode("utf-8", errors="replace")
                    if "Starting: Intent" not in out:
                        continue
                    time.sleep(2.5)  # longer settle for retry
                    real_fg = self._current_activity() or ""
                    act_matches_retry = (
                        real_fg == act
                        or real_fg.endswith("." + act.rsplit(".", 1)[-1])
                    )
                    if act_matches_retry:
                        captured = self._scan_capture(act, len(self.states))
                        if captured:
                            self.states.append(captured)
                            captured_activities.add(act)
                            results[act]["captured"] = True
                            results[act]["retry_ok"] = True
                            retried += 1
                    elif (
                        real_fg
                        and real_fg.startswith(package)
                        and real_fg not in captured_activities
                    ):
                        # Retry also benefits from focus_mismatch tolerance:
                        # some redirects only settle after the longer 2.5s
                        # sleep (Compose async init).
                        redirect = self._scan_capture(real_fg, len(self.states))
                        if redirect:
                            redirect["source"] = "scan_redirect_retry"
                            redirect["attempted_target"] = act
                            self.states.append(redirect)
                            captured_activities.add(real_fg)
                            results[act]["captured"] = True
                            results[act]["retry_ok"] = True
                            results[act]["redirect_captured"] = True
                            retried += 1
                except subprocess.TimeoutExpired:
                    pass
                except Exception as e:
                    logger.debug("[scan-retry] %s: %s", act, e)
                if (i + 1) % 10 == 0:
                    logger.info("[scan-retry] %d/%d, %d recovered", i + 1, len(retry_targets), retried)
            logger.info("[scan-retry] complete: %d recovered", retried)

        out_path = self.output_dir / "manifest_scan.json"
        try:
            out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning("[scan] could not save manifest_scan.json: %s", e)
        # Force-stop so the scan residue doesn't leak into the next run
        subprocess.run(["adb", "-s", self.device_serial, "shell",
                        "am", "force-stop", package],
                       capture_output=True, timeout=10)
        captured_total = sum(1 for r in results.values() if r.get("captured"))
        logger.info("[scan] complete: %d/%d launched, %d UI-captured",
                    launched, len(declared), captured_total)
        return results
