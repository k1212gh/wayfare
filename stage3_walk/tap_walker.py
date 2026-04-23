"""Smart Walker — priority-based walk that avoids redundant visits.

Instead of DroidBot's blind dfs_greedy, this controller:
1. Tracks visited states by structure_str (not just state_str)
2. Prioritizes clicks that lead to NEW screens (unseen structure)
3. Penalizes actions on already-walked pages
4. Auto-backs out of dead-end loops
5. Favors deeper navigation over lateral (list item) walk
"""

import json
import logging
import os
import re
import subprocess
import time
from collections import defaultdict
from pathlib import Path

from . import view_tree_parser
from .mixins import CaptureMixin, DeviceSessionMixin, GuardsMixin, ScanMixin

logger = logging.getLogger(__name__)


class TapWalker(ScanMixin, CaptureMixin, GuardsMixin, DeviceSessionMixin):
    """Step-by-step app walk with unseen-driven priority."""

    def __init__(self, device_serial: str, apk_path: str, output_dir: str,
                 timeout: int = 600, max_events: int = 500, framework: str = "xml"):
        self.device_serial = device_serial
        self.apk_path = apk_path
        self.output_dir = Path(output_dir)
        self.timeout = timeout
        self.max_events = max_events
        self.framework = framework

        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "states").mkdir(exist_ok=True)

        # Cancellation: dashboard/backend writes <tour>/cancel.flag, sibling of output_dir
        self._cancel_flag = self.output_dir.parent / "cancel.flag"
        # Pause: <tour>/paused.json — walker writes on auto-detect, user removes via Resume
        self._paused_file = self.output_dir.parent / "paused.json"
        self._pause_max_seconds = 600  # 10 min timeout

        # Framework-aware UI Extractor
        from .view_tree_readers import get_reader
        self.extractor = get_reader(framework)
        logger.info("Using %s extractor for framework=%s",
                    type(self.extractor).__name__, framework)

        # 3-Level State Hasher — defaults now come from env (see ScreenSigner
        # docstring). L3 threshold lowered to 0.82 and L1 is authoritative when
        # present, so DeskClock-style Fragment tabs stop collapsing into one node.
        from .screen_signer import ScreenSigner
        self.hasher = ScreenSigner()

        # Walk state
        self.visited_structures: dict[str, int] = defaultdict(int)  # canonical_id → visit count
        self.visited_screens: set[str] = set()
        self.canonical_map: dict[str, str] = {}  # raw_hash → canonical_id
        self.action_history: list[dict] = []
        self.tried_actions: dict[str, set[str]] = defaultdict(set)  # canonical_id → set of tried action descs
        self.states: list[dict] = []
        self.transitions: list[dict] = []
        self.back_count = 0
        self.stall_count = 0
        self.last_canonical = ""
        self.hash_stats = {"l1_matches": 0, "l2_matches": 0, "l3_matches": 0, "new_screens": 0}
        # Trap instrumentation
        self.trap_stats: dict[str, int] = defaultdict(int)
        # Per-canonical RecyclerView tap counter — limit to 3 before forcing scroll
        self.rv_taps_per_screen: dict[str, int] = defaultdict(int)

        # ---- Static-graph-guided walk plan ----
        # Loaded in run() from <tour>/output/screen_map.json (wireframe ScreenMap).
        # Priority list of Activity FQNs we still want to visit via direct
        # am start (explicit intent).  Filled from nodes with status=declared.
        self.plan_targets: list[str] = []
        self.plan_visited: set[str] = set()
        self.plan_failed: set[str] = set()

        # ---- Package guard ----
        # Set from APK package name on run() entry. Used to detect when a tap
        # sent us into an external app (Gmail via "Send feedback", Chrome via
        # a privacy-policy link, etc.) so we can bounce back and not waste
        # walk budget in someone else's UI.
        self.target_package: str = ""
        # Disable guard via env var when the target app legitimately uses
        # Chrome Custom Tabs for OAuth etc. — set ALLOW_EXTERNAL=1 to opt out.
        self._allow_external = os.environ.get("ALLOW_EXTERNAL", "").lower() in (
            "1", "true", "yes",
        )

    def run(self) -> dict:
        """Run the smart walk loop."""
        import subprocess

        logger.info("Smart Walker: device=%s, timeout=%ds, max_events=%d",
                     self.device_serial, self.timeout, self.max_events)

        # Medium-term mitigation: turn off system-level animation scales so
        # window transitions finish instantly. Helps uiautomator's idle
        # detection AND speeds the walker up in general. App-controlled
        # animations (ticking clocks, ad carousels) are NOT affected — those
        # are handled by the uiautomator2 path in u2_helper.
        from . import u2_helper
        u2_helper.disable_animations(self.device_serial)
        # Warm up u2 — first connect triggers agent APK install on the device
        # (one-time), so better to pay that cost upfront than mid-walk.
        u2_helper._connect_u2(self.device_serial)
        try:
            return self._run_body()
        finally:
            u2_helper.restore_animations(self.device_serial)

    def _run_body(self) -> dict:
        """Body of run(), wrapped so the caller can set up/tear down."""
        import subprocess

        start_time = time.time()
        event_count = 0

        apk_path = Path(self.apk_path).resolve()

        # Figure out target package upfront so we can check "already installed" on real devices
        from androguard.core.apk import APK
        apk = APK(self.apk_path)
        package = apk.get_package()
        # Stash for the package guard (_check_app_bounds). Set to empty means
        # guard is effectively off regardless of the ALLOW_EXTERNAL flag.
        self.target_package = package or ""

        is_emulator = self._is_emulator_device()

        # If the same package is already installed on the device/emulator,
        # skip reinstall — preserves login state and avoids
        # INSTALL_FAILED_DUPLICATE_PACKAGE when a prior session is pending.
        already_installed = False
        if package:
            try:
                check = subprocess.run(
                    ["adb", "-s", self.device_serial, "shell", "pm", "path", package],
                    capture_output=True, timeout=10)
                if check.returncode == 0 and (check.stdout or b"").decode("utf-8", errors="replace").strip().startswith("package:"):
                    already_installed = True
            except Exception:
                pass

        if is_emulator and not already_installed:
            # Emulator: install fresh only if the package isn't already there.
            sibling_apks = list(apk_path.parent.glob("*.apk"))
            if len(sibling_apks) > 1:
                logger.info("Emulator — installing %d split APKs: %s",
                             len(sibling_apks), [a.name for a in sibling_apks])
                install_result = subprocess.run(
                    ["adb", "-s", self.device_serial, "install-multiple", "-r"]
                    + [str(a) for a in sibling_apks],
                    capture_output=True, timeout=120)
            else:
                logger.info("Emulator — installing single APK: %s", apk_path.name)
                install_result = subprocess.run(
                    ["adb", "-s", self.device_serial, "install", "-r", str(apk_path)],
                    capture_output=True, timeout=60)
            install_stdout = (install_result.stdout or b"").decode("utf-8", errors="replace")
            install_stderr = (install_result.stderr or b"").decode("utf-8", errors="replace")
            if install_result.returncode != 0 or "Success" not in install_stdout:
                raise RuntimeError(
                    f"APK install failed (rc={install_result.returncode}): "
                    f"{install_stderr.strip() or install_stdout.strip()}"
                )
            logger.info("Install OK: %s", install_stdout.strip().splitlines()[-1] if install_stdout else "Success")
        else:
            # Real device: do NOT reinstall, but verify the app is already there
            if not package:
                raise RuntimeError("Could not read package name from APK")
            check = subprocess.run(
                ["adb", "-s", self.device_serial, "shell", "pm", "path", package],
                capture_output=True, timeout=10)
            out = (check.stdout or b"").decode("utf-8", errors="replace").strip()
            if check.returncode != 0 or not out.startswith("package:"):
                raise RuntimeError(
                    f"Real device: app '{package}' is not installed. "
                    "Install it on the device first, or use an emulator."
                )
            logger.info("Package %s already installed — skipping install (preserves login state)", package)

        # (package/main_activity for launch)
        main_activity = apk.get_main_activity()
        if main_activity and not main_activity.startswith(package):
            if main_activity.startswith("."):
                main_activity = package + main_activity

        self.package = package
        self.main_activity = main_activity

        # Pre-grant runtime permissions so first-launch permission dialogs
        # don't block walk.  Silently ignored if a permission isn't
        # applicable (adb returns non-zero but we don't care).
        self._grant_runtime_permissions(package)

        # Framework-specific device setup (e.g. Flutter enables Semantics).
        try:
            self.extractor.prepare_device(self.device_serial)
        except Exception as e:
            logger.warning("Extractor prepare_device failed: %s", e)

        subprocess.run(["adb", "-s", self.device_serial, "shell",
                        "am", "start", "-n", f"{package}/{main_activity}"],
                       capture_output=True, timeout=10)
        time.sleep(2)

        # Tier-1 bootstrap: visit obvious navigation entry points BEFORE random walk
        # starts.  This guarantees bottom-tab screens and drawer contents get captured.
        try:
            self._bootstrap_navigation(package, main_activity)
        except Exception as e:
            logger.warning("Bootstrap routine failed: %s", e)

        # ---- Load walk plan from static wireframe ScreenMap ----
        # If Stage 2.5 wrote screen_map.json, extract declared activities
        # (statically reachable ones first) as direct-launch targets.
        try:
            self._load_plan(package)
            if self.plan_targets:
                logger.info("[plan] Loaded %d declared activities as direct-launch targets",
                             len(self.plan_targets))
        except Exception as e:
            logger.warning("Plan load failed: %s", e)

        empty_count = 0  # Track consecutive empty UI dumps
        out_of_app_count = 0  # Guard: if we can't return to target app, bail

        while event_count < self.max_events and (time.time() - start_time) < self.timeout:
            # 0. Cancellation check (cooperative, from /api/tours/{id}/stop)
            if self._cancel_flag.exists():
                logger.info("Cancel flag detected — stopping walk at event %d", event_count)
                break

            # 0a. Pause check (manual or auto-detected login) — wait until user Resumes
            if self._paused_file.exists():
                if not self._wait_for_resume():
                    break  # cancelled during pause or timed out

            # 1. Capture current state
            state = self._capture_screen(event_count)
            if not state:
                break

            # 1b. Auto-detect login / auth screen → request user input
            if self._detect_user_input_needed(state):
                self._request_user_input(state)
                if not self._wait_for_resume():
                    break
                # after resume, re-capture to avoid stale state
                state = self._capture_screen(event_count)
                if not state:
                    break

            # 1a. Foreground guard: if current activity belongs to a different app
            # (user pressed back to launcher, or our tap opened another app), relaunch.
            activity = state.get("activity", "") or ""
            if activity and activity != "unknown":
                # Primary match: activity FQN under package namespace
                in_target = (activity == package or activity.startswith(package + "."))
                # Fallback: activity listed in the APK's manifest even if its
                # FQN uses a legacy namespace (e.g. DeskClock's
                # com.android.deskclock.* activities under the
                # com.google.android.deskclock package). The manifest is
                # authoritative — if it declares the activity, it's ours.
                if not in_target:
                    declared_acts = getattr(self, "_declared_activities", None)
                    if declared_acts is None:
                        declared_acts = self._load_declared_activities()
                        self._declared_activities = declared_acts
                    if activity in declared_acts:
                        in_target = True
                if not in_target:
                    out_of_app_count += 1
                    self.trap_stats["out_of_app_relaunch"] += 1
                    logger.warning("[TRAP] Out of target app: activity=%s (expected %s). Relaunching [%d]",
                                    activity, package, out_of_app_count)
                    if out_of_app_count >= 5:
                        logger.error("Gave up: foreground never returned to %s", package)
                        break
                    try:
                        subprocess.run(["adb", "-s", self.device_serial, "shell",
                                        "am", "force-stop", package],
                                       capture_output=True, timeout=5)
                        time.sleep(0.5)
                        subprocess.run(["adb", "-s", self.device_serial, "shell",
                                        "am", "start", "-n", f"{package}/{main_activity}"],
                                       capture_output=True, timeout=20)
                    except subprocess.TimeoutExpired:
                        logger.warning("[fg-guard] relaunch adb timed out, retrying next iter")
                    time.sleep(2)
                    self.back_count = 0
                    self.stall_count = 0
                    event_count += 1
                    continue
                else:
                    out_of_app_count = 0

            # Detect app crash: views=0 means UI dump failed
            if len(state.get("views", [])) == 0:
                empty_count += 1
                logger.warning("Empty UI dump (%d consecutive)", empty_count)
                if empty_count >= 3:
                    logger.info("App likely crashed, restarting...")
                    try:
                        subprocess.run(["adb", "-s", self.device_serial, "shell",
                                        "am", "force-stop", package],
                                       capture_output=True, timeout=5)
                        time.sleep(1)
                        subprocess.run(["adb", "-s", self.device_serial, "shell",
                                        "am", "start", "-n", f"{package}/{main_activity}"],
                                       capture_output=True, timeout=20)
                    except subprocess.TimeoutExpired:
                        logger.warning("[crash-restart] adb timed out")
                    empty_count = 0
                    self.tried_actions.clear()
                    time.sleep(3)
                event_count += 1
                continue
            empty_count = 0

            # 3-Level hashing: find canonical screen ID
            fp = self.hasher.compute_fingerprint(
                state.get("views", []),
                state.get("activity", ""),
                state.get("screenshot_path", ""),
            )
            match = self.hasher.find_match(fp)
            if match:
                canonical_id = match
                # Track which level matched for stats
                known = self.hasher.known_fingerprints[match]
                if fp.structural_hash == known.structural_hash:
                    self.hash_stats["l1_matches"] += 1
                elif fp.perceptual_hash and known.perceptual_hash:
                    self.hash_stats["l2_matches"] += 1
                else:
                    self.hash_stats["l3_matches"] += 1
            else:
                canonical_id = f"screen_{len(self.hasher.known_fingerprints):03d}"
                self.hasher.register(canonical_id, fp)
                self.hash_stats["new_screens"] += 1
                logger.info("  NEW screen: %s (activity=%s, elements=%d)",
                            canonical_id, state.get("activity", "?"), fp.widget_count)

            state["canonical_id"] = canonical_id
            state["structure_str"] = fp.structural_hash
            state["state_str"] = canonical_id  # Use canonical as state_str

            # Track visits by canonical ID
            self.visited_structures[canonical_id] += 1
            self.visited_screens.add(canonical_id)

            # Mark activity as visited in the plan (so we don't re-launch it)
            cur_activity = state.get("activity", "") or ""
            if cur_activity:
                self.plan_visited.add(cur_activity)

            # 1b2. Periodic hub refresh — every 25 events, tap a fresh bottom-nav
            # tab to expose the walker to screens that are only reachable via
            # hub rotation (Home/Search/Library/Browse). Without this, greedy
            # top-of-stack walk can stay in one hub (Home) forever.
            if event_count > 0 and event_count % 25 == 0 and not self.stall_count:
                bottom_tabs = [v for v in state.get("views", [])
                               if "bottomnav" in (v.get("class", "") + v.get("parent_class", "")).lower()
                               and v.get("clickable")]
                if len(bottom_tabs) >= 2:
                    tab_idx = (event_count // 25) % len(bottom_tabs)
                    tab = bottom_tabs[tab_idx]
                    logger.info("[hub-refresh] event=%d tab %d/%d desc=%s",
                                event_count, tab_idx + 1, len(bottom_tabs),
                                (tab.get("content_desc") or tab.get("text") or "?")[:30])
                    self._tap_view(tab)
                    event_count += 1
                    time.sleep(0.8)
                    continue

            # 1c. Overlay handling: distinguish popup menu (walk) vs
            #     blocking dialog (dismiss). Popups contain app-specific
            #     tappable items (Settings, Share, …) we want to visit.
            if state.get("is_dialog"):
                views_cur = state.get("views", [])
                is_popup = self._detect_popup_menu(views_cur)
                if is_popup:
                    popup_items = self._popup_items(views_cur)
                    tried = self.tried_actions.get(canonical_id, set())
                    # Prefer popup items that haven't been tapped yet
                    untried = [pi for pi in popup_items
                               if self.extractor.get_action_desc(pi) not in tried]
                    target = untried[0] if untried else (popup_items[0] if popup_items else None)
                    if target:
                        desc = target.get("content_desc") or target.get("text") or target.get("resource_id") or "?"
                        logger.info("[popup] tapping menu item: %s", desc[:40])
                        self.trap_stats["popup_item_tapped"] += 1
                        self.tried_actions[canonical_id].add(self.extractor.get_action_desc(target))
                        self._tap_view(target)
                        event_count += 1
                        time.sleep(0.8)
                        continue
                    # No popup items extractable — dismiss as fallback
                logger.info("[TRAP] Dialog detected on %s — attempting dismiss", canonical_id)
                self.trap_stats["dialog_dismissed"] += 1
                if self._dismiss_dialog(state):
                    event_count += 1
                    time.sleep(0.8)
                    continue

            # 2. Detect stall (same canonical screen 3+ times in a row)
            if canonical_id == self.last_canonical:
                self.stall_count += 1
            else:
                self.stall_count = 0
            self.last_canonical = canonical_id

            if self.stall_count >= 2:
                logger.info("Stall on %s (%d times), pressing back", canonical_id, self.stall_count)
                did_back = self._press_back()
                if not did_back:
                    # Back would exit the app — soft restart instead
                    self._soft_restart(package, main_activity)
                    self.back_count = 0
                    self.stall_count = 0
                    event_count += 1
                    continue
                self.back_count += 1
                event_count += 1
                time.sleep(0.5)

                if self.back_count >= 3:
                    # Before soft-restart, try to directly launch a planned activity —
                    # this uses the static ScreenMap as a navigation map, reaching activities
                    # that UI walk alone would never find.
                    if self._try_plan_launch(package):
                        self.back_count = 0
                        self.stall_count = 0
                        event_count += 1
                        continue
                    logger.info("Too many backs — soft-restart for fresh walk")
                    self._soft_restart(package, main_activity)
                    self.back_count = 0
                    self.stall_count = 0
                continue

            # 3. Get actionable elements and score them
            actions = self._get_scored_actions(state)

            if not actions:
                # No tap targets available (either real dead-end OR UI dump failed
                # due to persistent animations).  Try navigator launch first — this
                # bypasses UI and reaches declared activities via `am start`.
                if self._try_plan_launch(package):
                    event_count += 1
                    continue
                if not self._press_back():
                    self._soft_restart(package, main_activity)
                    self.back_count = 0
                    event_count += 1
                    continue
                self.back_count += 1
                event_count += 1
                time.sleep(0.5)
                continue

            # If best score is very low but there are untried actions, still try them
            if actions[0]["score"] < -2.0:
                untried = [a for a in actions if a.get("desc", "") not in self.tried_actions.get(canonical_id, set())]
                if untried:
                    actions = untried  # Use untried actions even if scored low
                else:
                    logger.info("All %d actions tried on %s, backing out", len(actions), canonical_id)
                    if not self._press_back():
                        self._soft_restart(package, main_activity)
                        self.back_count = 0
                        event_count += 1
                        continue
                    self.back_count += 1
                    event_count += 1
                    time.sleep(0.5)
                    continue

            # 4. Pick action — diversify among top 3 untried to escape greedy traps.
            # Pure greedy keeps hitting the same high-scored item when that
            # item's result state gets coalesce'd back to the same canonical.
            # Rotating among the top-3 untried exposes the walker to more
            # branches in Compose apps where many items are close in score.
            untried_top = [a for a in actions[:5]
                           if a.get("desc", "") not in self.tried_actions.get(canonical_id, set())]
            if not untried_top:
                untried_top = actions[:3]
            # Round-robin by event count so each visit to the same state picks
            # a different top candidate.
            best = untried_top[event_count % len(untried_top)]

            # 4b. RecyclerView trap: cap list-item taps per screen.
            # After 3 item taps on the same canonical, force a scroll-down so
            # we see new content instead of tapping identical-looking items.
            best_view = best.get("view") or best
            parent_cls = str(best_view.get("parent_class", "")).lower()
            is_rv_item = "recyclerview" in parent_cls or "listview" in parent_cls
            if is_rv_item:
                if self.rv_taps_per_screen[canonical_id] >= 3:
                    logger.info("[TRAP] RV cap on %s — scrolling instead of tapping item", canonical_id)
                    self.trap_stats["rv_cap_scrolled"] += 1
                    self._scroll_down(state)
                    event_count += 1
                    time.sleep(0.7)
                    continue
                self.rv_taps_per_screen[canonical_id] += 1
            logger.info("Event %d: %s on %s (score=%.2f, visits=%d)",
                        event_count, best["action"], best.get("desc", "?"),
                        best["score"], self.visited_structures[canonical_id])

            # 5. Execute action + record as tried
            prev_canonical = canonical_id
            self.tried_actions[canonical_id].add(best.get("desc", ""))
            self._execute_action(best, state)
            event_count += 1
            time.sleep(0.7)  # Faster walk

            # 5.5. Package guard — if the action sent us into a different app
            # (Gmail via "Send feedback", Chrome via "Privacy policy",
            # share-sheet, etc.), bounce back to the target package so the
            # next iteration's unseen scoring works on our own UI, not
            # someone else's. Disabled via ALLOW_EXTERNAL=1.
            if not self._allow_external:
                self._check_app_bounds()

            # 6. Capture new state, compute its canonical ID, record transition
            new_screen = self._capture_screen(event_count)
            if new_screen:
                # Hash the new state to get its canonical ID
                new_fp = self.hasher.compute_fingerprint(
                    new_screen.get("views", []),
                    new_screen.get("activity", ""),
                    new_screen.get("screenshot_path", ""),
                )
                new_match = self.hasher.find_match(new_fp)
                if new_match:
                    new_canonical = new_match
                else:
                    new_canonical = f"screen_{len(self.hasher.known_fingerprints):03d}"
                    self.hasher.register(new_canonical, new_fp)
                    self.hash_stats["new_screens"] += 1
                    logger.info("  -> NEW screen: %s", new_canonical)

                new_screen["canonical_id"] = new_canonical
                new_screen["state_str"] = new_canonical

                if new_canonical != prev_canonical:
                    self.transitions.append({
                        "from_screen": prev_canonical,
                        "to_screen": new_canonical,
                        "event_type": best["action"],
                        "event_str": best.get("desc", ""),
                    })

        # Save results
        elapsed = time.time() - start_time
        logger.info("Walk done: %d events, %d unique screens, %.0fs",
                     event_count, len(self.visited_structures), elapsed)
        # Trap summary (one line per kind, zero counts omitted)
        trap_summary = {k: v for k, v in self.trap_stats.items() if v}
        if trap_summary:
            logger.info("Traps hit: %s", trap_summary)

        # Final pass: probe every still-declared activity via am start -W so
        # coverage reflects reachability, not just UI-walked screens.
        try:
            self._manifest_scan(package)
        except Exception as e:
            logger.warning("Manifest scan failed: %s", e)

        # Second pass via deep links — many gated activities reject `-n` but
        # accept a VIEW intent with their registered scheme. This recovers
        # 5-15 extra activities for apps with rich intent_filter declarations
        # (Spotify, TikTok, YouTube, etc).
        try:
            self._deep_link_scan(package)
        except Exception as e:
            logger.warning("Deep link scan failed: %s", e)

        return self._save_results(package, elapsed, event_count)

    def _get_scored_actions(self, state: dict) -> list[dict]:
        """Score actions using framework-specific Extractor.

        Delegates to self.extractor.is_actionable() + score_action().
        """
        views = state.get("views", [])
        canonical = state.get("canonical_id", state.get("structure_str", ""))
        visit_count = self.visited_structures.get(canonical, 0)
        tried = self.tried_actions.get(canonical, set())
        context = {"canonical": canonical, "visit_count": visit_count, "tried_actions": tried}

        actions = []
        seen_labels = set()

        for view in views:
            if not self.extractor.is_actionable(view):
                continue

            # Coalescelicate by label (text > desc > rid > bounds for WebView)
            label = (view.get("text") or view.get("content_desc")
                     or view.get("resource_id") or str(view.get("bounds", "")))
            if label in seen_labels:
                continue
            seen_labels.add(label)

            score = self.extractor.score_action(view, context)
            action_desc = self.extractor.get_action_desc(view)
            if view.get("clickable"):
                action_type = "click"
            elif view.get("long_clickable"):
                action_type = "longclick"
            elif view.get("scrollable"):
                action_type = "scroll"
            else:
                action_type = "click"

            actions.append({
                "action": action_type,
                "view": view,
                "score": score,
                "desc": action_desc,
                "bounds": view.get("bounds", {}),
            })

        # Add ViewPager horizontal swipe as a separate synthetic action per pager
        # found on screen. Many apps use ViewPager(2) for tab pagination and
        # bottom-nav isn't the only way — some swipe-only screens are invisible
        # to tap-only walk.
        pagers_seen = set()
        for view in views:
            cls = str(view.get("class", "") or "")
            parent = str(view.get("parent_class", "") or "")
            is_pager = (
                "ViewPager" in cls or "ViewPager2" in cls
                or "HorizontalScroll" in cls
            )
            if not is_pager:
                continue
            # coalescee by bounds so a pager containing child pagers doesn't double-count
            b = str(view.get("bounds", ""))
            if b in pagers_seen:
                continue
            pagers_seen.add(b)
            desc = f"swipe_horizontal pager@{b[:24]}"
            if desc in tried:
                continue
            actions.append({
                "action": "swipe_horizontal",
                "view": view,
                "score": 2.0,  # moderate priority, below untried taps
                "desc": desc,
                "bounds": view.get("bounds", {}),
            })

        actions.sort(key=lambda a: a["score"], reverse=True)
        return actions


    def _execute_action(self, action: dict, state: dict) -> None:
        """Execute a UI action via ADB."""
        import subprocess

        bounds = action.get("bounds", {})
        if not bounds:
            view = action.get("view", {})
            bounds = view.get("bounds", {})

        if isinstance(bounds, dict):
            x = (bounds.get("x1", 0) + bounds.get("x2", 0)) // 2
            y = (bounds.get("y1", 0) + bounds.get("y2", 0)) // 2
        elif isinstance(bounds, list) and len(bounds) == 2:
            x = (bounds[0][0] + bounds[1][0]) // 2
            y = (bounds[0][1] + bounds[1][1]) // 2
        elif isinstance(bounds, str):
            # Parse "[x1,y1][x2,y2]" format
            import re
            nums = re.findall(r'\d+', bounds)
            if len(nums) >= 4:
                x = (int(nums[0]) + int(nums[2])) // 2
                y = (int(nums[1]) + int(nums[3])) // 2
            else:
                return
        else:
            return

        if action["action"] == "click":
            subprocess.run(["adb", "-s", self.device_serial, "shell",
                            "input", "tap", str(x), str(y)],
                           capture_output=True, timeout=5)
        elif action["action"] == "longclick":
            # 700ms press — reliably triggers long-press handlers
            subprocess.run(["adb", "-s", self.device_serial, "shell",
                            "input", "swipe", str(x), str(y), str(x), str(y), "700"],
                           capture_output=True, timeout=5)
        elif action["action"] == "scroll":
            subprocess.run(["adb", "-s", self.device_serial, "shell",
                            "input", "swipe", str(x), str(y), str(x), str(y - 300), "300"],
                           capture_output=True, timeout=5)
        elif action["action"] == "swipe_horizontal":
            # ViewPager page-swipe. Direction based on last swipe to alternate.
            vw = action.get("view", {}) or {}
            b = vw.get("bounds", "") or bounds
            if isinstance(b, str):
                import re
                nums = re.findall(r'\d+', b)
                if len(nums) >= 4:
                    x1_px, x2_px = int(nums[0]), int(nums[2])
                    w = x2_px - x1_px
                else:
                    w = 800
            else:
                w = 800
            # Swipe from 80% width → 20% (right-to-left = next page)
            x_start = x + int(w * 0.3)
            x_end = x - int(w * 0.3)
            subprocess.run(["adb", "-s", self.device_serial, "shell",
                            "input", "swipe", str(x_start), str(y), str(x_end), str(y), "250"],
                           capture_output=True, timeout=5)

    _DISMISS_KEYWORDS = (
        "dismiss", "close", "cancel", "×", "x", "no", "skip",
        "닫기", "취소", "아니오", "나중에", "skip",
        "확인",  # rarely means close but often is primary action
    )

    def _dismiss_dialog(self, state: dict) -> bool:
        """Try to close an overlay dialog by clicking the most likely dismiss button.

        Priority:
          1. Button with "close"/"cancel"/"dismiss" text or content-desc
          2. ImageView at top-right (typical close X)
          3. Fallback: press back
        """
        views = state.get("views", [])

        # 1. Text-based match
        for v in views:
            if not v.get("clickable"):
                continue
            text = (v.get("text") or "").lower().strip()
            desc = (v.get("content_desc") or "").lower().strip()
            combined = text + " " + desc
            if any(kw in combined for kw in self._DISMISS_KEYWORDS):
                logger.info("Dismiss via button: %r", text or desc)
                self._tap_view(v)
                return True

        # 2. Top-right ImageView (common close X position)
        top_right_candidates = [
            v for v in views
            if v.get("clickable")
            and "imageview" in (v.get("class") or "").lower()
            and self._is_top_right(v.get("bounds"))
        ]
        if top_right_candidates:
            v = top_right_candidates[0]
            logger.info("Dismiss via top-right image: bounds=%s", v.get("bounds"))
            self._tap_view(v)
            return True

        # 3. Back fallback
        logger.info("Dismiss via Back key")
        return self._press_back()

    @staticmethod
    def _is_top_right(bounds) -> bool:
        return view_tree_parser.is_top_right(bounds)

    def _scroll_down(self, state: dict, distance: int = 800) -> None:
        """Swipe up to reveal more content below (scrolls the list down).

        Uses the center x-axis of the first scrollable view, or screen center
        as fallback.  Distance defaults to 800px which is ~1/3 of a standard screen.
        """
        scrollable = next(
            (v for v in state.get("views", []) if v.get("scrollable")),
            None,
        )
        # Default: middle of screen
        x, y_start, y_end = 540, 1600, 1600 - distance
        if scrollable:
            import re
            b = scrollable.get("bounds", "")
            nums = re.findall(r"\d+", str(b))
            if len(nums) >= 4:
                x1, y1, x2, y2 = (int(n) for n in nums[:4])
                x = (x1 + x2) // 2
                y_start = y1 + (y2 - y1) * 3 // 4
                y_end = max(y1 + 40, y_start - distance)
        subprocess.run(
            ["adb", "-s", self.device_serial, "shell",
             "input", "swipe", str(x), str(y_start), str(x), str(y_end), "300"],
            capture_output=True, timeout=5,
        )

    def _tap_view(self, view: dict) -> None:
        """Tap the center of a view's bounds."""
        import re
        bounds = view.get("bounds", "")
        if isinstance(bounds, str):
            nums = re.findall(r"\d+", bounds)
            if len(nums) >= 4:
                cx = (int(nums[0]) + int(nums[2])) // 2
                cy = (int(nums[1]) + int(nums[3])) // 2
                subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     "input", "tap", str(cx), str(cy)],
                    capture_output=True, timeout=5,
                )
                time.sleep(0.3)

        return acts

    def _bootstrap_navigation(self, package: str, main_activity: str) -> None:
        """One-time routine that fires BEFORE the main walk loop.

        Strategy (minimal, conservative — don't burn too many events):
          1. Capture initial state
          2. If BottomNavigationView exists — tap each tab once
          3. If DrawerLayout exists — open drawer via edge-swipe; the main loop
             will then see the drawer's items as newly actionable elements
          4. If Toolbar with action icons exists — tap the top-right icon (usually
             overflow / profile / settings entry)

        Does NOT record these as transitions yet; main loop will capture resulting
        states naturally.
        """
        state = self._capture_screen(-1)
        if not state:
            return
        views = state.get("views", [])

        # --- 2. Bottom navigation ---
        bottom_tabs = [v for v in views
                       if "bottomnav" in (v.get("class", "") + v.get("parent_class", "")).lower()
                       and v.get("clickable")]
        for tab in bottom_tabs[:5]:  # cap to 5
            logger.info("[bootstrap] tapping bottom-nav: %s",
                        tab.get("content_desc") or tab.get("text") or "?")
            self._tap_view(tab)
            time.sleep(0.8)
            self._go_home_tab(bottom_tabs)  # return to first tab after each probe
            time.sleep(0.5)

        # --- 3. Drawer ---
        has_drawer = any("drawerlayout" in (v.get("class", "") or "").lower()
                         for v in views)
        if has_drawer or self._has_drawer_toggle(views):
            self._open_drawer()
            time.sleep(0.8)
            # Main loop will discover drawer items as new actionable views

        # --- 4. Top-right toolbar icons (overflow / profile / settings) ---
        top_right = self._find_top_right_actions(views)
        for v in top_right[:2]:  # cap to 2 to avoid burning events
            desc = v.get("content_desc") or v.get("text") or ""
            logger.info("[bootstrap] tapping top-right icon: %s", desc)
            self._tap_view(v)
            time.sleep(0.8)
            # Back to main screen so the loop starts from a known baseline
            self._press_back()
            time.sleep(0.4)

    def _has_drawer_toggle(self, views: list[dict]) -> bool:
        """Look for hamburger / drawer toggle button."""
        for v in views:
            cls = (v.get("class") or "").lower()
            desc = (v.get("content_desc") or "").lower()
            rid = (v.get("resource_id") or "").lower()
            if not v.get("clickable"):
                continue
            if "open drawer" in desc or "navigation" in desc or "menu" in desc:
                return True
            if "drawer_toggle" in rid or "drawer_indicator" in rid or "menu_icon" in rid:
                return True
            # Material toolbar up-button
            if "actionbar$tab" in cls or "draweractionbardrawertoggle" in cls:
                return True
        return False

    def _open_drawer(self) -> None:
        """Open navigation drawer via left-edge swipe.  Safe to call even if no drawer."""
        # Swipe from just inside the left edge (x=20) to mid-screen, slower duration
        # so Android's edge detector accepts it.
        subprocess.run(
            ["adb", "-s", self.device_serial, "shell",
             "input", "swipe", "20", "720", "600", "720", "400"],
            capture_output=True, timeout=5,
        )

    def _go_home_tab(self, tabs: list[dict]) -> None:
        """Return to the first bottom-nav tab (usually Home)."""
        if tabs:
            self._tap_view(tabs[0])

    def _find_top_right_actions(self, views: list[dict]) -> list[dict]:
        """Top-right area Toolbar menu items — commonly search/settings/profile."""
        import re
        out = []
        for v in views:
            if not v.get("clickable"):
                continue
            cls = (v.get("class") or "").lower()
            if not any(k in cls for k in ("actionmenuitemview", "imagebutton", "imageview")):
                continue
            bounds = v.get("bounds", "")
            nums = re.findall(r"\d+", str(bounds))
            if len(nums) < 4:
                continue
            x1, y1, x2, y2 = (int(n) for n in nums[:4])
            # Top-right: y < 300, x2 > screen_width * 0.6
            if y1 < 300 and x2 > 700:
                out.append(v)
        # Sort right-to-left (furthest right first — often most important)
        out.sort(key=lambda v: -int(re.findall(r"\d+", str(v.get("bounds","")))[2]) if re.findall(r"\d+", str(v.get("bounds",""))) else 0)
        return out

    def _load_plan(self, package: str) -> None:
        """Load declared-but-unvisited activities from the static wireframe ScreenMap.

        Priority:
          1. statically_reachable=True (DEX transitions pointed to them)
          2. has intent_filters (externally invokable) — also extract deep link URIs
          3. rest of declared
        """
        import json as _json
        # wireframe ScreenMap lives one level up from dynamic/
        screenmap_path = self.output_dir.parent / "output" / "screen_map.json"
        if not screenmap_path.exists():
            return
        try:
            screenmap = _json.loads(screenmap_path.read_text(encoding="utf-8"))
        except Exception:
            return
        nodes = screenmap.get("screen_map", {}).get("graph", {}).get("nodes", [])

        reachable, with_filter, rest = [], [], []
        self.plan_deep_links: list[str] = []
        for n in nodes:
            act = n.get("activity", "")
            if not act or not act.startswith(package):
                continue
            # Extract deep link URIs from intent_filters.
            # androguard returns data as a list of separate dicts, each with one
            # of {scheme, host, pathPrefix, ...}.  We combine all within the same
            # filter to form candidate URIs (cartesian product of schemes × hosts).
            for f in n.get("intent_filters", []) or []:
                if not isinstance(f, dict):
                    continue
                if "android.intent.action.VIEW" not in (f.get("actions") or []):
                    continue
                data = f.get("data") or []
                schemes: list[str] = []
                hosts: list[str] = []
                path_prefixes: list[str] = []
                for d in data:
                    if not isinstance(d, dict):
                        continue
                    if d.get("scheme"): schemes.append(d["scheme"])
                    if d.get("host"): hosts.append(d["host"])
                    if d.get("pathPrefix"): path_prefixes.append(d["pathPrefix"])
                # Skip http/https generic ones (too broad, usually open in browser)
                app_schemes = [s for s in schemes if s not in ("http", "https")]
                if not app_schemes and schemes:
                    app_schemes = schemes  # fallback
                hosts_or_stub = hosts or ["app"]
                prefix = path_prefixes[0] if path_prefixes else ""
                for sch in app_schemes:
                    for host in hosts_or_stub:
                        uri = f"{sch}://{host}{prefix or ''}"
                        if uri not in self.plan_deep_links:
                            self.plan_deep_links.append(uri)
            if n.get("status") not in (None, "declared"):
                continue
            if n.get("statically_reachable"):
                reachable.append(act)
            elif n.get("intent_filters"):
                with_filter.append(act)
            else:
                rest.append(act)
        # Cap total to avoid burning events on dead code
        self.plan_targets = (reachable + with_filter + rest)[:30]
        # Cap deep links similarly
        self.plan_deep_links = self.plan_deep_links[:15]

    def _try_plan_launch(self, package: str) -> bool:
        """Explicitly launch the next unvisited activity OR deep link.

        Alternates between direct Activity launch and Deep link invocation to
        cover both internal screens and deep-link-only entry points.

        Returns True if we actually kicked off something.
        """
        # First: try a deep link (these reach screens that direct launch can't)
        if getattr(self, "plan_deep_links", None):
            uri = self.plan_deep_links.pop(0)
            logger.info("[plan] Deep-link launching: %s", uri)
            try:
                r = subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     "am", "start", "-a", "android.intent.action.VIEW",
                     "-d", uri, package],
                    capture_output=True, timeout=10,
                )
                out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", errors="replace")
                if "Error" not in out and r.returncode == 0:
                    self.trap_stats["deeplink_launched"] += 1
                    time.sleep(2.0)
                    return True
                logger.info("[plan]   deep link failed: %s", out.strip()[:80])
            except Exception as e:
                logger.info("[plan]   deep link exception: %s", e)

        # Then: direct Activity launch
        if not self.plan_targets:
            return False
        for target in list(self.plan_targets):
            if target in self.plan_visited or target in self.plan_failed:
                self.plan_targets.remove(target)
                continue
            logger.info("[plan] Direct-launching: %s", target)
            self.plan_targets.remove(target)
            try:
                r = subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     "am", "start", "-n", f"{package}/{target}"],
                    capture_output=True, timeout=10,
                )
                out = (r.stdout or b"").decode("utf-8", errors="replace") \
                    + (r.stderr or b"").decode("utf-8", errors="replace")
                if "Error" in out or r.returncode != 0:
                    logger.info("[plan]   launch failed: %s", out.strip()[:100])
                    self.plan_failed.add(target)
                    continue
                self.trap_stats["plan_launched"] += 1
                time.sleep(2.0)
                return True
            except Exception as e:
                logger.info("[plan]   launch exception: %s", e)
                self.plan_failed.add(target)
        return False




    def _save_results(self, package: str, elapsed: float, event_count: int) -> dict:
        """Save walk results in DroidBot-compatible format."""
        result = {
            "states": self.states,
            "transitions": self.transitions,
            "activities_found": list({s["activity"] for s in self.states if s.get("activity")}),
            "stats": {
                "total_events": event_count,
                "unique_screens": len(self.hasher.known_fingerprints),
                "unique_screens_raw": len(self.visited_screens),
                "elapsed_seconds": round(elapsed, 1),
                "package": package,
                "hashing": {
                    "l1_structural_matches": self.hash_stats["l1_matches"],
                    "l2_phash_matches": self.hash_stats["l2_matches"],
                    "l3_gnn_matches": self.hash_stats["l3_matches"],
                    "new_screens_discovered": self.hash_stats["new_screens"],
                    "coalesce_ratio": round(
                        1 - len(self.hasher.known_fingerprints) / max(len(self.states), 1), 2
                    ),
                },
            },
        }

        # Save walk.json (compatible with utg_parser output)
        out_path = self.output_dir / "walk.json"
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

        return result
