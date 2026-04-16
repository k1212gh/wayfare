"""Parse AndroidManifest.xml — supports both decoded XML and binary APK via androguard."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_manifest(apk_or_manifest_path: str) -> dict:
    """Parse manifest from APK file or decoded XML.

    Automatically detects input type:
      - .apk → uses androguard to parse binary manifest directly
      - .xml → uses xml.etree for decoded (text) manifest
    """
    path = Path(apk_or_manifest_path)

    if path.suffix.lower() == ".apk":
        return _parse_with_androguard(str(path))

    # Fallback: try as text XML
    try:
        return _parse_with_etree(str(path))
    except Exception:
        logger.warning("Text XML parse failed, trying androguard on parent APK")
        # Find APK in parent directory
        for apk in path.parent.parent.glob("**/*.apk"):
            return _parse_with_androguard(str(apk))
        raise


def _parse_with_androguard(apk_path: str) -> dict:
    """Parse manifest directly from APK using androguard (handles binary XML)."""
    from androguard.core.apk import APK

    apk = APK(apk_path)
    package_name = apk.get_package() or ""

    # Activities
    activities = []
    main_activity = apk.get_main_activity() or ""

    for act_name in apk.get_activities():
        full_name = _resolve_name(act_name, package_name)
        is_launcher = (full_name == _resolve_name(main_activity, package_name))

        activities.append({
            "name": full_name,
            "short_name": act_name,
            "is_launcher": is_launcher,
            "intent_filters": [],
            "exported": "",
        })

    # Services, receivers, providers
    services = [_resolve_name(s, package_name) for s in apk.get_services()]
    receivers = [_resolve_name(r, package_name) for r in apk.get_receivers()]
    providers = [_resolve_name(p, package_name) for p in apk.get_providers()]

    # Permissions
    permissions = list(apk.get_permissions())

    entry_activity = _resolve_name(main_activity, package_name) if main_activity else ""
    if not entry_activity and activities:
        entry_activity = activities[0]["name"]

    logger.info(
        "Parsed manifest via androguard: %s, %d activities, entry=%s",
        package_name, len(activities), entry_activity,
    )

    return {
        "package_name": package_name,
        "activities": activities,
        "entry_activity": entry_activity,
        "services": services,
        "receivers": receivers,
        "providers": providers,
        "permissions": permissions,
    }


def _parse_with_etree(manifest_path: str) -> dict:
    """Parse decoded (text) AndroidManifest.xml with xml.etree."""
    from xml.etree import ElementTree as ET

    ANDROID_NS = "http://schemas.android.com/apk/res/android"

    tree = ET.parse(manifest_path)
    root = tree.getroot()
    package_name = root.attrib.get("package", "")

    activities = []
    entry_activity = ""
    app = root.find("application")

    if app is not None:
        for act in app.findall("activity"):
            name = act.attrib.get(f"{{{ANDROID_NS}}}name", "")
            full_name = _resolve_name(name, package_name)

            is_launcher = False
            for if_elem in act.findall("intent-filter"):
                actions = [a.attrib.get(f"{{{ANDROID_NS}}}name", "") for a in if_elem.findall("action")]
                categories = [c.attrib.get(f"{{{ANDROID_NS}}}name", "") for c in if_elem.findall("category")]
                if "android.intent.action.MAIN" in actions and "android.intent.category.LAUNCHER" in categories:
                    is_launcher = True
                    entry_activity = full_name

            activities.append({
                "name": full_name,
                "short_name": name,
                "is_launcher": is_launcher,
                "intent_filters": [],
                "exported": act.attrib.get(f"{{{ANDROID_NS}}}exported", ""),
            })

    if not entry_activity and activities:
        entry_activity = activities[0]["name"]

    services = _parse_components(app, "service", package_name, ANDROID_NS)
    receivers = _parse_components(app, "receiver", package_name, ANDROID_NS)
    providers = _parse_components(app, "provider", package_name, ANDROID_NS)

    return {
        "package_name": package_name,
        "activities": activities,
        "entry_activity": entry_activity,
        "services": services,
        "receivers": receivers,
        "providers": providers,
        "permissions": [],
    }


def _parse_components(app, tag, package, ns):
    if app is None:
        return []
    return [
        _resolve_name(c.attrib.get(f"{{{ns}}}name", ""), package)
        for c in app.findall(tag)
    ]


def _resolve_name(name: str, package: str) -> str:
    if not name:
        return ""
    if name.startswith("."):
        return package + name
    if "." not in name:
        return package + "." + name
    return name
