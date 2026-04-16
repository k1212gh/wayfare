"""Clean DroidBot view hierarchy — remove noise, keep functional attributes."""

import logging

logger = logging.getLogger(__name__)

# Attributes to keep (spec)
KEEP_ATTRS = {
    "resource_id", "class", "text", "content_desc",
    "clickable", "scrollable", "enabled", "long_clickable",
    "editable", "checked",
}

# Attributes to drop
DROP_ATTRS = {
    "package", "checkable", "focusable", "focused",
    "selected", "bounds", "size", "temp_id",
    "view_str", "naf",
}


def clean_views(views: list[dict], max_depth: int = 10) -> list[dict]:
    """Clean DroidBot view hierarchy.

    - Remove noise attributes (bounds, package, focusable, etc.)
    - Keep functional attributes (resource-id, class, text, clickable, etc.)
    - Filter empty/invisible nodes
    - Limit tree depth
    """
    cleaned = []
    for view in views:
        cv = _clean_single_view(view)
        if cv and cv.get("depth", 0) <= max_depth:
            cleaned.append(cv)
    return cleaned


def _clean_single_view(view: dict) -> dict | None:
    """Clean a single view dict, removing noise and empty views."""
    # Skip invisible views
    if not view.get("visible", True):
        return None

    cleaned = {}
    for key, val in view.items():
        if key in DROP_ATTRS:
            continue
        if key in KEEP_ATTRS or key in ("children", "parent", "depth"):
            cleaned[key] = val

    # Normalize resource_id: strip package prefix
    rid = cleaned.get("resource_id", "")
    if rid and "/" in rid:
        cleaned["resource_id"] = rid.split("/", 1)[1]

    # Normalize class: strip android.widget. prefix
    cls = cleaned.get("class", "")
    if cls:
        cleaned["class"] = cls.rsplit(".", 1)[-1]

    # Skip if view has no useful info at all
    has_info = any(
        cleaned.get(k)
        for k in ("resource_id", "text", "content_desc", "clickable", "scrollable", "editable")
    )
    if not has_info and not cleaned.get("children"):
        return None

    return cleaned


def views_to_cleaned_xml(cleaned_views: list[dict]) -> str:
    """Convert cleaned views list to simplified XML string for LLM input.

    Produces compact XML like:
      <node resource-id="btn_search" class="ImageButton" content-desc="Search" clickable="true"/>
    """
    lines = ['<hierarchy>']
    for view in cleaned_views:
        attrs = []
        if view.get("resource_id"):
            attrs.append(f'resource-id="{view["resource_id"]}"')
        if view.get("class"):
            attrs.append(f'class="{view["class"]}"')
        if view.get("text"):
            attrs.append(f'text="{_escape_xml(view["text"])}"')
        if view.get("content_desc"):
            attrs.append(f'content-desc="{_escape_xml(view["content_desc"])}"')
        if view.get("clickable"):
            attrs.append('clickable="true"')
        if view.get("scrollable"):
            attrs.append('scrollable="true"')
        if view.get("editable"):
            attrs.append('editable="true"')
        if view.get("long_clickable"):
            attrs.append('long-clickable="true"')

        if attrs:
            lines.append(f'  <node {" ".join(attrs)}/>')
    lines.append('</hierarchy>')
    return "\n".join(lines)


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
