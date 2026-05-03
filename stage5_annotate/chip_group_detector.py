"""Detect option groups (radio / checkbox / stepper / dropdown) on a screen.

Two-stage detection:

  1. **Heuristic sibling-pattern pass** (free, runs always)
     Scans views for sibling clusters that look like option groups:
       - 3~5 sibling buttons/radios under the same parent_index → radio group
       - 2+ sibling checkboxes → checkbox group
       - stepper_minus + stepper_display + stepper_plus trio → stepper
       - Spinner/Dropdown role view → dropdown

  2. **LLM Vision naming** (opt-in, OPTION_DETECT_LLM=1)
     For each candidate group, asks Claude Sonnet 4.6 Vision to:
       - Confirm the group is a real option group (vs. unrelated buttons that
         happen to be siblings — e.g. a row of 4 quick-action shortcuts)
       - Assign a semantic group_id ("size", "temperature", "shots")
       - For radio groups, identify the default-selected option
     Cost: ~$0.005 per screen with options. Single batch call per node.

Output schema (attached to screen_cards / nodes as ``chip_groups``):
    [
      {
        "group_id": "size",         # LLM-named or "group_<idx>" fallback
        "type": "radio",            # radio | checkbox | stepper | dropdown
        "options": [                 # for radio/checkbox/dropdown
          {"value": "Small",  "widget_id": "btn_small",  "selected_default": false},
          ...
        ],
        # for stepper:
        "minus_widget": "btn_minus",
        "plus_widget":  "btn_plus",
        "display_widget": "txt_qty",
        "min": 1, "max": 99, "default": 1,
        "required": true,
        "detection": {"method": "heuristic" | "llm_vision", "confidence": "high|med|low"}
      },
      ...
    ]

Requires Day-1 view dict with ``parent_index``, ``sibling_index``, and
Stage-4 ``role`` (from widget_classifier).
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)

# Sibling-pattern thresholds
_RADIO_SIBLING_MIN = 2
_RADIO_SIBLING_MAX = 7
_CHECKBOX_SIBLING_MIN = 2

# Dynamically-namable roles (used for radio detection from generic buttons)
_BUTTON_LIKE = {"button", "radio"}


def detect_chip_groups(views: list[dict],
                         screenshot_path: Optional[str] = None,
                         use_llm: Optional[bool] = None) -> list[dict]:
    """Return a list of detected option groups for a single screen.

    Args:
      views: list of view dicts with role/parent_index/sibling_index/widget_id.
      screenshot_path: optional path to the screen capture for LLM Vision naming.
      use_llm: override the OPTION_DETECT_LLM env flag. None = honor env.

    Returns:
      List of option group dicts (see module docstring schema). Empty list if
      the screen has no detectable groups.
    """
    if not views:
        return []
    # Ensure roles are present (caller may have skipped widget_classifier)
    if not any("role" in v for v in views):
        logger.debug("[chip_group] no roles annotated — running classifier first")
        from stage4_screens.widget_classifier import annotate_roles
        annotate_roles(views)

    # 1. Heuristic candidates
    groups = _heuristic_candidates(views)
    if not groups:
        return []

    # 2. LLM Vision naming (opt-in)
    if use_llm is None:
        use_llm = os.environ.get("OPTION_DETECT_LLM", "0") == "1"
    if use_llm and screenshot_path:
        try:
            groups = _llm_name_groups(groups, screenshot_path, views)
        except Exception as e:
            logger.warning("[chip_group] LLM naming failed, keeping heuristic names: %s", e)

    return groups


# ─────────────────────────────────────────────────────────────────────
# Heuristic detection
# ─────────────────────────────────────────────────────────────────────

def _heuristic_candidates(views: list[dict]) -> list[dict]:
    """Scan for sibling patterns that look like option groups."""
    groups: list[dict] = []

    # Group views by parent_index
    by_parent: dict[int, list[tuple[int, dict]]] = defaultdict(list)
    for i, v in enumerate(views):
        p = v.get("parent_index", -1)
        if p < 0:
            continue
        by_parent[p].append((i, v))

    group_counter = 0

    # Check each sibling group
    for parent_idx, siblings in by_parent.items():
        if len(siblings) < 2:
            continue
        sibling_views = [v for _, v in siblings]
        roles = [v.get("role", "") for v in sibling_views]

        # Pattern 1: Stepper trio (minus + display + plus among siblings)
        has_minus = any(r == "stepper_minus" for r in roles)
        has_plus = any(r == "stepper_plus" for r in roles)
        has_display = any(r == "stepper_display" for r in roles)
        if has_minus and has_plus and (has_display or len(siblings) <= 4):
            minus_v = next(v for v in sibling_views if v.get("role") == "stepper_minus")
            plus_v = next(v for v in sibling_views if v.get("role") == "stepper_plus")
            display_v = next((v for v in sibling_views if v.get("role") == "stepper_display"), None)
            group_counter += 1
            groups.append({
                "group_id": f"group_{group_counter}",
                "type": "stepper",
                "minus_widget": _eid(minus_v),
                "plus_widget": _eid(plus_v),
                "display_widget": _eid(display_v) if display_v else "",
                "min": 1, "max": 99, "default": 1,
                "required": False,
                "detection": {"method": "heuristic", "confidence": "high"},
            })
            continue   # don't double-count siblings as radio

        # Pattern 2: Radio group (2~7 buttons/radios under same parent)
        button_likes = [v for v in sibling_views if v.get("role") in _BUTTON_LIKE]
        if (_RADIO_SIBLING_MIN <= len(button_likes) <= _RADIO_SIBLING_MAX
                and len(button_likes) == len(sibling_views)):  # all siblings are buttons
            # Extract a label per option (text > content_desc > resource_id)
            options = []
            for v in button_likes:
                value = (v.get("text") or v.get("content_desc")
                         or v.get("resource_id") or "").strip()
                if not value:
                    continue
                options.append({
                    "value": value,
                    "widget_id": _eid(v),
                    "selected_default": bool(v.get("selected") or v.get("checked")),
                })
            if len(options) >= _RADIO_SIBLING_MIN:
                group_counter += 1
                # Type: explicit radio if any role=radio, else "radio" still
                # (heuristic — chip_group_detector is conservative and labels
                # "radio" by default; LLM can flip to "checkbox" if it sees check marks).
                gtype = "radio" if any(v.get("role") == "radio" for v in button_likes) else "radio"
                groups.append({
                    "group_id": f"group_{group_counter}",
                    "type": gtype,
                    "options": options,
                    "required": True,
                    "detection": {"method": "heuristic", "confidence": "med"},
                })
                continue

        # Pattern 3: Checkbox group (2+ checkboxes among siblings)
        checkboxes = [v for v in sibling_views if v.get("role") == "checkbox"]
        if len(checkboxes) >= _CHECKBOX_SIBLING_MIN:
            options = []
            for v in checkboxes:
                value = (v.get("text") or v.get("content_desc") or v.get("resource_id") or "").strip()
                if not value:
                    continue
                options.append({
                    "value": value,
                    "widget_id": _eid(v),
                    "selected_default": bool(v.get("checked")),
                })
            if options:
                group_counter += 1
                groups.append({
                    "group_id": f"group_{group_counter}",
                    "type": "checkbox",
                    "options": options,
                    "required": False,
                    "detection": {"method": "heuristic", "confidence": "high"},
                })
                continue

    # Pattern 4: Standalone dropdown views (any view with role=dropdown)
    for v in views:
        if v.get("role") != "dropdown":
            continue
        group_counter += 1
        groups.append({
            "group_id": f"group_{group_counter}",
            "type": "dropdown",
            "options": [],     # opening the dropdown is a separate interaction
            "trigger_widget": _eid(v),
            "required": False,
            "detection": {"method": "heuristic", "confidence": "high"},
        })

    return groups


def _eid(view: dict) -> str:
    """Stable element id — prefer resource_id, fall back to content_desc/text."""
    return (view.get("widget_id")
            or view.get("resource_id")
            or view.get("content_desc")
            or view.get("text")
            or "").strip()


# ─────────────────────────────────────────────────────────────────────
# LLM Vision naming (opt-in)
# ─────────────────────────────────────────────────────────────────────

_VISION_SYSTEM = (
    "You are an Android UI option-group naming assistant.\n"
    "Given a screen screenshot and a list of candidate option groups detected "
    "by sibling-pattern heuristic, your tour is to:\n"
    "  1. CONFIRM whether each candidate is a real option group (radio/checkbox/"
    "stepper/dropdown) or unrelated sibling buttons.\n"
    "  2. NAME each confirmed group with a short semantic id "
    "(\"size\", \"temperature\", \"quantity\", \"shots\", \"sweetness\", etc.).\n"
    "  3. For radio groups, identify which option is selected by default if "
    "any (highlighted, larger, different color).\n"
    "Reply JSON only: "
    '[{"index": 0, "confirmed": true, "group_id": "size", "default": "Medium"}, ...]\n'
    "Set confirmed=false to drop the candidate."
)


def _llm_name_groups(groups: list[dict],
                     screenshot_path: str,
                     views: list[dict]) -> list[dict]:
    """Use Claude Sonnet 4.6 Vision to confirm + name candidate groups.

    Reuses the image encoder from vision_labeler. Single batched call per
    screen (cost: ~$0.005 / screen with options).
    """
    try:
        from .vision_labeler import _encode_image_for_llm  # type: ignore
    except Exception:
        # vision_labeler may not expose this name yet — soft-fail with raw bytes
        from pathlib import Path
        img_bytes = Path(screenshot_path).read_bytes()
        media = "image/jpeg" if screenshot_path.lower().endswith(("jpg", "jpeg")) else "image/png"
    else:
        img_bytes, media = _encode_image_for_llm(screenshot_path, max_dim=400)

    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    candidate_desc = []
    for i, g in enumerate(groups):
        if g["type"] == "stepper":
            candidate_desc.append(f"  [{i}] type=stepper, controls=[-, value, +]")
        elif g["type"] == "dropdown":
            candidate_desc.append(f"  [{i}] type=dropdown, trigger={g.get('trigger_widget','?')}")
        else:
            opts = ", ".join(o["value"] for o in g.get("options", [])[:5])
            candidate_desc.append(f"  [{i}] type={g['type']}, options=[{opts}]")
    user_text = (
        "Candidate option groups detected on this screen:\n"
        + "\n".join(candidate_desc)
        + "\n\nConfirm + name each. Reply JSON array."
    )

    import base64
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=_VISION_SYSTEM,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": media,
                    "data": base64.standard_b64encode(img_bytes).decode("ascii"),
                }},
                {"type": "text", "text": user_text},
            ],
        }],
    )
    raw = msg.content[0].text if msg.content else "[]"

    # Parse — tolerate fenced JSON
    import json, re
    m = re.search(r"\[[\s\S]*\]", raw)
    if not m:
        return groups
    try:
        verdicts = json.loads(m.group(0))
    except Exception:
        return groups

    out: list[dict] = []
    for v in verdicts:
        idx = v.get("index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(groups):
            continue
        if not v.get("confirmed", True):
            continue   # drop unconfirmed candidates
        g = dict(groups[idx])
        if v.get("group_id"):
            g["group_id"] = str(v["group_id"]).strip()[:32]
        if v.get("default") and g.get("type") in ("radio", "checkbox"):
            for opt in g.get("options", []):
                opt["selected_default"] = (opt["value"] == v["default"])
        g["detection"] = {**g.get("detection", {}),
                          "method": "llm_vision", "confidence": "high"}
        out.append(g)
    return out if out else groups
