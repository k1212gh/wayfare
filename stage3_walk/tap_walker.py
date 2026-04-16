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
import time
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)


class TapWalker:
    """Step-by-step app walk with unseen-driven priority."""

    def __init__(self, device_serial: str, apk_path: str, output_dir: str,
                 timeout: int = 600, max_events: int = 500):
        self.device_serial = device_serial
        self.apk_path = apk_path
        self.output_dir = Path(output_dir)
        self.timeout = timeout
        self.max_events = max_events

        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "states").mkdir(exist_ok=True)

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

    def run(self) -> dict:
        """Run the smart walk loop."""
        import subprocess

        logger.info("Smart Walker: device=%s, timeout=%ds, max_events=%d",
                     self.device_serial, self.timeout, self.max_events)

        start_time = time.time()
        event_count = 0

        # Install and start app
        subprocess.run(["adb", "-s", self.device_serial, "install", "-r",
                        str(Path(self.apk_path).resolve())],
                       capture_output=True, timeout=60)

        # Get package and launch
        from androguard.core.apk import APK
        apk = APK(self.apk_path)
        package = apk.get_package()
        main_activity = apk.get_main_activity()
        if main_activity and not main_activity.startswith(package):
            if main_activity.startswith("."):
                main_activity = package + main_activity

        subprocess.run(["adb", "-s", self.device_serial, "shell",
                        "am", "start", "-n", f"{package}/{main_activity}"],
                       capture_output=True, timeout=10)
        time.sleep(2)

        while event_count < self.max_events and (time.time() - start_time) < self.timeout:
            # 1. Capture current state
            state = self._capture_screen(event_count)
            if not state:
                break

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

            # 2. Detect stall (same canonical screen 3+ times in a row)
            if canonical_id == self.last_canonical:
                self.stall_count += 1
            else:
                self.stall_count = 0
            self.last_canonical = canonical_id

            if self.stall_count >= 2:
                logger.info("Stall on %s (%d times), pressing back", canonical_id, self.stall_count)
                self._press_back()
                self.back_count += 1
                event_count += 1
                time.sleep(0.5)

                if self.back_count >= 3:
                    # Restart app + reset tried actions for fresh walk
                    logger.info("Restarting app + clearing tried_actions for fresh start")
                    subprocess.run(["adb", "-s", self.device_serial, "shell",
                                    "input keyevent KEYCODE_HOME"],
                                   capture_output=True, timeout=5)
                    time.sleep(1)
                    subprocess.run(["adb", "-s", self.device_serial, "shell",
                                    "am", "start", "-n", f"{package}/{main_activity}"],
                                   capture_output=True, timeout=10)
                    self.back_count = 0
                    self.stall_count = 0
                    # Clear tried actions so same screen gets fresh attempts
                    self.tried_actions.clear()
                    time.sleep(2)
                continue

            # 3. Get actionable elements and score them
            actions = self._get_scored_actions(state)

            if not actions:
                self._press_back()
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
                    self._press_back()
                    self.back_count += 1
                    event_count += 1
                    time.sleep(0.5)
                    continue

            # 4. Pick best action (highest unseen score)
            best = actions[0]
            logger.info("Event %d: %s on %s (score=%.2f, visits=%d)",
                        event_count, best["action"], best.get("desc", "?"),
                        best["score"], self.visited_structures[canonical_id])

            # 5. Execute action + record as tried
            prev_canonical = canonical_id
            self.tried_actions[canonical_id].add(best.get("desc", ""))
            self._execute_action(best, state)
            event_count += 1
            time.sleep(0.7)  # Faster walk

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

        return self._save_results(package, elapsed, event_count)

    def _get_scored_actions(self, state: dict) -> list[dict]:
        """Score all possible actions on current screen by unseen potential.

        Priority:
        1. Navigation elements (tabs, menus, settings) → likely leads to new screens
        2. Never-clicked elements on this page
        3. Buttons/links (click)
        4. Input fields (deprioritized — need context)
        5. Already-visited element types (penalized)
        """
        views = state.get("views", [])
        canonical = state.get("canonical_id", state.get("structure_str", ""))
        visit_count = self.visited_structures.get(canonical, 0)

        actions = []
        seen_texts = set()

        for view in views:
            if not view.get("clickable") and not view.get("scrollable"):
                continue
            if not view.get("visible", True):
                continue

            rid = view.get("resource_id", "")
            text = view.get("text", "")
            desc = view.get("content_desc", "")
            cls = view.get("class", "")
            bounds = view.get("bounds", {})

            # Skip duplicate text elements (e.g., list items with same label)
            label = text or desc or rid
            if label in seen_texts and not rid:
                continue
            seen_texts.add(label)

            action_desc = f"click {rid or text or desc or cls}"
            combined = (rid + text + desc + cls).lower()

            # Base score
            score = 1.0

            # === BONUS: never tried on this screen (biggest priority) ===
            if action_desc not in self.tried_actions.get(canonical, set()):
                score += 4.0

            # Bonus: navigation-like elements
            nav_keywords = ["tab", "menu", "nav", "drawer", "settings", "more",
                            "home", "profile", "search", "toolbar", "option",
                            "notification", "account", "calendar", "event",
                            "write", "create", "add", "new", "compose", "edit",
                            "back", "close", "cancel", "done", "save",
                            "detail", "info", "about", "help"]
            if any(k in combined for k in nav_keywords):
                score += 2.0

            # Bonus: buttons
            if "Button" in cls:
                score += 1.5
            elif "ImageView" in cls or "ImageButton" in cls:
                score += 1.0
            elif "Tab" in cls:
                score += 2.0

            # Bonus: elements with resource-id (more likely real buttons)
            if rid:
                score += 0.5

            # PENALTY: visited many times
            score -= visit_count * 1.5

            # PENALTY: already tried
            if action_desc in self.tried_actions.get(canonical, set()):
                score -= 8.0

            # Penalty: list items
            if "RecyclerView" in str(view.get("parent_class", "")):
                score -= 2.0

            # Penalty: scroll actions
            if view.get("scrollable") and not view.get("clickable"):
                score -= 1.0

            action_type = "click" if view.get("clickable") else "scroll"

            actions.append({
                "action": action_type,
                "view": view,
                "score": score,
                "desc": f"{action_type} {rid or text or desc or cls}",
                "bounds": bounds,
            })

        # Sort by score descending
        actions.sort(key=lambda a: a["score"], reverse=True)
        return actions

    def _capture_screen(self, idx: int) -> dict | None:
        """Dump UI hierarchy and screenshot from device."""
        import subprocess

        try:
            # UI dump (use shell quoting to avoid Git Bash path mangling)
            subprocess.run(["adb", "-s", self.device_serial, "shell",
                            "uiautomator dump //sdcard//sa_dump.xml"],
                           capture_output=True, timeout=10, shell=False)

            xml_path = self.output_dir / "states" / f"dump_{idx:04d}.xml"
            subprocess.run(["adb", "-s", self.device_serial, "pull",
                            "//sdcard//sa_dump.xml", str(xml_path)],
                           capture_output=True, timeout=10)

            # Screenshot
            screen_path = self.output_dir / "states" / f"screen_{idx:04d}.png"
            subprocess.run(["adb", "-s", self.device_serial, "shell",
                            "screencap -p //sdcard//sa_screen.png"],
                           capture_output=True, timeout=10)
            subprocess.run(["adb", "-s", self.device_serial, "pull",
                            "//sdcard//sa_screen.png", str(screen_path)],
                           capture_output=True, timeout=10)

            # Parse XML
            views = self._parse_ui_xml(xml_path)

            # Get current activity
            activity_result = subprocess.run(
                ["adb", "-s", self.device_serial, "shell",
                 "dumpsys activity activities"],
                capture_output=True, text=True, timeout=10,
            )
            activity = self._extract_activity(activity_result.stdout or "")

            # Compute structure hash
            clickable_ids = sorted(
                v.get("resource_id", "") for v in views if v.get("clickable")
            )
            structure_str = hashlib.sha256(
                f"{activity}|{'|'.join(clickable_ids)}".encode()
            ).hexdigest()

            state_str = hashlib.sha256(
                f"{activity}|{json.dumps([v.get('text','') for v in views[:20]])}".encode()
            ).hexdigest()

            state = {
                "state_str": state_str,
                "structure_str": structure_str,
                "activity": activity,
                "views": views,
                "screenshot_path": str(screen_path) if screen_path.exists() else "",
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
        elif action["action"] == "scroll":
            subprocess.run(["adb", "-s", self.device_serial, "shell",
                            "input", "swipe", str(x), str(y), str(x), str(y - 300), "300"],
                           capture_output=True, timeout=5)

    def _press_back(self) -> None:
        import subprocess
        subprocess.run(["adb", "-s", self.device_serial, "shell",
                        "input", "keyevent", "KEYCODE_BACK"],
                       capture_output=True, timeout=5)
        time.sleep(0.5)

    def _parse_ui_xml(self, xml_path: Path) -> list[dict]:
        """Parse uiautomator XML dump into view list."""
        if not xml_path.exists():
            return []

        try:
            from xml.etree import ElementTree as ET
            tree = ET.parse(str(xml_path))
        except Exception:
            return []

        views = []
        for elem in tree.iter("node"):
            attrs = elem.attrib
            bounds_str = attrs.get("bounds", "")

            views.append({
                "resource_id": attrs.get("resource-id", "").split("/")[-1] if "/" in attrs.get("resource-id", "") else attrs.get("resource-id", ""),
                "class": attrs.get("class", "").rsplit(".", 1)[-1] if "." in attrs.get("class", "") else attrs.get("class", ""),
                "text": attrs.get("text", ""),
                "content_desc": attrs.get("content-desc", ""),
                "clickable": attrs.get("clickable") == "true",
                "scrollable": attrs.get("scrollable") == "true",
                "enabled": attrs.get("enabled") == "true",
                "visible": True,
                "bounds": bounds_str,
            })

        return views

    def _extract_activity(self, dumpsys_output: str) -> str:
        """Extract current foreground activity from dumpsys."""
        import re
        # Try multiple patterns used by different Android versions
        patterns = ["ResumedActivity", "topResumedActivity", "mResumedActivity",
                     "mFocusedApp", "mFocusedActivity"]
        for line in dumpsys_output.split("\n"):
            if any(p in line for p in patterns):
                # Extract com.package/.ActivityName or com.package/com.package.Activity
                m = re.search(r'([a-zA-Z0-9_.]+/\.?[a-zA-Z0-9_.$]+)', line)
                if m:
                    full = m.group(1)
                    pkg, act = full.split("/", 1)
                    if act.startswith("."):
                        return pkg + act
                    if "." not in act:
                        return pkg + "." + act
                    return act
        return "unknown"

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
