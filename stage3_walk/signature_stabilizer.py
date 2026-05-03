"""Hash-stabilizing helpers for ``_capture_screen``.

Isolated so the transformations are unit-testable without spinning up a
device or the whole walker. Imported by `mixins/capture.py`.

Stable fields for hashing are:
- resource_id with numeric suffix stripped (``timer_item_3`` → ``timer_item_*``)
- content_desc with time-like patterns removed (``3:45`` → ``*``)
- view class filtered to drop animated/dynamic classes

See also: C 접근법 in `~/.claude/plans/breezy-brewing-harp.md`
"""

from __future__ import annotations

import hashlib
import json
import re


# View classes known to animate / tick — their content/text varies every dump
# but they all represent the same logical region. Stripping them from the
# structure hash stops same-screen coalesce failures on clock/timer apps.
_DYNAMIC_CLASSES = frozenset({
    "android.widget.AnalogClock",
    "android.widget.TextClock",
    "android.widget.Chronometer",
    "android.widget.ProgressBar",
    "android.media.AudioVisualization",
    "androidx.core.widget.ContentLoadingProgressBar",
})

# Resource-id numeric suffix: ``...row_12`` / ``...item3`` / ``..._0002``.
# Tail of digits (possibly preceded by ``_`` or ``:`` or nothing) replaced by ``*``.
_NUM_SUFFIX = re.compile(r"([_:])?\d+$")

# Time-like content_desc patterns that change every tick / view.
_TIME_PATTERNS = (
    re.compile(r"\d{1,2}\s*:\s*\d{2}(?:\s*:\s*\d{2})?"),   # 3:45:30, 12:59
    re.compile(r"\d{1,2}시\s*\d{1,2}분(?:\s*\d{1,2}초)?"),  # 3시 45분 30초
    re.compile(r"\d{2}/\d{2}/\d{2,4}"),                     # 04/23/2026
    re.compile(r"\d+\s*(seconds?|sec|초)"),                  # 30 seconds, 30초
    re.compile(r"\d+(\.\d+)?"),                              # standalone numbers (timers)
)


def stabilize_resource_id(rid: str) -> str:
    """Strip a numeric tail so RecyclerView items that only differ by index
    collapse to the same signature. No-op for ids without trailing digits."""
    if not rid:
        return rid
    m = _NUM_SUFFIX.search(rid)
    if m and m.start() > 0:   # keep at least one character in front
        return rid[: m.start()] + (m.group(1) or "") + "*"
    return rid


def stabilize_content_desc(desc: str) -> str:
    """Mask time-like tokens so ticking clocks/timers don't perturb the hash."""
    if not desc:
        return desc
    out = desc
    for pat in _TIME_PATTERNS:
        out = pat.sub("*", out)
    return out.strip()


def is_dynamic_class(cls: str) -> bool:
    """Return True if a view class is in our dynamic/animated set."""
    if not cls:
        return False
    return cls in _DYNAMIC_CLASSES


def _stable_view_sig(view: dict) -> tuple[str, str] | None:
    """Canonical (class, stabilized resource_id + content_desc) tuple.
    Returns None if the view should be excluded from the hash entirely."""
    cls = view.get("class", "") or ""
    if is_dynamic_class(cls):
        return None   # skip entirely
    rid = stabilize_resource_id(view.get("resource_id", "") or "")
    desc = stabilize_content_desc(view.get("content_desc", "") or "")
    return (cls, f"{rid}@{desc}")


def collapse_scroll_children(views: list[dict]) -> list[dict]:
    """Drop direct children of scrollable containers from the hash input.

    Why: in feed/list screens the same logical screen emits a different child
    count every time the user scrolls (RecyclerView/LazyColumn lazily inflate
    items). If those children land in the structural hash, every scroll
    position becomes a fresh canonical_id and the ScreenMap explodes with duplicates.

    Strategy: keep the scrollable parent itself (so the hash still records
    "this screen has a list region"), but drop its direct children. Children's
    own children are kept intact only if they had a non-scrollable ancestor;
    in practice list items are leaves so this collapses cleanly.

    Requires `parent_index` on each view (added by view_tree_parser walk()). Falls
    back to a no-op if parent_index is missing on every view (legacy dumps).

    A debug breadcrumb `_scroll_child_bucket` is attached to each surviving
    scrollable parent — NOT included in the hash to keep stability across
    item-count drift; useful for inspection / Stage 6 marking.
    """
    if not views or "parent_index" not in views[0]:
        return views   # legacy view dicts without parent_index — no-op

    scroll_parents: set[int] = {
        i for i, v in enumerate(views) if v.get("scrollable")
    }
    if not scroll_parents:
        return views

    child_counts: dict[int, int] = {}
    for v in views:
        p = v.get("parent_index")
        if p in scroll_parents:
            child_counts[p] = child_counts.get(p, 0) + 1

    def _bucket(n: int) -> str:
        if n == 0: return "empty"
        if n < 5:  return "few"
        if n < 20: return "some"
        return "many"

    out: list[dict] = []
    for i, v in enumerate(views):
        if v.get("parent_index") in scroll_parents:
            continue   # drop direct children of scrollable parents
        if i in scroll_parents:
            v = {**v, "_scroll_child_bucket": _bucket(child_counts.get(i, 0))}
        out.append(v)
    return out


def compute_structure_str(activity: str, fragment: str, views: list[dict]) -> str:
    """Stabilized structure hash — stable across ticking clocks, RecyclerView
    item count drift, animated Views, AND scroll position in feed-like screens.
    See module docstring for the rules.

    Preserves the legacy composition (activity | fragment | sorted clickable
    resource-ids) but pipes each resource-id through `stabilize_resource_id`,
    drops ticking/animated classes, strips time-patterns from content_desc
    when the view is clickable, and collapses scrollable containers' children
    via `collapse_scroll_children` (so infinite-scroll feeds don't explode
    into N canonical_ids).
    """
    views = collapse_scroll_children(views)
    stable_ids: set[str] = set()   # set, not list — collapses N repeats of
                                    # the same stabilized id (RecyclerView
                                    # with drifting item count) to a single entry.
    for v in views:
        if not v.get("clickable"):
            continue
        if is_dynamic_class(v.get("class", "") or ""):
            continue
        rid = stabilize_resource_id(v.get("resource_id", "") or "")
        desc = stabilize_content_desc(v.get("content_desc", "") or "")
        # If neither signal survives, fall back to the raw class name so we
        # still distinguish clickable-but-untagged views (common in Compose)
        stable_ids.add(rid or desc or (v.get("class", "") or ""))
    payload = f"{activity}|{fragment}|{'|'.join(sorted(stable_ids))}"
    return hashlib.sha256(payload.encode()).hexdigest()


def compute_state_str(activity: str, fragment: str, views: list[dict]) -> str:
    """Stabilized per-instance hash — excludes dynamic views' text so a live
    clock ticking doesn't produce N different state_strs. Keeps text from
    other views so truly-different instances remain distinguishable. Also
    collapses scrollable containers' children to keep infinite-scroll feeds
    from generating per-scroll-position state_strs.
    """
    views = collapse_scroll_children(views)
    sanitized_texts: list[str] = []
    for v in views[:20]:
        if is_dynamic_class(v.get("class", "") or ""):
            continue
        text = v.get("text", "") or ""
        sanitized_texts.append(stabilize_content_desc(text))
    payload = f"{activity}|{fragment}|{json.dumps(sanitized_texts)}"
    return hashlib.sha256(payload.encode()).hexdigest()
