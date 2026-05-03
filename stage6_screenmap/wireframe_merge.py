"""Merge the Stage-2.5 wireframe ScreenMap into the walk-derived graph.

Extracted from stage6_screenmap/transformations.py (Step 4 세분화).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def _merge_wireframe(config, graph: dict, static_info: dict, metadata: dict) -> None:
    """Merge the Stage-2.5 wireframe into the fresh graph.

    Policy:
      - Wireframe activity nodes we already have (by activity FQN) → keep enriched
        data and just preserve `intent_filters` / `is_launcher` flags.
      - Wireframe activity nodes we DON'T have → add with status='declared'.
      - Wireframe edges (launcher / intent_filter / static transitions) → add if
        not already present.
    """
    from .wireframe_builder import build_wireframe_screenmap

    # Load DEX transition_graph.json so we preserve static navigate / two_hop edges
    transition_info = None
    tg_path = Path(config.static_dir) / "transition_graph.json"
    if tg_path.exists():
        try:
            transition_info = json.loads(tg_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("transition_graph.json parse failed in merge: %s", e)

    wireframe = build_wireframe_screenmap(static_info, metadata, transition_info)
    sk_graph = wireframe["screen_map"]["graph"]
    sk_nodes = sk_graph.get("nodes", [])
    sk_edges = sk_graph.get("edges", [])

    # Index existing ACTIVITY-type nodes by activity FQN. Fragment nodes
    # hanging off an Activity must NOT take over the wireframe's Activity stub,
    # because the Activity host is the legitimate merge target.
    existing_activity_by_fqn: dict[str, dict] = {}
    existing_fragments_by_fqn: dict[str, list[dict]] = {}
    for n in graph.get("nodes", []):
        act = n.get("activity", "")
        if not act:
            continue
        if n.get("node_type") == "fragment":
            existing_fragments_by_fqn.setdefault(act, []).append(n)
        else:
            # Treat default / unspecified node_type as activity for merge.
            existing_activity_by_fqn[act] = n

    # Node merge — also build id-remap so wireframe edges can be re-pointed.
    # Without remap, when an walk page (`page_xxx`) covers the same
    # activity as a wireframe stub (`act_yyy`), the wireframe stub is dropped
    # but its outgoing/incoming edges still reference `act_yyy` → orphan edges.
    id_remap: dict[str, str] = {}
    existing_ids = {n["screen_id"] for n in graph.get("nodes", [])}
    added_nodes = 0
    for sn in sk_nodes:
        act = sn.get("activity", "")
        sk_id = sn["screen_id"]
        if sk_id == "system:external_entry":
            if sk_id not in existing_ids:
                graph["nodes"].append(sn)
                existing_ids.add(sk_id)
                added_nodes += 1
            continue
        matching = existing_activity_by_fqn.get(act)
        if matching:
            # Walk already produced an Activity-type node for this FQN.
            # Upgrade its status + copy wireframe metadata (intent_filters etc).
            for k in ("intent_filters", "is_launcher", "is_system"):
                if k in sn and k not in matching:
                    matching[k] = sn[k]
            if matching.get("status") in (None, "declared"):
                matching["status"] = "enriched"
            id_remap[sk_id] = matching["screen_id"]
        elif act in existing_fragments_by_fqn:
            # Only Fragment nodes exist for this FQN — add the wireframe Activity
            # as the host node (don't drop it). Fragment hierarchy step will
            # later attach `parent_activity_id` + `contains` edges.
            #
            # 2026-05-03 (P2): 자식 fragment 의 widgets / primary_affordances
            # 를 host activity 에 union — 메가커피 71cc7845 회귀: act_*
            # (wireframe) 노드와 page_* (fragment) 가 별도라 act_* 가
            # actionable=False (분모만 갉아먹음). 자식 fragment 가 가진 ui 신호
            # 합성하면 host 도 actionable 로 승격되어 분자 ↑.
            children = [
                n for n in graph["nodes"]
                if n.get("activity") == act and n.get("node_type") == "fragment"
            ]
            if children:
                # 자식 widgets union (id 기준 coalesce)
                ui_by_id: dict = {}
                for c in children:
                    for ui in (c.get("widgets") or []):
                        key = ui.get("id") if isinstance(ui, dict) else str(ui)
                        if key:
                            ui_by_id[key] = ui
                if ui_by_id:
                    sn.setdefault("widgets", [])
                    for k, v in ui_by_id.items():
                        if not any(
                            (e.get("id") if isinstance(e, dict) else str(e)) == k
                            for e in sn["widgets"]
                        ):
                            sn["widgets"].append(v)
                # primary_affordances union (string list)
                aff_set = {a for c in children for a in (c.get("primary_affordances") or [])}
                if aff_set:
                    existing_aff = set(sn.get("primary_affordances") or [])
                    sn["primary_affordances"] = list(existing_aff | aff_set)
                # status 승격 — 자식이 enriched/probed 면 host 도
                child_statuses = {c.get("status", "") for c in children}
                if "enriched" in child_statuses:
                    sn["status"] = "enriched"
                elif "probed" in child_statuses and sn.get("status", "declared") == "declared":
                    sn["status"] = "probed"
            if sk_id not in existing_ids:
                graph["nodes"].append(sn)
                existing_ids.add(sk_id)
                existing_activity_by_fqn[act] = sn
                added_nodes += 1
        else:
            if sk_id not in existing_ids:
                graph["nodes"].append(sn)
                existing_ids.add(sk_id)
                existing_activity_by_fqn[act] = sn
                added_nodes += 1

    # Edge merge with id remap applied.
    existing_edges = {(e["from"], e["to"]) for e in graph.get("edges", [])}
    added_edges = 0
    dropped_self_loops = 0
    for se in sk_edges:
        src = id_remap.get(se["from"], se["from"])
        dst = id_remap.get(se["to"], se["to"])
        # After remap, both endpoints must exist and not form a self-loop.
        if src not in existing_ids or dst not in existing_ids:
            continue
        if src == dst:
            dropped_self_loops += 1
            continue
        key = (src, dst)
        if key in existing_edges:
            continue
        new_edge = dict(se)
        new_edge["from"] = src
        new_edge["to"] = dst
        graph["edges"].append(new_edge)
        existing_edges.add(key)
        added_edges += 1
    if dropped_self_loops:
        logger.info("Wireframe merge: dropped %d self-loop edges after id remap",
                    dropped_self_loops)

    # Re-point entry_node to system:external_entry if wireframe defined it
    if wireframe["screen_map"]["graph"].get("entry_node") == "system:external_entry":
        graph["entry_node"] = "system:external_entry"

    if added_nodes or added_edges:
        logger.info("Wireframe merge: +%d nodes (declared), +%d edges (static)", added_nodes, added_edges)


