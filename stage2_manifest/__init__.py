"""Stage 2: Static analysis — parse manifest, analyze layouts."""

import json
import logging
from config import PipelineConfig
from .manifest_parser import parse_manifest

logger = logging.getLogger(__name__)


def run_stage2(config: PipelineConfig) -> None:
    """Run static analysis on the APK."""
    apk_files = list(config.apk_dir.glob("*.apk"))
    if not apk_files:
        raise FileNotFoundError(f"No APK found in {config.apk_dir}")
    apk_path = str(apk_files[0])

    # Parse manifest directly from APK (no decompile needed)
    manifest_info = parse_manifest(apk_path)

    # Save results
    output_path = config.static_dir / "analysis.json"
    output_path.write_text(json.dumps(manifest_info, indent=2, ensure_ascii=False, encoding="utf-8"))
    logger.info(
        "Static analysis complete: %d activities, entry=%s",
        len(manifest_info.get("activities", [])),
        manifest_info.get("entry_activity", "?"),
    )
