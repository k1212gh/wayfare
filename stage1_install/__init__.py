"""Stage 1: APK preprocessing — validation and metadata extraction."""

from config import PipelineConfig
from .apk_validator import validate_apk
from .metadata_extractor import extract_metadata


def run_stage1(config: PipelineConfig) -> None:
    """Validate APK and extract metadata."""
    import json
    import shutil
    from pathlib import Path

    apk_path = Path(config.apk_path)
    validate_apk(apk_path)

    # Copy APK to workspace (skip if already there)
    dest = config.apk_dir / apk_path.name
    if apk_path.resolve() != dest.resolve() and not dest.exists():
        shutil.copy2(apk_path, dest)

    # Use whichever path exists for metadata extraction
    target = dest if dest.exists() else apk_path

    # Extract metadata
    metadata = extract_metadata(str(target))
    meta_path = config.apk_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
