"""Stage 5: LLM analysis.

Modes:
  - vision_name (local default): 스크린샷을 보고 이름을 짓고 화면 텍스트 후보에 스냅 + 텍스트 피커로 나머지 채움.
  - grounded: 텍스트 후보 선택만 (비전 모델 없을 때).
  - screenmap_annotate (Claude API default): 그래프 전체 컨텍스트로 7개 필드 생성.
  - vision_only / legacy: 이전 경로.
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

    if mode == "vision_name":
        # 2026-09-13: 스크린샷 자유 생성 + 후보 스냅 (docs/labeling_method_comparison.md — 텍스트 후보 선택 62~65%
        # vs 스크린샷 자유 생성 74~82%). 스크린샷 없는 노드는 텍스트 피커가, purpose/description 은 (옵션) 비전 라벨러가 채운다.
        from .vision_namer import name_screens_with_vision
        from .label_picker import pick_labels
        try:
            name_screens_with_vision(config)
        except Exception as e:  # noqa: BLE001
            logger.warning("Vision namer failed (falling back to text picker): %s", e)
        pick_labels(config)
        if os.environ.get("LLM_STAGE5_VISION", "").lower() in ("1", "true", "yes"):
            try:
                from .vision_labeler import label_screens_with_vision
                label_screens_with_vision(config)
            except Exception as e:
                logger.warning("Vision labeler failed (vision_name mode continues): %s", e)
        return

    if mode == "vision_only":
        from .vision_labeler import label_screens_with_vision
        label_screens_with_vision(config)
        return

    if mode == "legacy":
        _run_legacy(config)
        return

    raise ValueError(f"Unknown stage5 mode: {mode!r} (use 'vision_name', 'grounded', 'screenmap_annotate', 'vision_only', or 'legacy')")


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
