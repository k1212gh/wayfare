"""Heuristic screen classifier — assigns a functional_category without LLM.

Uses UI element patterns (resource_id keywords, element types, text markers)
to guess one of 8 categories. Rough accuracy ~70% for Korean+English apps.
When the LLM runs later it can overwrite these labels with higher confidence.
"""

from __future__ import annotations

from typing import Any

CATEGORIES = ["home", "list", "detail", "form", "auth", "settings", "search", "other"]

# System/OS screens that are not part of the app's own flow
SYSTEM_ACTIVITY_SUBSTRINGS = (
    "packageinstaller",
    "nexuslauncher",
    "sec.android.app.launcher",
    "systemui",
    "android.settings",
    "permissioncontroller",
)


def classify_node(unit: dict, activity: str, is_entry: bool) -> tuple[str, str]:
    """Return (category, confidence). Confidence ∈ {high, medium, low}."""
    act_lower = (activity or "").lower()

    # 0. Launcher / system screens — treat as 'other' (could also be filtered out)
    if any(s in act_lower for s in SYSTEM_ACTIVITY_SUBSTRINGS):
        return "other", "high"

    # 1. Activity-name hints win when unambiguous
    if any(kw in act_lower for kw in ("login", "signin", "sign_in", "logon", "oauth", "auth")):
        return "auth", "high"
    if any(kw in act_lower for kw in ("setting", "preference", "profile", "account")):
        return "settings", "high"
    if any(kw in act_lower for kw in ("search",)):
        return "search", "medium"

    # Signals from elements
    actions = unit.get("available_actions", []) or []
    rids = " ".join(((a.get("widget_id") or "") for a in actions)).lower()
    texts = " ".join(((a.get("description") or "") for a in actions)).lower()
    joined = rids + " " + texts
    label_hint = (unit.get("label_hint") or "").lower()

    n_inputs = sum(1 for a in actions if "edittext" in (a.get("description") or "").lower())

    # 2. Password field → auth (near-certain)
    if "password" in rids or "pwd" in rids or "password" in texts:
        return "auth", "high"

    # 3. Search box
    has_search_kw = any(kw in joined or kw in label_hint for kw in ("검색", "search"))
    if has_search_kw and n_inputs >= 1:
        return "search", "high"
    if has_search_kw:
        return "search", "medium"

    # 4. Settings / profile by label
    if any(kw in label_hint for kw in ("설정", "계정", "프로필", "settings", "account", "profile")):
        return "settings", "medium"

    # 5. Form (≥2 inputs + submit-like button)
    submit_kw = ("확인", "저장", "제출", "완료", "등록", "submit", "complete", "register", "save")
    has_submit = any(kw in joined for kw in submit_kw)
    if n_inputs >= 2 and has_submit:
        return "form", "high"
    if n_inputs >= 2:
        return "form", "medium"

    # 6. Home — entry activity with multiple actions (not just a splash)
    if is_entry and len(actions) >= 3:
        return "home", "high"

    # 7. List — many actions in a RecyclerView-like container (hinted by repeat count)
    # We can't perfectly detect RecyclerView here, but lots of similar actions suggests a list
    if len(actions) >= 8:
        return "list", "medium"

    # 8. Detail — few but meaningful actions, often with descriptive label_hint
    if label_hint and len(actions) <= 5 and len(actions) > 0:
        return "detail", "low"

    return "other", "low"


def classify_all_nodes(nodes: list[dict], screen_cards: list[dict], entry_node: str) -> None:
    """Apply classification in-place to every node missing a meaningful category."""
    unit_map = {u["screen_id"]: u for u in screen_cards}
    for node in nodes:
        # Don't overwrite LLM-supplied high-confidence categories
        current = node.get("functional_category", "other")
        if current not in ("", "other"):
            continue  # LLM already set something
        sid = node.get("screen_id", "")
        unit = unit_map.get(sid, {})
        activity = node.get("activity", "") or unit.get("activity_name", "")
        is_entry = sid == entry_node
        cat, conf = classify_node(unit, activity, is_entry)
        node["functional_category"] = cat
        # Only upgrade confidence if current is low/empty
        if not node.get("confidence") or node.get("confidence") == "low":
            node["confidence"] = conf
