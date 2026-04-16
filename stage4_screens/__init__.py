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
    output_path.write_text(json.dumps(screen_cards, indent=2, ensure_ascii=False, encoding="utf-8"))

    logger.info(
        "Preprocessing complete: %d states → %d pages → %d context units",
        len(states), len(pages), len(screen_cards),
    )
