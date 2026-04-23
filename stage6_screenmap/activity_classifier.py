"""Heuristic classifier: tag every node with capture_priority ∈ {A, B, C}.

Classification rules (heuristics-first — no LLM needed):

  A — "user-facing screen". Agent likely needs screenshot + UI elements.
  B — "plumbing/trampoline/callback/proxy". Rendered briefly or not at all;
       agent never interacts here. Scan should skip.
  C — "deep-link entry". Reachable via URI only; intent_filter suffices.

Signals:
  - FQN substring: Proxy|Trampoline|Callback|Dummy|Wrapper|Hub|Invisible|Handle*
  - intent_filter with VIEW + scheme/host → C
  - intent_filter with LAUNCHER → A (main entry)
  - no intent_filter and no plumbing substring → A (default user screen)

The output is attached to each node as `capture_priority` (str) and
`capture_reason` (short debug tag). Scan uses these to pick retry budget.

LLM enhancement (optional, runs if API key valid): after heuristic pass,
Claude re-evaluates borderline cases (activities with unclear FQN) using
the screenshot of sibling same-package activities for visual evidence.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Signals in Activity FQN that strongly imply plumbing (category B)
_PLUMBING_PATTERNS = [
    r"Proxy", r"Trampoline", r"Callback", r"Dummy", r"Wrapper",
    r"Invisible", r"Shortcuts?", r"Handle[A-Z]", r"AfterLogin",
    r"SignIn(Hub|Entry)", r"AppWidgetConfig", r"Receiver",
    r"Redirect", r"Scheduler", r"BackgroundService",
]
_PLUMBING_RE = re.compile("|".join(_PLUMBING_PATTERNS))

# Signals that indicate a user-facing screen
_USER_SCREEN_PATTERNS = [
    r"Activity$", r"Page$", r"Screen$", r"View$", r"Dialog$",
    r"Picker$", r"Onboarding$", r"Settings$", r"Search$", r"Detail$",
    r"Editor$", r"Home$", r"List$",
]


def classify_activities(screenmap: dict) -> dict[str, int]:
    """Walk ScreenMap nodes, assign capture_priority + capture_reason.

    Returns count dict by priority. Modifies `screenmap` in place.
    """
    graph = screenmap.get("screen_map", {}).get("graph", {})
    nodes = graph.get("nodes", [])

    counts = {"A": 0, "B": 0, "C": 0}
    for n in nodes:
        if n.get("screen_id") == "system:external_entry":
            n["capture_priority"] = "-"
            n["capture_reason"] = "system_entry"
            continue
        prio, reason = _classify_one(n)
        n["capture_priority"] = prio
        n["capture_reason"] = reason
        counts[prio] = counts.get(prio, 0) + 1

    logger.info("Activity classification: %s (A=user-facing, B=plumbing, C=deep-link)", counts)
    return counts


def _classify_one(node: dict) -> tuple[str, str]:
    act = node.get("activity", "") or ""
    short = act.rsplit(".", 1)[-1] if "." in act else act
    ifs = node.get("intent_filters") or []

    # Rule 1 — Plumbing patterns in FQN short name
    if _PLUMBING_RE.search(short):
        return "B", f"plumbing_fqn:{_PLUMBING_RE.search(short).group(0)}"

    # Rule 2 — Deep-link entry: VIEW intent with scheme+host
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
            return "C", "deeplink_view"

    # Rule 3 — Launcher → always A (main entry)
    for f in ifs:
        if not isinstance(f, dict):
            continue
        cats = f.get("categories") or []
        if "android.intent.category.LAUNCHER" in cats:
            return "A", "launcher"

    # Rule 4 — Has UI-intent filter (MAIN, DEFAULT, BROWSABLE without scheme)?
    has_any_intent = len(ifs) > 0

    # Rule 5 — Default: treat as A (user screen) unless very short FQN
    if any(re.search(p, short) for p in _USER_SCREEN_PATTERNS):
        return "A", "user_screen_pattern"

    # Conservative default
    if has_any_intent:
        return "A", "intent_registered"
    return "A", "default_a"
