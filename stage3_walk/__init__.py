"""Stage 3: Dynamic walk — TapWalker or DroidBot."""

import json
import logging
import os
from config import PipelineConfig
from .walker_dispatcher import run_droidbot
from .activity_coverage import check_coverage

logger = logging.getLogger(__name__)


def run_stage3(config: PipelineConfig) -> None:
    """Run app walk and save results."""
    apk_files = list(config.apk_dir.glob("*.apk"))
    if not apk_files:
        raise FileNotFoundError(f"No APK found in {config.apk_dir}")
    apk_path = str(apk_files[0])

    # Load static analysis for coverage tracking
    static_path = config.static_dir / "analysis.json"
    static_info = {}
    if static_path.exists():
        static_info = json.loads(static_path.read_text(encoding="utf-8"))

    # Load framework from metadata (detected in Stage 1)
    meta_path = config.apk_dir / "metadata.json"
    framework = "xml"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        framework = meta.get("framework", "xml")
        logger.info("Using framework=%s for walk", framework)

    # Run walk (TapWalker or DroidBot, based on WALK_MODE)
    run_droidbot(
        apk_path=apk_path,
        device_serial=config.device_serial,
        output_dir=str(config.dynamic_dir),
        timeout=config.droidbot_timeout,
        policy=config.droidbot_policy,
        is_emulator=config.is_emulator,
        framework=framework,
    )

    # TapWalker writes walk.json directly.
    # DroidBot needs utg_parser to convert its output.
    walk_path = config.dynamic_dir / "walk.json"

    if walk_path.exists():
        walk = json.loads(walk_path.read_text(encoding="utf-8"))
    else:
        # DroidBot mode — parse its output format
        from .utg_parser import parse_droidbot_output
        walk = parse_droidbot_output(str(config.dynamic_dir))

    # Check coverage
    static_activities = [a["name"] for a in static_info.get("activities", [])]
    coverage = check_coverage(walk, static_activities)
    walk["coverage"] = coverage

    # Save final walk data
    walk_path.write_text(
        json.dumps(walk, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    logger.info(
        "Walk: %d states, %d transitions, %.0f%% coverage",
        len(walk.get("states", [])),
        len(walk.get("transitions", [])),
        coverage.get("ratio", 0) * 100,
    )
