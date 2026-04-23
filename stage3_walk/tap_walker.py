"""Smart Walker — priority-based walk that avoids redundant visits.

Instead of DroidBot's blind dfs_greedy, this controller:
1. Tracks visited states by structure_str (not just state_str)
2. Prioritizes clicks that lead to NEW screens (unseen structure)
3. Penalizes actions on already-walked pages
4. Auto-backs out of dead-end loops
5. Favors deeper navigation over lateral (list item) walk
"""

import hashlib
import json
import logging
import os
import re
import subprocess
import time
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)


class TapWalker:
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

        # 3-Level State Hasher
        from .screen_signer import ScreenSigner
        self.hasher = ScreenSigner(phash_threshold=10, gnn_threshold=0.95)

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
        import subprocess

        # Ensure directories
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
            from . import u2_helper
            raw_xml = self.output_dir / "raw" / f"capture_{idx:04d}.xml"
            xml_content = u2_helper.dump_hierarchy(self.device_serial, timeout=8.0)
            if xml_content and "<hierarchy" in xml_content:
                raw_xml.write_text(xml_content, encoding="utf-8")
                dump_success = True
            else:
                dump_success = False
                logger.info("UI dump returned empty — proceeding with screenshot only")

            # Screenshot
            raw_screen = self.output_dir / "raw" / f"capture_{idx:04d}.png"
            subprocess.run(["adb", "-s", self.device_serial, "shell",
                            "screencap -p //sdcard//sa_screen.png"],
                           capture_output=True, timeout=15)
            subprocess.run(["adb", "-s", self.device_serial, "pull",
                            "//sdcard//sa_screen.png", str(raw_screen)],
                           capture_output=True, timeout=15)

            # Parse XML
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

            # Detect dialog overlay in top-level views
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

            # Determine canonical screen ID (will be set by main loop later)
            # For now, use structure_str as screenshot filename
            screen_name = f"screen_{hashlib.sha256(structure_str.encode()).hexdigest()[:8]}"

            # Save screenshot to canonical location (only first time per screen)
            canonical_screen = self.output_dir / "screenshots" / f"{screen_name}.png"
            if not canonical_screen.exists() and raw_screen.exists():
                import shutil
                shutil.copy2(raw_screen, canonical_screen)
                # Also save XML
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

            # Save state JSON
            state_json = self.output_dir / "states" / f"state_{idx:04d}.json"
            state_json.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

            return state

        except Exception as e:
            logger.warning("Failed to capture state: %s", e)
            return None

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
        import re
        if isinstance(bounds, str):
            nums = re.findall(r"\d+", bounds)
            if len(nums) < 4:
                return False
            x1, y1, x2, y2 = int(nums[0]), int(nums[1]), int(nums[2]), int(nums[3])
            # Top 1/3 of screen, right 1/3
            return y1 < 800 and x2 > 700
        return False

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
    )

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

    def _press_back(self) -> bool:
        """Press Back only when it's safe (won't exit the app).

        Returns True if Back was executed, False if skipped (caller may try
        another strategy — e.g., restart instead of Back).
        """
        # Never press Back on our main activity — it always kicks to launcher
        current = self._current_activity()
        if current and getattr(self, "main_activity", None) and current == self.main_activity:
            logger.info("Skip Back: on main activity (%s) — would exit app", current)
            return False

        # Only press Back if the activity stack for our package has more than 1 entry.
        # (If stack depth == 1 on a non-main activity, Back would still exit to launcher.)
        depth = self._task_stack_depth(getattr(self, "package", ""))
        if depth is not None and depth <= 1:
            logger.info("Skip Back: task stack depth=%d (Back would exit)", depth)
            return False

        subprocess.run(["adb", "-s", self.device_serial, "shell",
                        "input", "keyevent", "KEYCODE_BACK"],
                       capture_output=True, timeout=5)
        time.sleep(0.5)
        return True

    def _current_activity(self) -> str:
        """Lightweight foreground-activity probe (dumpsys)."""
        try:
            r = subprocess.run(
                ["adb", "-s", self.device_serial, "shell",
                 "dumpsys", "activity", "activities"],
                capture_output=True, timeout=5,
            )
            return self._extract_activity(
                (r.stdout or b"").decode("utf-8", errors="replace")
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
            # `ActivityRecord{... u0 <package>/...}` appears once per entry in the stack
            import re as _re
            pattern = _re.compile(r"ActivityRecord\{[^}]*\s" + _re.escape(package) + r"/", _re.IGNORECASE)
            matches = pattern.findall(out)
            return len(matches) if matches else None
        except Exception:
            return None

    def _parse_ui_xml(self, xml_path: Path) -> list[dict]:
        """Parse uiautomator XML dump into view list (with parent_class)."""
        if not xml_path.exists():
            return []

        try:
            from xml.etree import ElementTree as ET
            tree = ET.parse(str(xml_path))
        except Exception:
            return []

        views: list[dict] = []

        def walk(elem, parent_class_full: str):
            if elem.tag == "node":
                attrs = elem.attrib
                full_cls = attrs.get("class", "")
                short_cls = full_cls.rsplit(".", 1)[-1] if "." in full_cls else full_cls
                rid_raw = attrs.get("resource-id", "")
                views.append({
                    "resource_id": rid_raw.split("/")[-1] if "/" in rid_raw else rid_raw,
                    "class": short_cls,
                    "parent_class": parent_class_full,
                    "text": attrs.get("text", ""),
                    "content_desc": attrs.get("content-desc", ""),
                    "clickable": attrs.get("clickable") == "true",
                    "long_clickable": attrs.get("long-clickable") == "true",
                    "scrollable": attrs.get("scrollable") == "true",
                    "enabled": attrs.get("enabled") == "true",
                    "visible": True,
                    "bounds": attrs.get("bounds", ""),
                })
                next_parent = full_cls
            else:
                next_parent = parent_class_full
            for child in elem:
                walk(child, next_parent)

        walk(tree.getroot(), "")
        return views

    def _extract_fragment(self, dumpsys_output: str) -> str:
        """Extract the currently active Fragment from `dumpsys activity top`.

        Returns short fragment class name (e.g. 'HomeFragment') or '' if none.
        Uses the last 'Added Fragments:' section (most recently added) or
        the first '  #0: {...Fragment}' pattern.
        """
        import re
        # Modern form: "Added Fragments:\n    #0: HomeFragment{...}"
        m = re.search(r"Added Fragments:\s*\n\s*#\d+:\s*([A-Za-z0-9_$]+?Fragment)\{",
                      dumpsys_output)
        if m:
            return m.group(1)
        # Alt form: just look for Fragment{classname=...}
        m = re.search(r"Fragment\{[^}]*\sclass\s*=\s*([A-Za-z0-9_.$]+)", dumpsys_output)
        if m:
            cls = m.group(1)
            return cls.rsplit(".", 1)[-1]
        # Loose fallback: any "{SomethingFragment}"
        m = re.search(r"\b([A-Za-z0-9_$]+Fragment)\b", dumpsys_output)
        return m.group(1) if m else ""

    def _detect_dialog(self, views: list[dict]) -> bool:
        """Detect if an overlay Dialog/BottomSheet is on top of the activity.

        Signals:
          - Top-level class contains 'Dialog' or 'BottomSheet' or 'Popup'
          - resource-id contains 'dialog'/'alert'
          - Very small/centered bounds (typical of modal)
        """
        for v in views[:20]:  # check top-level only
            cls = (v.get("class") or "").lower()
            rid = (v.get("resource_id") or "").lower()
            if any(kw in cls for kw in ("dialog", "bottomsheet", "popup", "alertdialog")):
                return True
            if any(kw in rid for kw in ("dialog", "alert", "popup")):
                return True
        return False

    def _detect_popup_menu(self, views: list[dict]) -> bool:
        """Is the overlay a user-intent popup menu (not a blocking dialog)?

        Popup menu / dropdown / context menu classes — these contain tappable
        list items that represent app functionality (Settings, Share, Delete)
        rather than Allow/Deny prompts. We should WALK these, not dismiss.
        """
        popup_class_kw = (
            "popupmenu", "listpopupwindow", "dropdownlistview",
            "menupopupwindow", "menuitem", "cascadingmenupopup",
            "overflowmenubutton",
        )
        alert_class_kw = ("alertdialog", "messagedialog", "confirmdialog")
        has_popup_marker = False
        has_alert_marker = False
        for v in views[:30]:
            cls = (v.get("class") or "").lower()
            if any(kw in cls for kw in popup_class_kw):
                has_popup_marker = True
            if any(kw in cls for kw in alert_class_kw):
                has_alert_marker = True
        # Popup-menu class wins over alert if both present (rare).
        return has_popup_marker and not has_alert_marker

    def _popup_items(self, views: list[dict]) -> list[dict]:
        """Return clickable views inside an active popup menu.

        Popup menu items typically live under `PopupWindow$PopupDecorView` or
        have resource_id like 'android:id/title'. We match clickable descendants
        of a popup container.
        """
        items: list[dict] = []
        for v in views:
            cls = (v.get("class") or "").lower()
            parent = (v.get("parent_class") or "").lower()
            # Item classes or parents suggesting menu container
            in_popup = (
                "popupmenu" in parent or "popupmenu" in cls
                or "listpopupwindow" in parent or "dropdownlist" in parent
                or "menupopupwindow" in parent
            )
            if in_popup and v.get("clickable"):
                items.append(v)
            # Also menu items identified by text + being in a list-like parent
            if not in_popup and v.get("clickable") and v.get("text"):
                rid = (v.get("resource_id") or "").lower()
                if "title" in rid or "menu" in rid:
                    items.append(v)
        return items

    def _extract_activity(self, dumpsys_output: str) -> str:
        """Extract current foreground activity from dumpsys output.

        Supports multiple dumpsys formats:
          - `mResumedActivity = ActivityRecord{... com.pkg/.Name ...}` (classic)
          - `topResumedActivity = ...`, `mFocusedApp = ...`
          - `ACTIVITY com.pkg/.Name <hash> pid=...` (dumpsys activity top, newer AOSP)
          - Target app's package prefix filtering, to ignore system chrome/settings.
        """
        import re
        act_re = re.compile(r'([a-zA-Z][a-zA-Z0-9_.]*)/(\.?[a-zA-Z0-9_.$]+)')
        target_pkg = getattr(self, 'package', '') or ''

        # 1. Classic keywords
        patterns = ("ResumedActivity", "topResumedActivity", "mResumedActivity",
                    "mFocusedApp", "mFocusedActivity")
        for line in dumpsys_output.split("\n"):
            if any(p in line for p in patterns):
                m = act_re.search(line)
                if m:
                    pkg, act = m.group(1), m.group(2)
                    if act.startswith("."):
                        return pkg + act
                    if "." not in act:
                        return pkg + "." + act
                    return act

        # 2. Newer `dumpsys activity top` format — each TASK section begins with
        #    `ACTIVITY com.pkg/.Name <hash> pid=... userId=...`
        #    Prefer lines matching our target package; fall back to the first
        #    ACTIVITY line.
        fallback = ""
        for line in dumpsys_output.split("\n"):
            stripped = line.strip()
            if not stripped.startswith("ACTIVITY "):
                continue
            m = act_re.search(stripped)
            if not m:
                continue
            pkg, act = m.group(1), m.group(2)
            full_act = pkg + act if act.startswith(".") else (
                pkg + "." + act if "." not in act else act
            )
            # Prefer target package; remember first as fallback
            if target_pkg and pkg == target_pkg:
                return full_act
            if not fallback:
                fallback = full_act
        return fallback or "unknown"

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

    # ───── Pause / user-input support ──────────────────────────
    _LOGIN_KEYWORDS = (
        "login", "log_in", "signin", "sign_in", "logon",
        "password", "passwd", "pwd",
        "email", "username", "phone",
        "otp", "verify", "verification", "2fa",
        "auth", "oauth",
    )

    def _detect_user_input_needed(self, state: dict) -> bool:
        """Heuristic: does the current screen require manual login / auth?"""
        if self._paused_file.exists():
            return False  # already paused, don't re-trigger

        activity = (state.get("activity") or "").lower()
        # Strong signal from activity class name
        if any(kw in activity for kw in ("login", "signin", "sign_in", "oauth", "auth")):
            return True

        views = state.get("views", [])
        has_password = False
        hit_kw: set[str] = set()
        for v in views:
            cls = (v.get("class") or "").lower()
            rid = (v.get("resource_id") or "").lower()
            desc = (v.get("content_desc") or "").lower()
            text = (v.get("text") or "").lower()
            combined = f"{rid} {desc} {text}"
            # Password-type EditText is near-certain login
            if "edittext" in cls and ("password" in rid or "password" in desc or "pwd" in rid):
                has_password = True
            for kw in self._LOGIN_KEYWORDS:
                if kw in combined:
                    hit_kw.add(kw)

        if has_password:
            return True
        # Need at least 2 distinct keywords to reduce false positives
        return len(hit_kw) >= 2

    def _request_user_input(self, state: dict) -> None:
        """Write paused.json so dashboard shows a banner + Resume button."""
        import json as _json
        reason = "login/auth screen detected"
        activity = state.get("activity", "")
        if activity and any(kw in activity.lower() for kw in ("login", "signin", "auth")):
            reason = f"login screen: {activity.rsplit('.', 1)[-1]}"
        payload = {
            "reason": reason,
            "auto": True,
            "activity": activity,
            "since": time.time(),
        }
        try:
            self._paused_file.write_text(
                _json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("PAUSED — waiting for user: %s", reason)
        except Exception as e:
            logger.warning("Failed to write paused.json: %s", e)

    def _wait_for_resume(self) -> bool:
        """Block until paused.json is removed, cancel is set, or timeout.

        Returns True if we should continue, False if walk should abort.
        """
        pause_start = time.time()
        logged = False
        while self._paused_file.exists():
            if self._cancel_flag.exists():
                logger.info("Cancel during pause — stopping")
                return False
            if (time.time() - pause_start) > self._pause_max_seconds:
                logger.warning("Pause timed out after %ds — cancelling", self._pause_max_seconds)
                try:
                    self._paused_file.unlink(missing_ok=True)
                except Exception:
                    pass
                return False
            if not logged:
                logger.info("Waiting for user to Resume (paused.json present)")
                logged = True
            time.sleep(2)
        logger.info("Resumed after %.1fs", time.time() - pause_start)
        return True

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
        import json as _json
        screenmap_path = self.output_dir.parent / "output" / "screen_map.json"
        if not screenmap_path.exists():
            return {}
        try:
            screenmap = _json.loads(screenmap_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        nodes = screenmap.get("screen_map", {}).get("graph", {}).get("nodes", [])

        # Activities already UI-captured (by manifest_scan or interactive loop)
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
                        # Deep link resolved but routed to a different Activity
                        # (login redirect, home fallback). Skip.
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
            out_path.write_text(_json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        logger.info("[deep-scan] complete: %d captured via deep links", captured_count)
        return results

    def _build_deep_link_uris(self, filters: list) -> list[str]:
        """Build candidate URIs from manifest intent_filter data specs."""
        if not filters or not isinstance(filters, list):
            return []
        uris: list[str] = []
        for f in filters:
            if not isinstance(f, dict):
                continue
            if "android.intent.action.VIEW" not in (f.get("actions") or []):
                continue
            data = f.get("data") or []
            schemes: list[str] = []
            hosts: list[str] = []
            paths: list[str] = []
            for d in data:
                if not isinstance(d, dict):
                    continue
                if d.get("scheme"): schemes.append(d["scheme"])
                if d.get("host"): hosts.append(d["host"])
                if d.get("path"): paths.append(d["path"])
                elif d.get("pathPrefix"): paths.append(d["pathPrefix"])
                elif d.get("pathPattern"):
                    # Replace wildcards with a deterministic stub so the Activity
                    # at least receives a well-formed URI.
                    pp = d["pathPattern"].replace(".*", "stub").replace("........", "00000000") \
                                         .replace("....", "0000").replace("..", "00")
                    paths.append(pp)
            # Prefer custom schemes (spotify://) over https://
            custom = [s for s in schemes if s not in ("http", "https")]
            use_schemes = custom or schemes
            if not use_schemes:
                continue
            for sch in use_schemes[:2]:
                for host in (hosts or [""])[:2]:
                    path = paths[0] if paths else ""
                    uri = f"{sch}://{host}{path}"
                    if uri not in uris:
                        uris.append(uri)
        return uris

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
        import hashlib
        import shutil
        from . import u2_helper

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
        # The node metadata is tagged with `self_finishing: True` so the dashboard
        # and downstream agents can distinguish "reached briefly then bounced"
        # from "fully resolved" screens.
        fg_first: str | None = None
        fg_last: str | None = None

        # Cumulative delays. 0.2s handles the fastest gated flows,
        # 1.7s handles splash/animated onboarding that settles slowly.
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

            # Parse views from this attempt via temp file (avoid polluting main path)
            tmp_xml = xml_path.with_name(f"scan_{idx:04d}.t{step}.xml")
            tmp_xml.write_text(xml, encoding="utf-8")
            views = self._parse_ui_xml(tmp_xml)

            # Capture matching screenshot at this same moment
            tmp_png = png_path.with_name(f"scan_{idx:04d}.t{step}.png")
            try:
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

            # Early exit: clearly-good capture — no need to keep polling
            if fg_ok and len(views) >= 5:
                logger.debug("[scan_capture] %s early-exit at step=%d (fg_ok, %d views)",
                             target_short, step, len(views))
                break

        # Promote best attempt to canonical path, clean up temps
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

        # Self-finishing classification — target was foreground initially but
        # not at the end, meaning onCreate() triggered a redirect/finish().
        # Downstream consumers (dashboard, MobileGPT agent) use this to distinguish
        # "briefly reachable, needs specific Intent extras" from "fully visible".
        self_finishing = (
            fg_first == target_short and
            fg_last is not None and
            fg_last != target_short
        )

        # Keep if we have EITHER views OR screenshot. A screenshot alone
        # is still valuable — LLM vision can reason about the screen even
        # without a parsed element tree.
        if not views and not screenshot_path:
            # Edge case: even if both are empty, record self_finishing signal
            # on the caller side via a minimal stub. But simpler is to return
            # None here and let scan wrapper log the skip.
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
            result["redirect_to"] = fg_last  # what activity took over
        return result

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
        # topResumedActivity=ActivityRecord{... u0 pkg/.ClassName ...}
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

        # Still outside — force relaunch via monkey (doesn't need main activity FQN)
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

    def _manifest_scan(self, package: str, per_launch_timeout: int = 8) -> dict:
        """End-of-walk scan: force-launch every still-declared activity.

        Uses `am start -W` which runs as shell uid (uid 2000) so non-exported
        activities can still be started. For each activity we record whether
        the launch succeeded and what foreground component it produced, so
        Stage 6 can flip node status from `declared` → `probed`.
        """
        import json as _json
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
            screenmap = _json.loads(screenmap_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("[scan] failed to read wireframe: %s", e)
            return {}
        nodes = screenmap.get("screen_map", {}).get("graph", {}).get("nodes", [])
        # Only scan still-declared activities (not entry/resolved/unknown that
        # natural walk already covered).
        # NOTE: don't filter by activity.startswith(package) — many apps
        # (Spotify, TikTok, Facebook family) use sub-package FQNs like
        # com.spotify.jam.*, com.meta.auth.* for activities declared in a
        # single parent APK. We rely on `status == 'declared'` instead.
        #
        # NEW: use `capture_priority` set by activity_classifier:
        #   A = user-facing screen → scan aggressively
        #   B = plumbing / trampoline → SKIP (saves time, avoids junk captures)
        #   C = deep-link only → skip scan (intent_filter suffices for agent)
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
            prio = n.get("capture_priority", "A")  # default to A if not classified
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
        # foreground and poison our captures. If a Spotify activity launches
        # but immediately finish()'es, Android returns focus to whatever was
        # previously foreground — frequently Chrome (from earlier deep-link
        # fallback) or Settings. Those dialogs then get captured as if they
        # were the Spotify screen. Clearing them makes the fallback go to
        # the launcher / Spotify MainActivity instead.
        for poison in ("org.chromium.chrome", "com.android.chrome",
                       "com.google.android.apps.chrome", "com.android.settings",
                       "com.google.android.googlequicksearchbox"):
            subprocess.run(
                ["adb", "-s", self.device_serial, "shell",
                 "am", "force-stop", poison],
                capture_output=True, timeout=5,
            )

        # Parent task warm-up — many Activities (Settings, Preferences, nested
        # detail screens) require a valid parent task to render. Launch
        # MainActivity first so each subsequent probe finds a proper task
        # stack instead of falling back to launcher.
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
        scan_budget_s = 420  # 7 min hard cap — prevents 85+ activity apps
                              # from starving stage 4/6 if each probe hits the
                              # worst-case ~35s per-activity timeout.
        scan_start = time.time()
        for i, act in enumerate(declared):
            if self._cancel_flag.exists():
                logger.info("[scan] cancel flag set, stopping at %d/%d", i, len(declared))
                break
            if time.time() - scan_start > scan_budget_s:
                logger.info("[scan] budget exceeded (%ds) — stopping at %d/%d",
                            scan_budget_s, i, len(declared))
                break
            try:
                # Note: `-S` flag (force-stop before launch) was tried but made
                # things worse — child activities in stopped-app context fall back
                # to launcher (NexusLauncher). Current non-S approach: redirected
                # activities remain as stub `probed`, runtime JIT captures them
                # when agent navigates naturally.
                r = subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     "am", "start", "-W", "-n", f"{package}/{act}"],
                    capture_output=True, timeout=per_launch_timeout,
                )
                out = (r.stdout or b"").decode("utf-8", errors="replace") + \
                      (r.stderr or b"").decode("utf-8", errors="replace")
                # Hard-failure signals (Activity truly unreachable / permission denied).
                # We don't use rc != 0 alone because am start -W returns non-zero when the
                # target Activity throws during onCreate (missing params, null session,
                # etc.) — but that still proves the Activity exists and is resolvable,
                # which is the "reachable" semantic we want for `probed` status.
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
                # Tag soft failures so we can distinguish "came up" vs "intent accepted
                # but activity aborted" when showing in the UI.
                soft_fail = r.returncode != 0
                # Wait for settle, then VERIFY the real foreground. am start -W
                # returns the launched activity name even when it finished and
                # Android returned to a different foreground. Without this
                # check we'd capture the OLD foreground (e.g. Chrome Page Info
                # dialog, Settings) and falsely label it as the Spotify target.
                time.sleep(1.5)  # give Compose/async renderers time to settle
                real_fg = self._current_activity() or ""
                # If the real foreground isn't the target activity, the activity
                # didn't stick. Skip capture — we don't want to pollute the ScreenMap
                # with screenshots of chrome/settings/etc tagged as the target.
                act_matches = (
                    real_fg == act
                    or real_fg.endswith("." + act.rsplit(".", 1)[-1])
                )
                captured = None
                if act_matches:
                    captured = self._scan_capture(act, len(self.states))
                    if captured:
                        self.states.append(captured)
                results[act] = {
                    "launched": True,
                    "foreground": fg,
                    "real_foreground": real_fg,
                    "soft_fail": soft_fail,
                    "captured": bool(captured),
                    "focus_mismatch": not act_matches,
                }
                launched += 1
                # Brief pause so we don't pile up ActivityManager queue
                time.sleep(0.15)
            except subprocess.TimeoutExpired:
                results[act] = {"launched": False, "reason": "timeout"}
            except Exception as e:
                results[act] = {"launched": False, "reason": str(e)[:120]}
            if (i + 1) % 10 == 0:
                logger.info("[scan] %d/%d probed, %d launched", i + 1, len(declared), launched)

        # 2nd pass — retry activities that launched but failed UI capture.
        # Some Activities only render after 1-2s of async init (data fetch,
        # Compose recomposition). Re-launching with 2.5s settle gives that
        # chance. This recaptures ~10-20% of initially-missed screens.
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
                    if not (real_fg == act or
                            real_fg.endswith("." + act.rsplit(".", 1)[-1])):
                        continue  # still not in the target activity
                    captured = self._scan_capture(act, len(self.states))
                    if captured:
                        self.states.append(captured)
                        results[act]["captured"] = True
                        results[act]["retry_ok"] = True
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
            out_path.write_text(_json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
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
