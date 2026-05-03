"""Stage 4: Data preprocessing — XML cleaning, state clustering, context building."""

import json
import logging
from config import PipelineConfig
from .view_tree_cleaner import clean_views
from .screenshot_processor import process_screenshots
from .screen_clusterer import cluster_screens_to_pages
from .screen_card_builder import build_screen_cards
from .widget_classifier import annotate_roles

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

    # 1b. Role annotation (sprint 2026-04-27) — widget_classifier tags every
    # view with a UX role (button/radio/checkbox/stepper/dropdown/...). Used
    # by Stage 5 chip_group_detector for sibling-pattern grouping.
    role_count = 0
    for state in states:
        for views_key in ("cleaned_views", "views"):
            vs = state.get(views_key) or []
            if vs:
                role_count += annotate_roles(vs)
    if role_count:
        logger.info("Annotated roles on %d views", role_count)

    # 2. Process screenshots
    process_screenshots(states, config.analysis_dir / "screens", config.screenshot_size)

    # 3. Cluster states into pages (ScreenAtlas: structure_str grouping)
    pages = cluster_screens_to_pages(states, transitions)

    # 4. Build context units for LLM
    screen_cards = build_screen_cards(pages, transitions)

    # 4b. Option-group detection (sprint 2026-04-27). Heuristic by default;
    # LLM Vision naming opt-in via OPTION_DETECT_LLM=1. Attaches chip_groups
    # list to each context unit so screenmap_builder picks it up onto ScreenMap nodes.
    try:
        from stage5_annotate.chip_group_detector import detect_chip_groups
        # Index states by structure_str so we can find the views per unit.
        state_by_struct: dict[str, dict] = {}
        for s in states:
            sk = s.get("structure_str") or ""
            if sk and sk not in state_by_struct:
                state_by_struct[sk] = s
        attached_units = 0
        attached_groups = 0
        provisional_marked = 0
        for unit in screen_cards:
            page_state = state_by_struct.get(unit.get("structure_str") or "")
            if not page_state:
                continue
            views = page_state.get("cleaned_views") or page_state.get("views") or []
            screenshot = (unit.get("screenshot")
                          or page_state.get("screenshot_path") or "")
            groups = detect_chip_groups(views, screenshot_path=screenshot or None)
            if groups:
                unit["chip_groups"] = groups
                attached_units += 1
                attached_groups += len(groups)
            # 2026-04-30: Stage 3 의 provisional 마킹 (state.needs_vision_in_revisit)
            # 을 unit 으로 전파. screenmap_builder 가 node.is_provisional 로 매핑.
            # Pass 2 walker 가 이 노드 들 entry 로 재탐색.
            if page_state.get("needs_vision_in_revisit"):
                unit["needs_vision_in_revisit"] = True
                provisional_marked += 1
        if provisional_marked:
            logger.info(
                "[provisional] %d units marked for Pass 2 vision fallback",
                provisional_marked,
            )
        if attached_groups:
            logger.info("Detected %d option groups across %d pages",
                        attached_groups, attached_units)
    except Exception as e:
        logger.warning("Option group detection failed: %s", e)

    # Save results
    output_path = config.analysis_dir / "screen_cards.json"
    output_path.write_text(json.dumps(screen_cards, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info(
        "Preprocessing complete: %d states → %d pages → %d context units",
        len(states), len(pages), len(screen_cards),
    )
