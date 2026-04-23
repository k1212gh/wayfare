"""Stage 5: LLM analysis.

Two modes:
  - screenmap_annotate (default): reads wireframe ScreenMap from stage6, annotates in place.
                           Token-efficient (batch + prompt caching).
  - legacy: per-screen analysis + subflow derivation. Kept for back-compat.
"""

import json
import logging
from config import PipelineConfig
from .llm_client import create_client

logger = logging.getLogger(__name__)


def run_stage5(config: PipelineConfig, mode: str = "screenmap_annotate") -> None:
    """Run LLM stage.

    Args:
        config: pipeline config
        mode: "screenmap_annotate" (default) or "legacy"
    """
    if mode == "screenmap_annotate":
        # Vision-first: use screenshots to label screens when available. This
        # is far more accurate than text-only annotation for Compose/WebView/RN
        # screens where XML is semantically bare.
        try:
            from .vision_labeler import label_screens_with_vision
            label_screens_with_vision(config)
        except Exception as e:
            logger.warning("Vision labeler failed, falling back to text annotator: %s", e)
        # Text-based annotator fills gaps (nodes w/o screenshot, still 'other'
        # after vision). Uses the same prompt-cacheable graph context.
        from .screenmap_annotator import annotate_screenmap
        annotate_screenmap(config)
        return

    if mode == "vision_only":
        from .vision_labeler import label_screens_with_vision
        label_screens_with_vision(config)
        return

    if mode == "legacy":
        _run_legacy(config)
        return

    raise ValueError(f"Unknown stage5 mode: {mode!r} (use 'screenmap_annotate', 'vision_only', or 'legacy')")


def _run_legacy(config: PipelineConfig) -> None:
    """Original per-screen LLM analysis. Requires stage6 to NOT have run yet."""
    from .screen_analyzer import analyze_screens
    from .subflow_analyzer import derive_subflows

    cu_path = config.analysis_dir / "screen_cards.json"
    if not cu_path.exists():
        raise FileNotFoundError(f"screen_cards.json not found in {config.analysis_dir}")

    screen_cards = json.loads(cu_path.read_text(encoding="utf-8"))
    meta_path = config.apk_dir / "metadata.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    client = create_client(
        api_key=config.anthropic_api_key,
        model_screen=config.llm_model_screen,
        model_widget=config.llm_model_widget,
        temperature=config.llm_temperature,
        max_retries=config.llm_max_retries,
    )

    screen_analyses = analyze_screens(client, screen_cards, metadata)
    (config.analysis_dir / "screen_analyses.json").write_text(
        json.dumps(screen_analyses, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    subflows = derive_subflows(client, screen_analyses, screen_cards, metadata)
    (config.analysis_dir / "subflows.json").write_text(
        json.dumps(subflows, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    logger.info(
        "Legacy LLM analysis complete: %d screens, %d subflows",
        len(screen_analyses), len(subflows),
    )
