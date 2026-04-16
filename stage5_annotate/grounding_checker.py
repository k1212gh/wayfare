"""Check LLM output for hallucinated element IDs not present in actual XML."""

import logging

logger = logging.getLogger(__name__)

# Valid functional_category values (spec)
VALID_CATEGORIES = {
    "login", "home", "settings", "content_detail", "search",
    "list", "form", "profile", "navigation", "other",
}

VALID_ACTION_TYPES = {"click", "input", "scroll", "toggle", "long_click"}
VALID_CONFIDENCE = {"high", "medium", "low"}


def check_grounding(analysis: dict, valid_widget_ids: set[str]) -> dict:
    """Verify LLM output against actual XML data.

    FIX #12:
    - Check elements even when valid_widget_ids is empty (flag all as suspicious)
    - Remove elements with empty widget_id
    - Validate functional_category and confidence enums
    """
    key_widgets = analysis.get("key_widgets", [])
    clean_widgets = []
    hallucinated = []

    for elem in key_widgets:
        eid = elem.get("widget_id", "")

        # FIX: Empty widget_id is always suspicious
        if not eid:
            hallucinated.append("(empty)")
            continue

        # If we have valid IDs, check membership
        if valid_widget_ids and eid not in valid_widget_ids:
            hallucinated.append(eid)
            continue

        # If no valid IDs available, flag elements but keep them with warning
        clean_widgets.append(elem)

    if hallucinated:
        logger.warning(
            "Removed %d hallucinated/empty elements: %s",
            len(hallucinated),
            hallucinated[:5],
        )
        analysis["key_widgets"] = clean_widgets
        if analysis.get("confidence") == "high":
            analysis["confidence"] = "medium"
        elif analysis.get("confidence") == "medium":
            analysis["confidence"] = "low"
        analysis["ungrounded_widgets"] = hallucinated

    # Validate enum fields
    category = analysis.get("functional_category", "")
    if category and category not in VALID_CATEGORIES:
        logger.warning("Invalid functional_category '%s', defaulting to 'other'", category)
        analysis["functional_category"] = "other"

    confidence = analysis.get("confidence", "")
    if confidence and confidence not in VALID_CONFIDENCE:
        analysis["confidence"] = "medium"

    # Validate action_types in elements
    for elem in analysis.get("key_widgets", []):
        action = elem.get("action_type", "")
        if action and action not in VALID_ACTION_TYPES:
            elem["action_type"] = "click"  # safe default

    return analysis
