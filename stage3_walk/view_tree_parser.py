"""Pure-function UI parsing helpers.

Extracted from tap_walker.py (refactor Step 3). Everything here takes
its inputs as arguments and returns values — no `self`, no side effects.
Callers inside TapWalker delegate here via 1-line wrappers so tests
that still `self._parse_ui_xml(...)` keep working.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree as ET


# ─── UI hierarchy XML → view list ──────────────────────────────────

def parse_ui_xml(xml_path: Path) -> list[dict]:
    """Parse uiautomator XML dump into a flat list of view dicts.

    Each entry carries `class` (short name), `parent_class` (fully-qualified
    name of the nearest enclosing <node>), and the usual attrs (resource_id,
    text, content_desc, clickable, bounds, …).
    """
    if not xml_path.exists():
        return []
    try:
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


# ─── `dumpsys activity top` parsing ───────────────────────────────

def extract_activity(dumpsys_output: str, target_pkg: str = "") -> str:
    """Extract current foreground activity FQN from a dumpsys dump.

    Supports multiple dumpsys formats:
      - `mResumedActivity = ActivityRecord{... com.pkg/.Name ...}` (classic)
      - `topResumedActivity = ...`, `mFocusedApp = ...`
      - `ACTIVITY com.pkg/.Name <hash> pid=...` (dumpsys activity top, newer AOSP)

    If `target_pkg` is provided we prefer lines matching it; otherwise the
    first parseable line wins.
    """
    act_re = re.compile(r'([a-zA-Z][a-zA-Z0-9_.]*)/(\.?[a-zA-Z0-9_.$]+)')

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

    # 2. Newer `dumpsys activity top` format
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
        if target_pkg and pkg == target_pkg:
            return full_act
        if not fallback:
            fallback = full_act
    return fallback or "unknown"


# Android framework Fragment classes that are NOT the user-facing tab/panel
# we care about — they leak into dumpsys and must not be returned by
# extract_fragment(). Most are internal WindowManager / ActivityRecord bookkeeping.
_SYSTEM_FRAGMENT_CLASSES = frozenset({
    "TaskFragment",                    # WM internal (leaked via taskFragmentBounds)
    "WindowContainerTask",
    "DialogFragment",                  # base class; the real subclass name is more informative
    "NavHostFragment",                 # nav-compose wrapper
    "PreferenceFragment",
    "PreferenceFragmentCompat",        # Settings framework base; subclass is the real screen
    "BottomSheetDialogFragment",       # base for ad-hoc sheets
    "ListFragment",                    # generic AOSP base
    "SupportMapFragment",              # maps wrapper
})


def extract_fragment(dumpsys_output: str) -> str:
    """Return a short identifier for the currently-foreground Fragment, or ''.

    Handles three dumpsys output formats, in order of preference:

    1. **Active Fragments block** (modern AOSP, ViewPager/tab-based apps).
       Each entry has a ``tag=TAG`` + ``mState=N`` where N=7 means RESUMED
       (user-visible). Returns the tag of the RESUMED fragment — tags
       survive R8 obfuscation where class names (``ays``, ``bdp``, ``bsc``)
       don't. DeskClock is a prime example: three fragments share the same
       class hash but tag=CLOCKS / BEDTIME / STOPWATCH identifies which tab.

    2. **Added Fragments block** (older AOSP / single-fragment apps).
       ``#0: HomeFragment{...}`` — return the class.

    3. **Classic Fragment{class=...}** — return the class, filtered by
       ``_SYSTEM_FRAGMENT_CLASSES`` so framework internals don't leak.

    4. **Last-resort scan** for ``*Fragment`` token — also filtered.
    """
    # 1. Active Fragments — new format used by FragmentActivity (most modern apps).
    # Don't use a block regex: each fragment entry contains a "Back Stack Index"
    # line inside its Child FragmentManager section, so naive block boundary
    # detection cuts off at the first fragment. Instead, anchor on "Active
    # Fragments:" occurring SOMEWHERE before the match, and match each entry
    # independently. The per-entry pattern stops gobbling `[\s\S]*?` as soon
    # as it sees the NEXT fragment entry (line starting with 4 spaces + an
    # unindented class name followed by `{`) — guarding against cross-entry
    # mState pickup.
    if "Active Fragments:" in dumpsys_output:
        # Slice everything from "Active Fragments:" to the end, then stop at
        # ViewRoot or Local Activity (the next Activity's section).
        start = dumpsys_output.index("Active Fragments:")
        slice_ = dumpsys_output[start:]
        # Find an explicit end-of-section marker (indent drops back to 4 spaces
        # or fewer and a new "ViewRoot:" / "Local Activity" begins).
        end_m = re.search(r"\n    (?:ViewRoot|Local Activity|Local FragmentActivity)",
                          slice_[20:])
        if end_m:
            slice_ = slice_[: 20 + end_m.start()]

        entries: list[tuple[str, str, int]] = []
        # Each fragment entry: a line like "    <class>{<hash>} (<uuid> ... tag=<TAG>)"
        # followed (within that entry) by "mState=<N>". The lazy ``[\s\S]*?``
        # grabs text up to the first mState, which is the CORRECT one for
        # this entry (mState comes very early in each entry, before any
        # Child FragmentManager noise).
        for m in re.finditer(
            r"(?m)^    (\S+?)\{[0-9a-f]+\}\s*\([^)]*?tag=([A-Za-z0-9_\-]+)\)"
            r"[\s\S]*?mState=(\d+)",
            slice_,
        ):
            cls, tag, screen_s = m.groups()
            entries.append((cls, tag, int(screen_s)))

        if entries:
            # Prefer RESUMED (state=7) or STARTED-visible (state=5 with
            # visible hint). DeskClock in ViewPager lands on state=5 for
            # the visible tab and state=4 for the off-screen preloaded ones.
            for cls, tag, state in entries:
                if state == 7:
                    return tag if tag and tag.lower() not in ("tag", "null", "0") else cls
            # Fallback: highest-state fragment wins (state=5 > 4 > 3 > 1).
            entries.sort(key=lambda e: -e[2])
            cls, tag, _ = entries[0]
            return tag if tag and tag.lower() not in ("tag", "null", "0") else cls

    # 2. Added Fragments — older format, single fragment
    m = re.search(
        r"Added Fragments:\s*\n\s*#\d+:\s*([A-Za-z0-9_$]+?Fragment)\{",
        dumpsys_output,
    )
    if m and m.group(1) not in _SYSTEM_FRAGMENT_CLASSES:
        return m.group(1)

    # 3. Classic "Fragment{class=...}"
    m = re.search(
        r"Fragment\{[^}]*\sclass\s*=\s*([A-Za-z0-9_.$]+)",
        dumpsys_output,
    )
    if m:
        cls = m.group(1).rsplit(".", 1)[-1]
        if cls not in _SYSTEM_FRAGMENT_CLASSES:
            return cls

    # 4. Last resort: any *Fragment token — filtered against system classes
    for m in re.finditer(r"\b([A-Za-z0-9_$]+Fragment)\b", dumpsys_output):
        name = m.group(1)
        if name not in _SYSTEM_FRAGMENT_CLASSES:
            return name
    return ""


# ─── Dialog / popup menu detection ────────────────────────────────

def detect_dialog(views: list[dict]) -> bool:
    """True if an overlay Dialog / BottomSheet / AlertDialog is present.

    Only the first 20 top-level nodes are scanned — dialogs are always at
    the top of the hierarchy, and full traversal is wasteful on large trees.
    """
    for v in views[:20]:
        cls = (v.get("class") or "").lower()
        rid = (v.get("resource_id") or "").lower()
        if any(kw in cls for kw in ("dialog", "bottomsheet", "popup", "alertdialog")):
            return True
        if any(kw in rid for kw in ("dialog", "alert", "popup")):
            return True
    return False


def detect_popup_menu(views: list[dict]) -> bool:
    """Is the overlay a user-intent popup menu (not a blocking dialog)?

    Popup menus contain tappable list items that represent app functionality
    (Settings, Share, Delete) rather than Allow/Deny prompts — these should
    be walked, not dismissed.
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
    return has_popup_marker and not has_alert_marker


