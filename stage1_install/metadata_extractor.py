"""Extract APK metadata using aapt2."""

import re
import shutil
import subprocess
import logging

logger = logging.getLogger(__name__)


def extract_metadata(apk_path: str) -> dict:
    """Extract package name, version, and permissions from APK.

    Uses aapt2 if available, falls back to androguard.
    """
    if shutil.which("aapt2"):
        return _extract_with_aapt2(apk_path)
    try:
        return _extract_with_androguard(apk_path)
    except ImportError:
        raise RuntimeError(
            "Neither aapt2 nor androguard is available. "
            "Install Android SDK (aapt2) or run: pip install androguard"
        )


def _extract_with_aapt2(apk_path: str) -> dict:
    result = subprocess.run(
        ["aapt2", "dump", "badging", apk_path],
        capture_output=True, text=True, timeout=30,
    )
    output = result.stdout

    metadata: dict = {"source": "aapt2"}

    # package name
    m = re.search(r"package:\s+name='([^']+)'", output)
    metadata["package_name"] = m.group(1) if m else "unknown"

    # version
    m = re.search(r"versionCode='(\d+)'", output)
    metadata["version_code"] = m.group(1) if m else ""
    m = re.search(r"versionName='([^']+)'", output)
    metadata["version_name"] = m.group(1) if m else ""

    # permissions
    permissions = re.findall(r"uses-permission:\s+name='([^']+)'", output)
    metadata["permissions"] = permissions

    # launchable activity
    m = re.search(r"launchable-activity:\s+name='([^']+)'", output)
    metadata["launch_activity"] = m.group(1) if m else ""

    logger.info("Extracted metadata for %s", metadata["package_name"])
    return metadata


def _extract_with_androguard(apk_path: str) -> dict:
    from androguard.core.apk import APK

    apk = APK(apk_path)
    return {
        "source": "androguard",
        "package_name": apk.get_package(),
        "version_code": apk.get_androidversion_code() or "",
        "version_name": apk.get_androidversion_name() or "",
        "permissions": apk.get_permissions(),
        "launch_activity": apk.get_main_activity() or "",
    }
