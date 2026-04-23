"""Build Screen Cards for LLM analysis — one unit per page."""

import logging
from .view_tree_cleaner import views_to_cleaned_xml

logger = logging.getLogger(__name__)


def build_screen_cards(pages: list[dict], transitions: list[dict]) -> list[dict]:
    """Package each page into a Screen Card for LLM input.

    Each unit contains:
      - screen_id, activity_name
      - cleaned_xml (compact XML for LLM)
      - available_actions (interactive elements)
      - navigation_context (from/to pages)
      - screenshot reference
    """
    # Build transition lookup
    outgoing: dict[str, list[str]] = {}
    incoming: dict[str, list[str]] = {}
    for t in transitions:
        fp = t.get("from_page", "")
        tp = t.get("to_page", "")
        outgoing.setdefault(fp, []).append(tp)
        incoming.setdefault(tp, []).append(fp)

    screen_cards = []
    for page in pages:
        pid = page["page_id"]

        # Build cleaned XML from elements
        cleaned_xml = views_to_cleaned_xml(
            page.get("elements", [])
        )

        # Available actions
        available_actions = []
        for elem in page.get("elements", []):
            for action_type in elem.get("action_types", []):
                desc_parts = [elem.get("class", "")]
                if elem.get("text"):
                    desc_parts.append(f'text="{elem["text"]}"')
                if elem.get("content_desc"):
                    desc_parts.append(f'desc="{elem["content_desc"]}"')

                available_actions.append({
                    "widget_id": elem["widget_id"],
                    "type": action_type,
                    "description": ", ".join(desc_parts),
                })

        # Navigation context
        reachable = list(set(outgoing.get(pid, [])))
        from_screens = list(set(incoming.get(pid, [])))

        # screen_clusterer writes page["fragment_class"]; fall back to
        # page["fragment"] for legacy cluster shapes. Emit BOTH so
        # downstream consumers (screenmap_builder, dashboard) that expect either
        # key keep working.
        fragment_id = (page.get("fragment_class") or page.get("fragment") or "").strip()
        unit = {
            "screen_id": pid,
            "activity_name": page.get("activity", ""),
            "fragment": fragment_id,
            "fragment_class": fragment_id,
            "node_type": page.get("node_type", "activity"),
            "screenshot": page.get("screenshot_path", ""),
            "cleaned_xml": cleaned_xml,
            "available_actions": available_actions,
            "navigation_context": {
                "from_screens": from_screens,
                "reachable_screens": reachable,
            },
            "widget_count": len(page.get("elements", [])),
            "structure_str": page.get("structure_str", ""),
            "label_hint": page.get("label_hint", ""),
        }
        screen_cards.append(unit)

    logger.info("Built %d context units", len(screen_cards))
    return screen_cards
