"""Stage 5: LLM analysis — screen purpose, element roles, subflow derivation."""

import json
import logging
from config import PipelineConfig
from .llm_client import create_client
from .screen_analyzer import analyze_screens
from .subflow_analyzer import derive_subflows

logger = logging.getLogger(__name__)


def run_stage5(config: PipelineConfig) -> None:
    """Run LLM analysis on context units."""
    cu_path = config.analysis_dir / "screen_cards.json"
    if not cu_path.exists():
        raise FileNotFoundError(f"screen_cards.json not found in {config.analysis_dir}")

    screen_cards = json.loads(cu_path.read_text(encoding="utf-8"))

    # Load metadata
    meta_path = config.apk_dir / "metadata.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    # Initialize LLM client (auto-detects CLI vs API from .env)
    client = create_client(
        api_key=config.anthropic_api_key,
        model_screen=config.llm_model_screen,
        model_widget=config.llm_model_widget,
        temperature=config.llm_temperature,
        max_retries=config.llm_max_retries,
    )

    # 1. Analyze each screen
    screen_analyses = analyze_screens(client, screen_cards, metadata)
    sa_path = config.analysis_dir / "screen_analyses.json"
    sa_path.write_text(json.dumps(screen_analyses, indent=2, ensure_ascii=False), encoding="utf-8")

    # 2. Derive subflows
    subflows = derive_subflows(client, screen_analyses, screen_cards, metadata)
    sg_path = config.analysis_dir / "subflows.json"
    sg_path.write_text(json.dumps(subflows, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info(
        "LLM analysis complete: %d screens analyzed, %d subflows derived",
        len(screen_analyses), len(subflows),
    )
