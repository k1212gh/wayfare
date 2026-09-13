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

    Each entry carries:
      - ``class`` (short name) and ``parent_class`` (FQN of nearest enclosing <node>)
      - ``parent_index`` (index into this list, -1 for root) and ``sibling_index``
        (0-based position among siblings under the same parent) — needed for
        sibling-group detection (radio/checkbox/stepper) and for collapsing
        list-container children when computing structural hashes
      - ``depth`` (0 for root, +1 per nesting level)
      - The usual attrs (resource_id, text, content_desc, clickable, bounds, …)
    """
    if not xml_path.exists():
        return []
    try:
        tree = ET.parse(str(xml_path))
    except Exception:
        return []

    views: list[dict] = []

    def walk(elem, parent_class_full: str, parent_idx: int, depth: int):
        if elem.tag == "node":
            attrs = elem.attrib
            full_cls = attrs.get("class", "")
            short_cls = full_cls.rsplit(".", 1)[-1] if "." in full_cls else full_cls
            rid_raw = attrs.get("resource-id", "")
            my_idx = len(views)
            views.append({
                "resource_id": rid_raw.split("/")[-1] if "/" in rid_raw else rid_raw,
                "package": attrs.get("package", ""),
                "class": short_cls,
                "parent_class": parent_class_full,
                "parent_index": parent_idx,
                "sibling_index": 0,  # filled in the post-pass below
                "depth": depth,
                "text": attrs.get("text", ""),
                "content_desc": attrs.get("content-desc", ""),
                "clickable": attrs.get("clickable") == "true",
                "long_clickable": attrs.get("long-clickable") == "true",
                "scrollable": attrs.get("scrollable") == "true",
                "enabled": attrs.get("enabled") == "true",
                "visible": True,
                "bounds": attrs.get("bounds", ""),
            })
            next_parent_class = full_cls
            next_parent_idx = my_idx
            next_depth = depth + 1
        else:
            next_parent_class = parent_class_full
            next_parent_idx = parent_idx
            next_depth = depth
        for child in elem:
            walk(child, next_parent_class, next_parent_idx, next_depth)

    walk(tree.getroot(), "", -1, 0)

    # Post-pass: assign sibling_index by parent. DFS pre-order means children
    # of the same parent are visited in document order; we just enumerate them
    # in that order. Done as a separate pass to keep walk() recursion-light.
    children_by_parent: dict[int, list[int]] = {}
    for i, v in enumerate(views):
        children_by_parent.setdefault(v["parent_index"], []).append(i)
    for indices in children_by_parent.values():
        for sib_idx, view_idx in enumerate(indices):
            views[view_idx]["sibling_index"] = sib_idx

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
    # 2026-04-30: lifecycle / library invisible fragments — UI 가 아니라
    # observer/manager 용. 메가커피 등에서 ReportFragment 가 100% 점유 노이즈.
    "ReportFragment",                  # androidx.lifecycle.ReportFragment
    "SupportRequestManagerFragment",   # Glide
    "RequestManagerFragment",          # Glide
    "LifecycleCallback",               # AOSP lifecycle bookkeeping
    "ProcessLifecycleOwner",
    "FragmentManagerImpl",             # FM bookkeeping leaked
    "BackStackRecord",
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
            # Filter out invisible lifecycle/library fragments — they are
            # always RESUMED and would dominate the result.
            ui_entries = [
                (cls, tag, state) for cls, tag, state in entries
                if cls not in _SYSTEM_FRAGMENT_CLASSES
            ]
            entries = ui_entries or entries  # all-system fallback: keep originals
            # Prefer RESUMED (state=7) or STARTED-visible (state=5 with
            # visible hint). DeskClock in ViewPager lands on state=5 for
            # the visible tab and state=4 for the off-screen preloaded ones.
            for cls, tag, state in entries:
                if state == 7 and cls not in _SYSTEM_FRAGMENT_CLASSES:
                    return tag if tag and tag.lower() not in ("tag", "null", "0") else cls
            # Fallback: highest-state fragment wins (state=5 > 4 > 3 > 1).
            entries.sort(key=lambda e: -e[2])
            for cls, tag, _ in entries:
                if cls not in _SYSTEM_FRAGMENT_CLASSES:
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
    """True if an overlay Dialog / BottomSheet / AlertDialog / Picker overlay 가 있음.

    검색 범위: 상위 30 view (이전 20 → Material 3 picker 가 좀 더 깊게 있을 수 있음).

    포함 패턴:
      - 일반: dialog, bottomsheet, popup, alertdialog
      - 입력 picker (2026-04-29 추가, DeskClock + 버튼 → TimePicker 누락 fix):
        timepicker, datepicker, numberpicker, calendarview, pickerselector
      - Material 3 변형: materialdatepicker, materialtimepicker
    """
    dialog_class_kw = (
        "dialog", "bottomsheet", "popup", "alertdialog",
        "timepicker", "datepicker", "numberpicker",
        "calendarview", "pickerselector",
        "materialdatepicker", "materialtimepicker",
    )
    dialog_id_kw = (
        "dialog", "alert", "popup",
        "time_picker", "date_picker", "picker_dialog",
        # 2026-09-13: Dialog/BottomSheet 윈도우의 표준 id — WebView 앱의 시트도 이걸로 잡힌다 (메가커피 매장 정보 시트)
        "touch_outside", "design_bottom_sheet", "bottom_sheet",
    )
    # 2026-09-13: 화면 높이의 90% 이상을 덮는 BottomSheetDialog 는 사실상 페이지다 (메가커피 매장 정보→검색→상세
    # 흐름 전체가 [0,100][1440,3035] 시트 안에서 돈다). 이걸 다이얼로그로 보면 워커는 매번 닫으려 들고(Back 가드에
    # 막혀 stall), 지도는 세 화면을 오버레이로 표시한다. 시트 뷰의 bounds 가 없으면 판단 불가 → 기존대로 다이얼로그.
    _sheet_kw = ("touch_outside", "design_bottom_sheet", "bottom_sheet")
    ys = [b for b in (_parse_bounds(v.get("bounds", "")) for v in views) if b]
    screen_h = (max(b[3] for b in ys) - min(b[1] for b in ys)) if ys else 0
    full_sheet = False
    for v in views[:40]:
        rid = (v.get("resource_id") or "").lower()
        if any(kw in rid for kw in ("design_bottom_sheet", "bottom_sheet")):
            b = _parse_bounds(v.get("bounds", ""))
            if b and screen_h and (b[3] - b[1]) >= 0.9 * screen_h:
                full_sheet = True
    for v in views[:30]:
        cls = (v.get("class") or "").lower()
        rid = (v.get("resource_id") or "").lower()
        if any(kw in cls for kw in dialog_class_kw):
            return True
        if any(kw in rid for kw in dialog_id_kw):
            if full_sheet and any(kw in rid for kw in _sheet_kw):
                continue
            return True
    return False


def is_webview_dominant(views: list[dict]) -> tuple[bool, dict | None]:
    """이 화면이 WebView 가 dominant 한지 — vision fallback 결정용.

    2026-04-30: 멘토 의견 + 실측 (view_tree_parser 코드 0건 webview 처리) 따라:
    - WebView 안 a11y 활성된 element 는 이미 자동으로 일반 view 로 parse 됨
    - 단 WebView 가 화면 50%+ 차지 + 안 자식 view 거의 없으면 → 분석 불가능
    - 그런 화면만 vision fallback 호출 (dominant + 자식 부족)

    반환: (is_dominant_webview, webview_view 또는 None)
    """
    if not views:
        return False, None

    # 화면 영역 가정 (uiautomator dump 의 root bounds 기준)
    # 보통 1080×2400 / 1080×2160. root view 의 bounds 로 결정.
    root = views[0] if views else {}
    root_bounds = _parse_bounds(root.get("bounds", ""))
    if not root_bounds:
        return False, None
    screen_area = (root_bounds[2] - root_bounds[0]) * (root_bounds[3] - root_bounds[1])
    if screen_area <= 0:
        return False, None

    webview_classes = ("WebView", "ChromeWebView", "RNCWebView",
                       "RCTWebView", "ReactWebView", "X5WebView")
    webview_views = [
        v for v in views
        if any(kw in (v.get("class") or "") for kw in webview_classes)
    ]
    if not webview_views:
        return False, None

    # 가장 큰 WebView 의 영역 비율
    largest_wv = None
    largest_area = 0
    for wv in webview_views:
        b = _parse_bounds(wv.get("bounds", ""))
        if not b:
            continue
        area = (b[2] - b[0]) * (b[3] - b[1])
        if area > largest_area:
            largest_area = area
            largest_wv = wv

    if not largest_wv:
        return False, None
    ratio = largest_area / screen_area
    if ratio < 0.5:
        return False, None  # webview 가 작은 영역 — 일반 화면

    # dominant webview 안 자식 (text 또는 clickable) 비율
    children_with_signal = sum(
        1 for v in views
        if v.get("class") != largest_wv.get("class")
        and (v.get("text") or v.get("clickable") or v.get("content_desc"))
    )
    # WebView 자체 외에 의미 있는 view 가 5개+ 면 a11y 잘 잡힌 — 분석 가능
    # 5개 미만이면 webview 안 element 가 a11y 안 노출 — vision fallback 필요
    is_problem = children_with_signal < 5
    return is_problem, largest_wv if is_problem else None


def _parse_bounds(s: str) -> tuple[int, int, int, int] | None:
    """uiautomator '[x1,y1][x2,y2]' → (x1,y1,x2,y2)."""
    import re as _re
    m = _re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", s)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))) if m else None


def detect_popup_menu(views: list[dict]) -> bool:
    """Is the overlay a user-intent popup menu (not a blocking dialog)?

    Popup menus contain tappable list items that represent app functionality
    (Settings, Share, Delete) rather than Allow/Deny prompts — these should
    be walked, not dismissed.

    2026-04-29: dropdownmenu / menucontent 추가 — Material 3 / Compose 의
    overflow menu (DeskClock 등) 가 이전 패턴에 안 잡혀 dialog 로 오인 →
    dismiss 됐었음.
    """
    popup_class_kw = (
        "popupmenu", "popup_menu",
        "listpopupwindow", "list_popup",
        "dropdownlistview", "dropdownmenu", "dropdown_menu",
        "menupopupwindow", "menu_popup",
        "menuitem", "menucontent", "menu_content",
        "cascadingmenupopup", "cascadingmenu",
        "overflowmenubutton",
    )
    alert_class_kw = ("alertdialog", "messagedialog", "confirmdialog")
    has_popup_marker = False
    has_alert_marker = False
    for v in views[:30]:
        cls = (v.get("class") or "").lower()
        parent = (v.get("parent_class") or "").lower()
        if any(kw in cls for kw in popup_class_kw) or any(kw in parent for kw in popup_class_kw):
            has_popup_marker = True
        if any(kw in cls for kw in alert_class_kw):
            has_alert_marker = True
    return has_popup_marker and not has_alert_marker


_POPUP_PARENT_PATTERNS = (
    "popupmenu", "popup_menu", "listpopupwindow", "list_popup",
    "dropdownlist", "menupopupwindow", "menu_popup",
    "cascadingmenu", "menudropdown",
    # Material 3 / Compose dropdown (DeskClock 의 overflow menu 가 이쪽)
    "dropdownmenu", "menucontent",
)
_POPUP_ITEM_RID_HINTS = ("title", "menu", "item")


def popup_items(views: list[dict]) -> list[dict]:
    """Return clickable views that are inside an active popup menu.

    2026-04-29 강화: parent_class 매칭 광범위화 + popup 영역 안의
    clickable 모두 잡기 (이전엔 4 패턴만 매칭 → DeskClock 의 Material 3
    DropdownMenu 같은 곳에서 5 item 중 일부만 잡혀 popup_items[0] 무한
    재선택 발생). 같은 화면 안 popup item 은 모두 가져오고 호출자가
    tried 로 라운드.
    """
    items: list[dict] = []
    seen: set[str] = set()  # bounds 기반 coalesce

    def _key(v: dict) -> str:
        return f"{v.get('bounds','')}|{v.get('text','')}"

    for v in views:
        if not v.get("clickable"):
            continue
        cls = (v.get("class") or "").lower()
        parent = (v.get("parent_class") or "").lower()
        in_popup = any(p in parent or p in cls for p in _POPUP_PARENT_PATTERNS)
        if in_popup:
            k = _key(v)
            if k not in seen:
                items.append(v)
                seen.add(k)
            continue
        # 2차 — resource-id hint
        rid = (v.get("resource_id") or "").lower()
        if v.get("text") and any(h in rid for h in _POPUP_ITEM_RID_HINTS):
            k = _key(v)
            if k not in seen:
                items.append(v)
                seen.add(k)
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
