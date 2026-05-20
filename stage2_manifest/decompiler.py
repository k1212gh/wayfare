"""APK decompilation using apktool.

STATUS (2026-05-14): UNUSED — zero callers in the current pipeline.

Originally intended to produce res/, smali/, decoded AndroidManifest.xml for
downstream modules. No longer needed because:
  - Manifest:   androguard decodes binary AXML in-memory (manifest_parser.py)
  - DEX:        dexdump parses .dex directly (dex_transitions.py) — ~10x faster
  - res/layout: only consumer was layout_analyzer.py (sibling, also unused)

Removal candidate. If revived, pair with layout_analyzer.py and add a
setContentView DEX pattern for activity↔layout binding (see that file).
"""

import shutil
import subprocess
import logging

logger = logging.getLogger(__name__)


def decompile_apk(apk_path: str, output_dir: str) -> None:
    """Decompile APK using apktool.

    Produces: AndroidManifest.xml, res/, smali/
    Falls back to simple unzip if apktool is unavailable.
    """
    if shutil.which("apktool"):
        _decompile_with_apktool(apk_path, output_dir)
    else:
        logger.warning("apktool not found, falling back to ZIP extraction")
        _extract_as_zip(apk_path, output_dir)


def _decompile_with_apktool(apk_path: str, output_dir: str) -> None:
    cmd = ["apktool", "d", apk_path, "-o", output_dir, "-f"]
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"apktool failed: {result.stderr[:500]}")
    logger.info("Decompiled to %s", output_dir)


def _extract_as_zip(apk_path: str, output_dir: str) -> None:
    """Fallback: extract APK as ZIP (limited — no smali, encoded XML)."""
    import zipfile
    from pathlib import Path

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(apk_path, "r") as zf:
        zf.extractall(output_dir)
    logger.info("Extracted APK as ZIP to %s", output_dir)
