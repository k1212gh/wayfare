"""Vision-based screen labeler using Claude 4 multimodal.

Reads nodes that have a screenshot_ref and asks Claude Vision to determine:
- functional_category (home/list/detail/form/auth/settings/media/search/dialog/other)
- screen_purpose (one sentence)
- key_widgets' role (from visible UI)

This complements `screenmap_annotator` which only has XML + graph structure. Vision
sees the actual rendered pixels — critical for Compose/WebView/RN screens where
the XML tree is semantically bare (just ComposeView / ReactViewGroup / WebView).

Cost: ~$0.005 per image (Sonnet 4.6 vision). For a 15-40 shot app, total <$0.20.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config import PipelineConfig

logger = logging.getLogger(__name__)

CATEGORY_ENUM = [
    "home", "list", "detail", "form", "auth", "settings",
    "dialog", "media", "search", "other",
]


def label_screens_with_vision(config: PipelineConfig) -> None:
    """Walk ScreenMap nodes with screenshot_ref, send each to Claude Vision.

    Updates in place:
      - functional_category (constrained to enum)
      - screen_purpose
      - label (if LLM produced a better short name)
      - widgets[*].role (from what's visible)
      - confidence

    Skips nodes already labeled with high confidence and non-"other" category.
    """
    screenmap_path = config.output_dir / config.screenmap_output_filename
    if not screenmap_path.exists():
        logger.warning("No ScreenMap at %s — skipping vision labeler", screenmap_path)
        return
    screenmap = json.loads(screenmap_path.read_text(encoding="utf-8"))
    graph = screenmap.get("screen_map", {}).get("graph", {})
    nodes = graph.get("nodes", [])
    if not nodes:
        return

    candidates = [n for n in nodes if _should_label(n)]
    if not candidates:
        logger.info("Vision labeler: no candidate nodes (all already labeled or no screenshots)")
        return
    logger.info("Vision labeler: %d / %d nodes have screenshots to label",
                len(candidates), len(nodes))

    from .llm_client import create_client
    client = create_client(
        api_key=config.anthropic_api_key,
        model_screen=config.llm_model_screen,
        model_widget=config.llm_model_widget,
        temperature=config.llm_temperature,
        max_retries=config.llm_max_retries,
    )

    system_prompt = _system_prompt()
    labeled = 0
    for i, n in enumerate(candidates):
        ss_path = _resolve_screenshot(n.get("screenshot_ref", ""))
        if not ss_path or not ss_path.exists():
            continue
        try:
            img_bytes = ss_path.read_bytes()
        except Exception as e:
            logger.debug("Could not read %s: %s", ss_path, e)
            continue

        media = "image/jpeg" if ss_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        user_prompt = _build_user_prompt(n)

        try:
            resp = client.query_with_image(
                system_prompt, user_prompt, img_bytes,
                image_media_type=media, max_tokens=1024,
            )
        except Exception as e:
            logger.warning("Vision call failed for %s: %s", n.get("screen_id"), e)
            continue

        ann = _parse_vision_response(resp)
        if ann:
            _apply_vision_annotation(n, ann)
            labeled += 1

        # Persist every 5 labels so partial progress survives
        if labeled and labeled % 5 == 0:
            screenmap_path.write_text(json.dumps(screenmap, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("Vision labeler: %d / %d done (saved)", i + 1, len(candidates))

    # Final save
    screenmap_path.write_text(json.dumps(screenmap, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Vision labeler: %d nodes labeled", labeled)


# ─── Candidate filter ──────────────────────────────────────

def _should_label(node: dict) -> bool:
    if not node.get("screenshot_ref"):
        return False
    if node.get("screen_id") == "system:external_entry":
        return False
    # Re-label if category is 'other' or still the default
    cat = node.get("functional_category", "other")
    has_purpose = bool(node.get("screen_purpose"))
    if cat != "other" and has_purpose:
        return False
    return True


def _resolve_screenshot(ref: str) -> Path | None:
    if not ref:
        return None
    p = Path(ref)
    if p.is_absolute() and p.exists():
        return p
    return None


# ─── Prompts ──────────────────────────────────────────────

def _system_prompt() -> str:
    return (
        "You are an Android-UI labeler for a knowledge-graph pipeline consumed by "
        "a MobileGPT-style agent. Given a single screen screenshot and its "
        "activity metadata, produce a compact JSON describing the screen.\n\n"
        "Required fields:\n"
        f"  - functional_category: one of {CATEGORY_ENUM}\n"
        "  - label: short Korean OR English phrase (<=40 chars) — what this screen is\n"
        "  - screen_purpose: one sentence, what the user does here\n"
        "  - primary_affordances: up to 5 clickable elements the user is likely to hit "
        "(each as short phrase)\n"
        "  - confidence: 'high' | 'medium' | 'low'\n\n"
        "Respond ONLY with valid JSON, no markdown fences, no preamble.\n"
        "If the screen looks like a loading spinner / empty scaffold / ambiguous "
        "redirect, set confidence='low' and mark screen_purpose accordingly."
    )


def _build_user_prompt(node: dict) -> str:
    act = node.get("activity", "") or ""
    short = act.rsplit(".", 1)[-1] if "." in act else act
    elems = node.get("widgets", []) or []
    elem_ids = [e.get("id", "") for e in elems[:10] if e.get("id")]
    intent = ""
    ifs = node.get("intent_filters") or []
    if ifs and isinstance(ifs, list):
        actions = ifs[0].get("actions", []) if isinstance(ifs[0], dict) else []
        if actions:
            intent = ", ".join(actions[:2])
    return (
        f"Activity FQN: {act}\n"
        f"Short name: {short}\n"
        f"UI element IDs present: {elem_ids}\n"
        + (f"Intent actions: {intent}\n" if intent else "")
        + "\nLabel this screen."
    )


# ─── Response parsing / merge ─────────────────────────────

def _parse_vision_response(text: str) -> dict | None:
    import re
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _apply_vision_annotation(node: dict, ann: dict) -> None:
    cat = (ann.get("functional_category") or "").strip().lower()
    if cat in CATEGORY_ENUM:
        node["functional_category"] = cat
    label = (ann.get("label") or "").strip()
    if label:
        node["label"] = label[:50]
    purpose = (ann.get("screen_purpose") or "").strip()
    if purpose:
        node["screen_purpose"] = purpose
    conf = (ann.get("confidence") or "").strip().lower()
    if conf in ("high", "medium", "low"):
        node["confidence"] = conf
    # Attach vision-derived affordances as a separate field (doesn't mangle widgets)
    aff = ann.get("primary_affordances") or []
    if isinstance(aff, list) and aff:
        node["primary_affordances"] = [str(x)[:60] for x in aff[:5]]
