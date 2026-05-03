"""View → role classifier.

Role-tags every view in a UI dump with its UX function so downstream stages
(chip_group_detector, journey_planner, ScreenPanel) can reason about
"what kind of widget is this?" rather than just "is it clickable?".

Role taxonomy (one of):
    button            generic clickable button / icon button
    radio             radio button (mutually-exclusive selector member)
    checkbox          checkbox (multi-select member)
    stepper_minus     "-" button next to a numeric display
    stepper_plus      "+" button next to a numeric display
    stepper_display   numeric value (1, 2, 99) shown between stepper +/-
    dropdown          spinner / dropdown / pull-down list
    input             EditText / text input
    image             clickable/non-clickable visual asset
    text              non-clickable label (read-only text)
    label             plain label associated with another widget

Classifier is **purely heuristic** (no LLM). Designed to be cheap enough
to run on every view in every dump. LLM Vision augmentation is the
chip_group_detector's responsibility.
"""

from __future__ import annotations

import re
from typing import Iterable

ROLES = (
    "button", "radio", "checkbox",
    "stepper_minus", "stepper_plus", "stepper_display",
    "dropdown", "input",
    "image", "text", "label",
)

# Class-substring → role. Checked in priority order; first match wins.
_CLASS_HINTS: tuple[tuple[str, str], ...] = (
    ("RadioButton",   "radio"),
    ("CheckBox",      "checkbox"),
    ("Switch",        "checkbox"),    # Material Switch behaves like checkbox
    ("ToggleButton",  "checkbox"),
    ("Spinner",       "dropdown"),
    ("Dropdown",      "dropdown"),
    ("AutoComplete",  "dropdown"),
    ("EditText",      "input"),
    ("TextField",     "input"),       # Compose
    ("ImageButton",   "button"),      # icon button — keep generic button role
    ("Button",        "button"),
    ("ImageView",     "image"),
)

# stepper +/- glyphs (Korean apps often use halfwidth/fullwidth/symbols)
_PLUS_GLYPHS = {"+", "＋", "➕", "+ "}
_MINUS_GLYPHS = {"-", "－", "−", "➖", "- "}

# Numeric stepper display: standalone integer up to 4 digits
_DIGIT_DISPLAY = re.compile(r"^\s*\d{1,4}\s*$")


def classify_role(view: dict) -> str:
    """Classify a single view dict into one of the ROLES strings.

    Order of precedence:
      1. Class-name substring (most reliable — Android stdlib widgets)
      2. Stepper glyph in text/content_desc
      3. Numeric display pattern (only when bounds suggest a stepper context —
         caller should re-classify via context if needed; here we just emit
         "stepper_display" candidate when the text is a plain integer)
      4. Editability (EditText escapees)
      5. Clickability fallback (button)
      6. Text presence fallback (text/label)
      7. Otherwise → "image"
    """
    cls = view.get("class", "") or ""
    text = (view.get("text") or "").strip()
    desc = (view.get("content_desc") or "").strip()

    # 1. Class-substring hints
    for hint, role in _CLASS_HINTS:
        if hint in cls:
            return role

    # 2. Stepper +/- glyphs (text or desc)
    if text in _PLUS_GLYPHS or desc.lower() in ("plus", "increase", "increment", "더하기", "추가"):
        return "stepper_plus"
    if text in _MINUS_GLYPHS or desc.lower() in ("minus", "decrease", "decrement", "빼기", "감소"):
        return "stepper_minus"

    # 3. Numeric display (candidate; chip_group_detector confirms via siblings)
    if text and _DIGIT_DISPLAY.match(text):
        return "stepper_display"

    # 4. Editable fallback
    if view.get("editable") or view.get("focusable") and view.get("clickable") and not text:
        # weak input signal — Compose TextField doesn't always advertise EditText
        if "Field" in cls or "Input" in cls:
            return "input"

    # 5. Clickability → button
    if view.get("clickable") or view.get("long_clickable"):
        return "button"

    # 6. Text presence fallback
    if text or desc:
        return "text"

    # 7. Default
    return "image"


def annotate_roles(views: Iterable[dict]) -> int:
    """Mutate each view dict in-place, adding ``view['role'] = ...``.

    Returns the count of views annotated. Idempotent — re-running on already
    annotated views overwrites with the same result.
    """
    count = 0
    for v in views:
        v["role"] = classify_role(v)
        count += 1
    return count
