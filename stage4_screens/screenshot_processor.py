"""Screenshot preprocessing — resize, crop, format conversion."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def process_screenshots(
    states: list[dict],
    output_dir: Path,
    target_size: tuple[int, int] = (720, 1280),
    quality: int = 85,
) -> None:
    """Process screenshots from DroidBot output.

    - Resize to target resolution
    - Convert PNG → JPEG
    - Update state dicts with processed screenshot paths
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow not installed, skipping screenshot processing")
        return

    processed = 0
    for state in states:
        src = state.get("screenshot_path", "")
        if not src or not Path(src).exists():
            continue

        try:
            img = Image.open(src)
            # JPEG does not support alpha. Flatten RGBA/LA/P onto white.
            if img.mode in ("RGBA", "LA", "P"):
                rgba = img.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[-1])
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")

            img = img.resize(target_size, Image.LANCZOS)

            screen_id = state.get("state_str", "unknown")[:16]
            dest = output_dir / f"{screen_id}.jpg"
            img.save(str(dest), "JPEG", quality=quality)

            state["processed_screenshot"] = str(dest)
            processed += 1
        except Exception as e:
            logger.warning("Failed to process screenshot %s: %s", src, e)

    logger.info("Processed %d screenshots", processed)
