"""Stage 5: LLM analysis.

Two modes:
  - screenmap_annotate (default): reads wireframe ScreenMap from stage6, annotates in place.
                           Token-efficient (batch + prompt caching).
  - legacy: per-screen analysis + subflow derivation. Kept for back-compat.
"""

import json
import os
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

    if mode == "grounded":
        # 2026-09-12: 후보 선택형 라벨링 — 화면에 보이는 텍스트 중 하나를 고른다.
        # 텍스트만 쓰므로 싸고, 작은 로컬 모델에서도 안정적. LLM_STAGE5_VISION=1 이면
        # 스크린샷 라벨러를 추가로 돌린다 (로컬 비전 모델 있을 때).
        from .label_picker import pick_labels
        pick_labels(config)
        if os.environ.get("LLM_STAGE5_VISION", "").lower() in ("1", "true", "yes"):
            try:
                from .vision_labeler import label_screens_with_vision
                label_screens_with_vision(config)
            except Exception as e:
                logger.warning("Vision labeler failed (grounded mode continues): %s", e)
        return

    if mode == "vision_only":
        from .vision_labeler import label_screens_with_vision
        label_screens_with_vision(config)
        return

    if mode == "legacy":
        _run_legacy(config)
        return

    raise ValueError(f"Unknown stage5 mode: {mode!r} (use 'screenmap_annotate', 'grounded', 'vision_only', or 'legacy')")


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
