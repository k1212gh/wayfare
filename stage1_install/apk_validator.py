"""APK file validation — extension, magic bytes, ZIP integrity."""

import zipfile
from pathlib import Path


class APKValidationError(Exception):
    pass


def validate_apk(apk_path: Path) -> None:
    """Validate that the given path is a valid APK file.

    Checks:
    1. File exists
    2. Extension is .apk
    3. Magic bytes match PK (ZIP format)
    4. ZIP integrity
    """
    if not apk_path.exists():
        raise APKValidationError(f"File not found: {apk_path}")

    if apk_path.suffix.lower() != ".apk":
        raise APKValidationError(f"Not an APK file (extension: {apk_path.suffix})")

    # Magic bytes check — ZIP starts with PK (0x504B)
    with open(apk_path, "rb") as f:
        magic = f.read(2)
    if magic != b"PK":
        raise APKValidationError("Invalid APK: not a ZIP/PK format")

    # ZIP integrity
    try:
        with zipfile.ZipFile(apk_path, "r") as zf:
            bad = zf.testzip()
            if bad is not None:
                raise APKValidationError(f"Corrupted APK: bad file inside ZIP — {bad}")
    except zipfile.BadZipFile as e:
        raise APKValidationError(f"Corrupted APK: {e}") from e
