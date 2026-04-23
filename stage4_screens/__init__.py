"""Stage 4: Data preprocessing — XML cleaning, state clustering, context building."""

import json
import logging
from config import PipelineConfig
from .view_tree_cleaner import clean_views
from .screenshot_processor import process_screenshots
from .screen_clusterer import cluster_screens_to_pages
from .screen_card_builder import build_screen_cards

logger = logging.getLogger(__name__)


def run_stage4(config: PipelineConfig) -> None:
    """Preprocess DroidBot output for LLM analysis."""
    walk_path = config.dynamic_dir / "walk.json"
    if not walk_path.exists():
        raise FileNotFoundError(f"walk.json not found in {config.dynamic_dir}")

    walk = json.loads(walk_path.read_text(encoding="utf-8"))
    states = walk["states"]
    transitions = walk["transitions"]

    # 0. Filter out states captured OUTSIDE the target app (Android launcher,
    #    Settings, Chrome etc. that slipped through the foreground guard).
    #    Keep states whose `activity` is declared in the manifest, or whose
    #    FQN starts with the target package as a cheap fallback when manifest
    #    is missing.
    static_path = config.static_dir / "analysis.json"
    meta_path = config.apk_dir / "metadata.json"
    declared_activities: set[str] = set()
    package_name = ""
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            package_name = meta.get("package_name", "")
        except Exception:
            pass
    if static_path.exists():
        try:
            si = json.loads(static_path.read_text(encoding="utf-8"))
            declared_activities = {a.get("name", "") for a in si.get("activities", []) if a.get("name")}
        except Exception:
            pass

    if declared_activities or package_name:
        def _in_app(st: dict) -> bool:
            act = st.get("activity") or ""
            if not act:
                return True  # keep unknowns so we don't lose data
            if act in declared_activities:
                return True
            if package_name and act.startswith(package_name + "."):
                return True
            return False

        before = len(states)
        states = [s for s in states if _in_app(s)]
        dropped = before - len(states)
        if dropped:
            dropped_acts = sorted({s.get("activity","") for s in walk["states"] if not _in_app(s)})
            logger.info("Filtered %d out-of-app states (activities: %s)", dropped, dropped_acts[:5])
        kept_screen_ids = {s.get("state_str","") for s in states}
        transitions = [t for t in transitions
                       if t.get("from_screen","") in kept_screen_ids and t.get("to_screen","") in kept_screen_ids]

    # 1. Clean XML views for each state
    for state in states:
        state["cleaned_views"] = clean_views(state.get("views", []))

    # 2. Process screenshots
    process_screenshots(states, config.analysis_dir / "screens", config.screenshot_size)

    # 3. Cluster states into pages (ScreenAtlas: structure_str grouping)
    pages = cluster_screens_to_pages(states, transitions)

    # 4. Build context units for LLM
    screen_cards = build_screen_cards(pages, transitions)

    # Save results
    output_path = config.analysis_dir / "screen_cards.json"
    output_path.write_text(json.dumps(screen_cards, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info(
        "Preprocessing complete: %d states → %d pages → %d context units",
        len(states), len(pages), len(screen_cards),
    )
