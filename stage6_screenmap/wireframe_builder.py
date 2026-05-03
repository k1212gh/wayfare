"""Static-analysis-only ScreenMap wireframe builder.

Runs immediately after Stage 2 (before dynamic walk) so the dashboard
has a graph to render right away. Later stages (4/6) enrich this wireframe
rather than replace it.

Node lifecycle:
  declared  (here, static-only)
     ↓  walk reaches this screen
  visited   (TapWalker sets this)
     ↓  screen_card has screenshot + elements
  enriched  (Stage 4/6 context unit present)
     ↓  LLM annotates
  annotated (Stage 5 sets functional_category + purpose)
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# System/launcher packages that should be treated as out-of-scope
SYSTEM_PACKAGES = (
    "com.google.android.packageinstaller",
    "com.android.packageinstaller",
    "com.google.android.apps.nexuslauncher",
    "com.android.launcher",
    "com.sec.android.app.launcher",
    "com.android.systemui",
    "android.settings",
    "com.android.settings",
    "com.android.permissioncontroller",
)


def _is_system_activity(activity_fqn: str) -> bool:
    return any(activity_fqn.startswith(p) for p in SYSTEM_PACKAGES)


def _short(fqn: str) -> str:
    return fqn.rsplit(".", 1)[-1] if "." in fqn else fqn


def _node_id(activity_fqn: str) -> str:
    """Stable node id used both by wireframe and enrichment phases."""
    h = hashlib.sha256(activity_fqn.encode("utf-8")).hexdigest()[:12]
    return f"act_{h}"


def build_wireframe_screenmap(static_info: dict, metadata: dict, transition_info: dict | None = None) -> dict:
    """Construct an screen_map.json-compatible dict from static analysis alone.

    Args:
        static_info: parsed `static/analysis.json` (activities, entry_activity, ...)
        metadata:    parsed `apk/metadata.json` (package_name, version, framework, ...)
        transition_info: optional parsed `static/transition_graph.json` from DEX
                         bytecode analysis. When present, produces real inter-activity
                         edges instead of just launcher/intent-filter.

    Returns:
        dict matching the ScreenMap schema used by dashboard; callers can write it to disk.
    """
    activities = list(static_info.get("activities", []) or [])
    entry_fqn = static_info.get("entry_activity", "") or ""
    pkg = metadata.get("package_name", "") or ""

    # Handle <activity-alias> case: entry_activity may not appear in activities list
    # because androguard's get_activities() skips aliases. Add it synthetically.
    known_names = {a.get("name", "") for a in activities}
    if entry_fqn and entry_fqn not in known_names:
        activities.append({
            "name": entry_fqn,
            "short_name": entry_fqn,
            "is_launcher": True,
            "intent_filters": [{"actions": ["android.intent.action.MAIN"]}],
            "exported": True,
            "_alias": True,
        })

    # Ensure the entry activity is marked as launcher even if it's in the list
    # without the flag (some manifest parsers miss this).
    for a in activities:
        if a.get("name") == entry_fqn:
            a["is_launcher"] = True
            if not a.get("intent_filters"):
                a["intent_filters"] = [{"actions": ["android.intent.action.MAIN"]}]

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_node_ids: set[str] = set()

    # External entry virtual node — will be the "from" for launcher + intent-filter edges
    EXTERNAL = "system:external_entry"

    for a in activities:
        fqn = a.get("name", "") or ""
        if not fqn:
            continue
        short = _short(fqn)
        sid = _node_id(fqn)
        if sid in seen_node_ids:
            continue
        seen_node_ids.add(sid)

        is_launcher = bool(a.get("is_launcher", False)) or fqn == entry_fqn
        is_system = _is_system_activity(fqn)

        nodes.append({
            "screen_id": sid,
            "activity": fqn,
            "label": short,
            "functional_category": "other",
            "screen_purpose": "",
            "params": {"inputs": [], "outputs": [], "displays": []},
            "widgets": [],
            # ScreenMap expressivity extensions (sprint 2026-04-27) — empty for wireframe
            # nodes; populated later if/when the activity gets dynamic content.
            "chip_groups": [],
            "state_variables": [],
            "infinite_scroll": False,
            "scroll_metadata": {},
            "screenshot_ref": "",
            "structure_str": "",
            "confidence": "low",
            # Lifecycle — starts as declared for every activity
            "status": "declared",
            "is_launcher": is_launcher,
            "is_system": is_system,
            "intent_filters": a.get("intent_filters", []) or [],
            # Hierarchy: all wireframe stubs are Activity hosts
            "node_type": "activity",
        })

    # Add the external entry virtual node, only if any activity has an intent-filter
    has_any_filter = any(n.get("intent_filters") or n.get("is_launcher") for n in nodes)
    if has_any_filter:
        nodes.append({
            "screen_id": EXTERNAL,
            "activity": "",
            "label": "External",
            "functional_category": "entry",
            "screen_purpose": "System/external app entry points (launcher, deep links, widgets)",
            "params": {"inputs": [], "outputs": [], "displays": []},
            "widgets": [],
            "screenshot_ref": "",
            "structure_str": "",
            "confidence": "high",
            "status": "entry",
            "is_launcher": False,
            "is_system": False,
            "node_type": "system",
        })
        # Launcher edge
        for n in nodes:
            if n["screen_id"] == EXTERNAL:
                continue
            sid = n["screen_id"]
            if n.get("is_launcher"):
                edges.append(_make_edge(EXTERNAL, sid, "launcher", "LAUNCHER"))
            elif n.get("intent_filters"):
                # Use first action as label
                first_action = ""
                for f in n["intent_filters"]:
                    acts = f.get("actions") if isinstance(f, dict) else None
                    if acts:
                        first_action = acts[0].rsplit(".", 1)[-1]
                        break
                edges.append(_make_edge(EXTERNAL, sid, "intent_filter", first_action or "intent"))

    # --- Inter-activity edges from DEX static analysis ---
    if transition_info:
        # Index nodes by activity FQN for lookup
        by_activity = {n["activity"]: n for n in nodes if n.get("activity")}

        # Normal startActivity transitions
        for t in transition_info.get("transitions", []) or []:
            src_fqn = t.get("source", "")
            tgt_fqn = t.get("target", "")
            if not src_fqn or not tgt_fqn:
                continue

            # Auto-add target node as declared if the Manifest didn't list it
            # (happens with aliases, runtime-only classes, or when target is in
            # another app via package-visible Intent).
            tgt_node = by_activity.get(tgt_fqn)
            if not tgt_node and tgt_fqn and "." in tgt_fqn:
                sid = _node_id(tgt_fqn)
                tgt_node = {
                    "screen_id": sid,
                    "activity": tgt_fqn,
                    "label": _short(tgt_fqn),
                    "functional_category": "other",
                    "screen_purpose": "",
                    "params": {"inputs": [], "outputs": [], "displays": []},
                    "widgets": [],
                    "screenshot_ref": "",
                    "structure_str": "",
                    "confidence": "low",
                    "status": "declared",
                    "is_launcher": False,
                    "is_system": _is_system_activity(tgt_fqn),
                    "intent_filters": [],
                    "source_method": t.get("source_method", ""),
                    "node_type": "activity",
                }
                nodes.append(tgt_node)
                by_activity[tgt_fqn] = tgt_node

            # Track that this target is "statically reachable" regardless of source
            if tgt_node:
                tgt_node["statically_reachable"] = True

            src_node = by_activity.get(src_fqn)
            if src_node and tgt_node:
                # Clean Activity→Activity edge (strongest signal)
                trigger = t.get("trigger", "startActivity")
                kind_label = "two_hop" if t.get("kind") == "two_hop" else "navigate"
                edges.append(_make_edge(
                    src_node["screen_id"], tgt_node["screen_id"],
                    kind=kind_label,
                    trigger_widget=f"{trigger} [static]",
                ))
            elif tgt_node and not src_node and src_fqn:
                # Obfuscated helper class → target: record as external soft-edge
                # so the target doesn't look disconnected.  Marked with kind="static_ref"
                # and confidence="static_intent" to differentiate from real edges.
                helper_short = src_fqn.rsplit(".", 1)[-1]
                if not any(e["from"] == EXTERNAL and e["to"] == tgt_node["screen_id"]
                           and e.get("kind") == "static_ref" for e in edges):
                    edges.append(_make_edge(
                        EXTERNAL, tgt_node["screen_id"],
                        kind="static_ref",
                        trigger_widget=f"ref from {helper_short}",
                    ))

        # PendingIntent system entry points
        for t in transition_info.get("pending_intents", []) or []:
            tgt_fqn = t.get("target", "")
            tgt_node = by_activity.get(tgt_fqn)
            if not tgt_node:
                continue
            if not any(e["from"] == EXTERNAL and e["to"] == tgt_node["screen_id"] for e in edges):
                edges.append(_make_edge(
                    EXTERNAL, tgt_node["screen_id"],
                    kind="pending_intent",
                    trigger_widget=t.get("trigger", "PendingIntent"),
                ))

    # Pick entry node: prefer launcher activity if present, else first node
    entry_node_id = EXTERNAL if has_any_filter else (nodes[0]["screen_id"] if nodes else "")

    logger.info(
        "Wireframe ScreenMap: %d nodes (%d declared), %d edges (all static_intent)",
        len(nodes),
        sum(1 for n in nodes if n["status"] == "declared"),
        len(edges),
    )

    # Wrap in ScreenMap schema used by the dashboard
    return {
        "screen_map": {
            "app_name": pkg.rsplit(".", 1)[-1] if "." in pkg else pkg,
            "package_name": pkg,
            "version": metadata.get("version_name", ""),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "graph": {
                "entry_node": entry_node_id,
                "nodes": nodes,
                "edges": edges,
                "edge_groups": [],
                "global_params": {},
            },
            "metadata": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "coverage_ratio": 0.0,
                "orphan_nodes": 0,
                "dead_end_nodes": 0,
                "validation_issues": 0,
                "wireframe_only": True,  # Flag for the dashboard to show "tentative" cues
            },
        }
    }


def _make_edge(src: str, dst: str, kind: str, trigger_widget: str = "") -> dict:
    eid_raw = f"{src}|{dst}|{kind}|{trigger_widget}".encode()
    return {
        "edge_id": "e_sk_" + hashlib.sha256(eid_raw).hexdigest()[:12],
        "from": src,
        "to": dst,
        "trigger_action": "intent",
        "trigger_widget": trigger_widget,
        "kind": kind,  # launcher | intent_filter | static_intent | navigate...
        "confidence": "static_intent",
        "source": "static",
        "condition": None,
        "passed_params": [],
        "returned_params": [],
    }


def write_wireframe(config, static_info: dict, metadata: dict) -> Path:
    """Write the wireframe ScreenMap to the tour's output dir. Returns the path."""
    # Optionally load static transition graph if dex_transitions ran
    transition_info = None
    tg_path = Path(config.static_dir) / "transition_graph.json"
    if tg_path.exists():
        try:
            transition_info = json.loads(tg_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("transition_graph.json parse failed: %s", e)

    screenmap = build_wireframe_screenmap(static_info, metadata, transition_info)
    # Classify nodes (A/B/C) so the scan stage can read `capture_priority`.
    try:
        from .activity_classifier import classify_activities
        classify_activities(screenmap)
    except Exception as e:
        logger.warning("Classifier failed at wireframe time: %s", e)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / config.screenmap_output_filename
    out.write_text(json.dumps(screenmap, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote wireframe ScreenMap → %s", out)
    return out
