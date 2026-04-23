"""Stage 2: Static analysis — parse manifest + DEX bytecode transitions."""

import json
import logging
import os
from pathlib import Path

from config import PipelineConfig
from .manifest_parser import parse_manifest

logger = logging.getLogger(__name__)


def run_stage2(config: PipelineConfig) -> None:
    """Run static analysis on the APK (manifest + static transitions)."""
    apk_files = list(Path(config.apk_dir).glob("*.apk"))
    if not apk_files:
        raise FileNotFoundError(f"No APK found in {config.apk_dir}")
    # Prefer base.apk for split APKs
    base_apk = next((a for a in apk_files if a.name == "base.apk"), apk_files[0])

    # 1. Manifest
    manifest_info = parse_manifest(str(base_apk))
    output_path = Path(config.static_dir) / "analysis.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest_info, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "Manifest analyzed: %d activities, entry=%s",
        len(manifest_info.get("activities", [])),
        manifest_info.get("entry_activity", "?"),
    )

    # 2. Static transitions via DEX bytecode (dexdump)
    # Skipped if env var set, or if dexdump missing (logged but not fatal)
    if os.environ.get("SKIP_DEX_TRANSITIONS", "").lower() not in ("1", "true"):
        try:
            from .dex_transitions import write_transition_graph
            pkg = manifest_info.get("package_name", "")
            # Limit dex count for very large APKs (env tunable)
            max_dex = int(os.environ.get("DEX_MAX_FILES", "10"))
            known_acts = {a.get("name", "") for a in manifest_info.get("activities", []) if a.get("name")}
            write_transition_graph(
                apk_path=base_apk,
                output_path=Path(config.static_dir) / "transition_graph.json",
                package_filter="",
                max_dex=max_dex,
                known_activities=known_acts,
            )
        except RuntimeError as e:
            logger.warning("DEX transition extraction skipped: %s", e)
        except Exception as e:
            logger.exception("DEX transition extraction failed: %s", e)