def popup_items(views: list[dict]) -> list[dict]:
    """Return clickable views that are inside an active popup menu.

    Matches direct PopupMenu children and clickable views whose resource_id
    suggests a menu item (title/menu).
    """
    items: list[dict] = []
    for v in views:
        cls = (v.get("class") or "").lower()
        parent = (v.get("parent_class") or "").lower()
        in_popup = (
            "popupmenu" in parent or "popupmenu" in cls
            or "listpopupwindow" in parent or "dropdownlist" in parent
            or "menupopupwindow" in parent
        )
        if in_popup and v.get("clickable"):
            items.append(v)
        if not in_popup and v.get("clickable") and v.get("text"):
            rid = (v.get("resource_id") or "").lower()
            if "title" in rid or "menu" in rid:
                items.append(v)
    return items


def is_top_right(bounds) -> bool:
    """True if `bounds` (uiautomator "[x1,y1][x2,y2]" string) falls in the
    top-right third of the screen — common position for close (X) buttons."""
    if isinstance(bounds, str):
        nums = re.findall(r"\d+", bounds)
        if len(nums) < 4:
            return False
        x1, y1, x2, y2 = int(nums[0]), int(nums[1]), int(nums[2]), int(nums[3])
        return y1 < 800 and x2 > 700
    return False


# ─── Deep-link URI builder ────────────────────────────────────────

def build_deep_link_uris(filters: list) -> list[str]:
    """Build candidate `scheme://host/path` URIs from manifest intent_filter
    entries. Used by the deep-link scan to reach gated VIEW-intent activities.

    Prefers custom schemes (e.g. `spotify://`) over `http`/`https`. For each
    filter we emit at most 2 schemes × 2 hosts × 1 path.
    """
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
            if d.get("scheme"):
                schemes.append(d["scheme"])
            if d.get("host"):
                hosts.append(d["host"])
            if d.get("path"):
                paths.append(d["path"])
            elif d.get("pathPrefix"):
                paths.append(d["pathPrefix"])
            elif d.get("pathPattern"):
                # Replace wildcards with a deterministic stub so the Activity
                # at least receives a well-formed URI.
                pp = (d["pathPattern"]
                      .replace(".*", "stub")
                      .replace("........", "00000000")
                      .replace("....", "0000")
                      .replace("..", "00"))
                paths.append(pp)
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
