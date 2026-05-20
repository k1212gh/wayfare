"""Heuristic classifier: tag every node with capture_priority ∈ {A, B, C}.

Classification rules (heuristics-first — no LLM needed):

  A — "user-facing screen". Agent likely needs screenshot + UI elements.
  B — "plumbing/trampoline/callback/proxy". Rendered briefly or not at all;
       agent never interacts here. Scan should skip.
  C — "deep-link entry" or "external SDK surface that was never captured".

Signals:
  - FQN substring: Proxy|Trampoline|Callback|Dummy|Wrapper|Hub|Invisible
                   |Handle*|*Handler|*Bridge|*Stub|*Forward
  - intent_filter with VIEW + scheme/host → C (deep-link)
  - intent_filter with LAUNCHER → A (main entry)
  - activity FQN starts with a known external-SDK package prefix
    (see external_libs.yaml) → is_external_lib=True; priority lowered to C
    when no screenshot was captured.
  - Self-correcting: if screenshot_ref is present (the user actually saw the
    screen), plumbing / external rules are bypassed and the node stays A.

Cross-node post-pass (CAT3): an activity node whose only captured child is
exactly one fragment is demoted A→B (single-fragment container is redundant
with the fragment itself; tab-hubs with 2+ children remain A).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Static patterns
# ---------------------------------------------------------------------------

# Signals in Activity FQN that strongly imply plumbing (category B/C)
_PLUMBING_PATTERNS = [
    r"Proxy", r"Trampoline", r"Callback", r"Dummy", r"Wrapper",
    r"Invisible", r"Shortcuts?", r"Handle[A-Z]", r"AfterLogin",
    r"SignIn(Hub|Entry)", r"AppWidgetConfig", r"Receiver",
    r"Redirect", r"Scheduler", r"BackgroundService",
    # Suffix forms (CAT2 extension — 2026-05-15)
    r"\w+Handler$", r"\w+Bridge$", r"\w+Stub$", r"\w+Forward$",
]
_PLUMBING_RE = re.compile("|".join(_PLUMBING_PATTERNS))

# Signals that indicate a user-facing screen
_USER_SCREEN_PATTERNS = [
    r"Activity$", r"Page$", r"Screen$", r"View$", r"Dialog$",
    r"Picker$", r"Onboarding$", r"Settings$", r"Search$", r"Detail$",
    r"Editor$", r"Home$", r"List$",
]


# ---------------------------------------------------------------------------
# External-library prefix loading (yaml-driven)
# ---------------------------------------------------------------------------

_EXT_PREFIXES_CACHE: list[tuple[str, str]] | None = None


def _load_external_lib_prefixes() -> list[tuple[str, str]]:
    """Load external-SDK package prefixes from external_libs.yaml.

    Returns list of (prefix, name) tuples. Cached after first load.
    Falls back to empty list if yaml missing or malformed (degrades to
    pre-CAT1 behavior — no external_lib flagging).
    """
    global _EXT_PREFIXES_CACHE
    if _EXT_PREFIXES_CACHE is not None:
        return _EXT_PREFIXES_CACHE

    yaml_path = Path(__file__).parent / "external_libs.yaml"
    if not yaml_path.exists():
        logger.warning("external_libs.yaml not found at %s — CAT1 disabled", yaml_path)
        _EXT_PREFIXES_CACHE = []
        return _EXT_PREFIXES_CACHE

    try:
        import yaml
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        entries = data.get("external_lib_prefixes") or []
        out: list[tuple[str, str]] = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            p = e.get("prefix")
            if not p or not isinstance(p, str):
                continue
            out.append((p, str(e.get("name", ""))))
        _EXT_PREFIXES_CACHE = out
        logger.info("Loaded %d external-lib prefixes from %s", len(out), yaml_path.name)
        return out
    except Exception as e:
        logger.warning("external_libs.yaml load failed (%s) — CAT1 disabled", e)
        _EXT_PREFIXES_CACHE = []
        return _EXT_PREFIXES_CACHE


def _match_external_lib(activity: str, host_pkg: str) -> tuple[str, str] | None:
    """Return (prefix, name) if activity matches an external-lib prefix.

    Activities under the host package are never treated as external libs,
    even if their FQN coincidentally starts with a listed prefix.
    """
    if not activity:
        return None
    if host_pkg and activity.startswith(host_pkg + "."):
        return None
    for prefix, name in _load_external_lib_prefixes():
        if activity.startswith(prefix):
            return prefix, name
    return None


# ---------------------------------------------------------------------------
# Captured-screen signal (self-correction)
# ---------------------------------------------------------------------------

def _has_captured_screen_signal(node: dict) -> bool:
    """True if the node was actually captured in dynamic walk.

    Used to bypass plumbing/external-lib demotion: if the user really saw
    the screen, classification should respect that over static guesses.
    """
    if node.get("screenshot_ref"):
        return True
    if node.get("structure_str"):
        # uihash was computed → meaningful view tree existed
        return True
    return False


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def classify_activities(screenmap: dict) -> dict[str, int]:
    """Walk ScreenMap nodes, assign capture_priority + capture_reason + flags.

    Adds these fields to each node:
      capture_priority    : "A" | "B" | "C" | "-"
      capture_reason      : short debug tag
      is_external_lib     : bool
      external_lib_source : str (prefix, empty if not external)
      is_plumbing         : bool

    Then runs a cross-node post-pass to demote redundant act parents
    (CAT3): A act_* with exactly one captured fragment child → B.

    Returns counts dict by priority.
    """
    graph = screenmap.get("screen_map", {}).get("graph", {})
    nodes = graph.get("nodes", [])
    host_pkg = screenmap.get("screen_map", {}).get("package_name", "") or ""

    counts = {"A": 0, "B": 0, "C": 0}
    for n in nodes:
        if n.get("screen_id") == "system:external_entry":
            n["capture_priority"] = "-"
            n["capture_reason"] = "system_entry"
            n.setdefault("is_external_lib", False)
            n.setdefault("external_lib_source", "")
            n.setdefault("is_plumbing", False)
            continue
        prio, reason, ext_match, is_plumb = _classify_one(n, host_pkg)
        n["capture_priority"] = prio
        n["capture_reason"] = reason
        n["is_external_lib"] = ext_match is not None
        n["external_lib_source"] = ext_match[0] if ext_match else ""
        n["is_plumbing"] = is_plumb
        counts[prio] = counts.get(prio, 0) + 1

    # Cross-node post-pass: demote single-captured-child act parents.
    demoted = _demote_redundant_act_parents(nodes)
    if demoted:
        counts["A"] -= demoted
        counts["B"] += demoted
        logger.info("CAT3 post-pass: demoted %d redundant act parents (A→B)", demoted)

    logger.info(
        "Activity classification: %s (A=user-facing, B=plumbing, C=deep-link/external)",
        counts,
    )
    return counts


# ---------------------------------------------------------------------------
# Per-node rule
# ---------------------------------------------------------------------------

def _classify_one(node: dict, host_pkg: str) -> tuple[str, str, tuple[str, str] | None, bool]:
    act = node.get("activity", "") or ""
    short = act.rsplit(".", 1)[-1] if "." in act else act
    ifs = node.get("intent_filters") or []

    ext_match = _match_external_lib(act, host_pkg)
    plumb_match = _PLUMBING_RE.search(short)
    is_plumbing = plumb_match is not None
    has_capture = _has_captured_screen_signal(node)

    # Rule 0 — Self-correction: real capture beats static guess.
    # If the screen was actually rendered, it's a user-facing screen even if
    # the FQN looks like plumbing or the package looks external.
    if has_capture:
        # Still set flags for downstream consumers, but priority stays A
        # unless an explicit deep-link rule below claims it.
        if ext_match:
            return "A", f"external_lib_captured:{ext_match[0]}", ext_match, is_plumbing
        if is_plumbing:
            return "A", f"plumbing_captured:{plumb_match.group(0)}", ext_match, is_plumbing
        # Fall through to standard rules (launcher / deeplink / pattern).

    # Rule 1 — External library + not captured → C (CAT1)
    if ext_match and not has_capture:
        # If also plumbing, mark with combined reason (double negative).
        reason = (
            f"external_lib_plumbing:{ext_match[0]}"
            if is_plumbing
            else f"external_lib:{ext_match[0]}"
        )
        return "C", reason, ext_match, is_plumbing

    # Rule 2 — Plumbing patterns in FQN short name (host package).
    if is_plumbing and not has_capture:
        return "B", f"plumbing_fqn:{plumb_match.group(0)}", ext_match, is_plumbing

    # Rule 3 — Deep-link entry: VIEW intent with scheme+host
    for f in ifs:
        if not isinstance(f, dict):
            continue
        actions = f.get("actions") or []
        if "android.intent.action.VIEW" not in actions:
            continue
        data = f.get("data") or []
        has_scheme = any(
            isinstance(d, dict) and d.get("scheme") for d in data
        )
        has_host_or_path = any(
            isinstance(d, dict) and (d.get("host") or d.get("pathPrefix") or d.get("pathPattern"))
            for d in data
        )
        if has_scheme and has_host_or_path:
            return "C", "deeplink_view", ext_match, is_plumbing

    # Rule 4 — Launcher → always A (main entry)
    for f in ifs:
        if not isinstance(f, dict):
            continue
        cats = f.get("categories") or []
        if "android.intent.category.LAUNCHER" in cats:
            return "A", "launcher", ext_match, is_plumbing

    has_any_intent = len(ifs) > 0

    # Rule 5 — Default: treat as A (user screen) unless very short FQN
    if any(re.search(p, short) for p in _USER_SCREEN_PATTERNS):
        return "A", "user_screen_pattern", ext_match, is_plumbing

    # Conservative default
    if has_any_intent:
        return "A", "intent_registered", ext_match, is_plumbing
    return "A", "default_a", ext_match, is_plumbing


# ---------------------------------------------------------------------------
# Cross-node post-pass (CAT3)
# ---------------------------------------------------------------------------

def _demote_redundant_act_parents(nodes: list[dict]) -> int:
    """Demote act_* nodes whose only captured child is a single fragment.

    Rationale: when a SettingsActivity has exactly one captured fragment
    (e.g. prefs_fragment), the activity-level node is a redundant container.
    Tab/pager hubs with 2+ captured children stay A. Act nodes that were
    themselves captured (screenshot_ref set) stay A.

    Returns count of demoted nodes. Modifies nodes in place.
    """
    captured_children_by_act: dict[str, list[dict]] = {}
    for n in nodes:
        if n.get("node_type") != "fragment":
            continue
        if not n.get("screenshot_ref"):
            continue
        pid = n.get("parent_activity_id") or ""
        if not pid:
            continue
        captured_children_by_act.setdefault(pid, []).append(n)

    demoted = 0
    for n in nodes:
        if n.get("node_type") != "activity":
            continue
        if n.get("capture_priority") != "A":
            continue
        if n.get("screenshot_ref"):
            # act itself was captured — keep A
            continue
        kids = captured_children_by_act.get(n.get("screen_id", ""), [])
        if len(kids) == 1:
            n["capture_priority"] = "B"
            base = n.get("capture_reason") or ""
            n["capture_reason"] = (
                base + "|demoted_single_captured_child"
                if base
                else "demoted_single_captured_child"
            )
            demoted += 1
    return demoted
