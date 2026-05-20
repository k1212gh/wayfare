"""Analyze res/layout/*.xml to extract clickable elements and UI structure.

STATUS (2026-05-14): UNUSED — zero callers in the current pipeline.

Not wired up because:
  - Input requires a decompiled res/ dir. Raw APK contains binary AXML; the
    ET parser below can't read it. Only decompiler.py produces text XML, and
    that module is also unused.
  - Dynamic uiautomator already captures the same info live (view_tree_parser.py)
    with accurate visibility/inflate handling that static XML can't see.
  - The `activities` parameter is declared but never used in the body —
    activity↔layout binding (setContentView / R.layout.X) was never
    implemented, so layout dicts have no activity to attach to.

Removal candidate. If revived, also: (a) enable decompiler.py (apktool),
(b) add a setContentView pattern to dex_transitions.py, (c) gate execution
to framework=='xml' (Compose/Flutter/RN have no layout XMLs).
"""

import logging
from pathlib import Path
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

ANDROID_NS = "http://schemas.android.com/apk/res/android"

CLICKABLE_CLASSES = {
    "Button", "ImageButton", "FloatingActionButton",
    "MaterialButton", "AppCompatButton", "ExtendedFloatingActionButton",
}

INPUT_CLASSES = {
    "EditText", "AutoCompleteTextView", "TextInputEditText",
    "SearchView", "AppCompatEditText",
}

SCROLLABLE_CLASSES = {
    "RecyclerView", "ListView", "ScrollView",
    "NestedScrollView", "HorizontalScrollView", "ViewPager", "ViewPager2",
}


def analyze_layouts(res_dir: str, activities: list[dict]) -> list[dict]:
    """Analyze all layout XMLs and extract interactive elements.

    Returns list of layout dicts with clickable/input/scrollable elements.
    """
    res_path = Path(res_dir)
    layouts = []

    layout_dirs = [d for d in res_path.iterdir() if d.is_dir() and d.name.startswith("layout")]
    if not layout_dirs:
        logger.warning("No layout directories found in %s", res_dir)
        return layouts

    seen_files: set[str] = set()
    for layout_dir in layout_dirs:
        for xml_file in layout_dir.glob("*.xml"):
            if xml_file.name in seen_files:
                continue
            seen_files.add(xml_file.name)

            try:
                layout_info = _analyze_single_layout(xml_file)
                if layout_info:
                    layouts.append(layout_info)
            except ET.ParseError:
                logger.warning("Failed to parse layout: %s", xml_file)

    logger.info("Analyzed %d layout files", len(layouts))
    return layouts


def _analyze_single_layout(xml_path: Path) -> dict | None:
    tree = ET.parse(xml_path)
    root = tree.getroot()

    clickable = []
    inputs = []
    scrollable = []

    for elem in root.iter():
        tag = _short_tag(elem.tag)
        res_id = elem.attrib.get(f"{{{ANDROID_NS}}}id", "")
        res_id = _clean_id(res_id)
        is_clickable = elem.attrib.get(f"{{{ANDROID_NS}}}clickable", "") == "true"
        text = elem.attrib.get(f"{{{ANDROID_NS}}}text", "")

        info = {"tag": tag, "id": res_id, "text": text}

        if tag in CLICKABLE_CLASSES or is_clickable:
            clickable.append(info)
        if tag in INPUT_CLASSES:
            hint = elem.attrib.get(f"{{{ANDROID_NS}}}hint", "")
            info["hint"] = hint
            inputs.append(info)
        if tag in SCROLLABLE_CLASSES:
            scrollable.append(info)

    return {
        "file": xml_path.name,
        "clickable_widgets": clickable,
        "input_widgets": inputs,
        "scrollable_widgets": scrollable,
        "total_views": sum(1 for _ in root.iter()),
    }


def _short_tag(tag: str) -> str:
    """Strip namespace and package prefix from tag name."""
    if "}" in tag:
        tag = tag.split("}")[1]
    if "." in tag:
        tag = tag.rsplit(".", 1)[1]
    return tag


def _clean_id(res_id: str) -> str:
    """Convert @+id/foo or @id/foo to foo."""
    if "/" in res_id:
        return res_id.split("/", 1)[1]
    return res_id
