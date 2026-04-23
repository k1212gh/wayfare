"""Stage 6: Screen Map generation — merge, validate, serialize."""

import json
import logging
from pathlib import Path
from config import PipelineConfig
from .screenmap_builder import build_graph
from .screenmap_validator import validate_graph
from .screenmap_enricher import enrich_graph
from .screenmap_serializer import serialize_screenmap

logger = logging.getLogger(__name__)


def run_stage6(config: PipelineConfig) -> None:
    """Build the final screen_map.json."""
    sa_path = config.analysis_dir / "screen_analyses.json"
    sg_path = config.analysis_dir / "subflows.json"
    cu_path = config.analysis_dir / "screen_cards.json"
    meta_path = config.apk_dir / "metadata.json"
    static_path = config.static_dir / "analysis.json"
    walk_path = config.dynamic_dir / "walk.json"

    screen_analyses = json.loads(sa_path.read_text(encoding="utf-8")) if sa_path.exists() else []
    subflows = json.loads(sg_path.read_text(encoding="utf-8")) if sg_path.exists() else []
    screen_cards = json.loads(cu_path.read_text(encoding="utf-8")) if cu_path.exists() else []
    metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    static_info = json.loads(static_path.read_text(encoding="utf-8")) if static_path.exists() else {}

    # Load walk data (TapWalker/DroidBot)
    walk_transitions = []
    walk_screens = []
    if walk_path.exists():
        walk = json.loads(walk_path.read_text(encoding="utf-8"))
        walk_transitions = walk.get("transitions", [])
        walk_screens = walk.get("states", [])
        logger.info("Loaded %d walk transitions, %d states", len(walk_transitions), len(walk_screens))

    # 1. Build graph from subflows + screen analyses (fresh)
    graph = build_graph(subflows, screen_analyses, screen_cards)

    # 1b. If a wireframe ScreenMap was built at Stage 2.5, merge it in — preserves
    #     declared activities we never visited during walk, plus static
    #     entry edges (launcher / intent-filter).
    try:
        _merge_wireframe(config, graph, static_info, metadata)
    except Exception as e:
        logger.warning("Wireframe merge failed: %s", e)

    # 1b2. Classify each activity as A (user screen) / B (plumbing) / C (deep-link)
    #      BEFORE manifest scan so downstream stages can read the priority.
    #      Scan doesn't read this yet (scan runs in Stage 3), but the frontend
    #      and task navigator use it to interpret node roles.
    try:
        from .activity_classifier import classify_activities
        # Wrap graph as ScreenMap-shaped dict for the classifier
        classify_activities({"screen_map": {"graph": graph}})
    except Exception as e:
        logger.warning("Activity classification failed: %s", e)

    # 1c. Apply manifest scan results — activities successfully launched by
    #     `am start -W` get status='probed'. Coverage bar counts them as
    #     reachable without requiring UI-walked navigation.
    try:
        _apply_manifest_scan(config, graph)
    except Exception as e:
        logger.warning("Manifest scan merge failed: %s", e)

    # 1d. Fragment hierarchy: link each fragment node to its host activity
    #     (parent_activity_id) + emit `contains` edges Activity → Fragment.
    try:
        _link_fragment_hierarchy(graph)
    except Exception as e:
        logger.warning("Fragment hierarchy linking failed: %s", e)

    # 2. Inject walk transitions + compute edge weights from frequency
    _inject_walk_transitions(graph, walk_transitions, screen_cards, walk_screens)

    # 2b. Compute edge weights from transition frequency
    _compute_transition_weights(graph, walk_transitions, screen_cards, walk_screens)

    # 2b+. Detect global edges — same (to, trigger) appearing from ≥3 sources
    try:
        _mark_global_transitions(graph)
    except Exception as e:
        logger.warning("Global edge detection failed: %s", e)

    # 2c. Heuristic classification — assign functional_category for every node
    # that the LLM hasn't already labeled. Uses UI element / activity hints.
    try:
        from .heuristic_classifier import classify_all_nodes
        classify_all_nodes(
            graph.get("nodes", []),
            screen_cards,
            graph.get("entry_node", ""),
        )
        cats = {}
        for n in graph.get("nodes", []):
            c = n.get("functional_category", "other")
            cats[c] = cats.get(c, 0) + 1
        logger.info("Heuristic classification: %s", cats)
    except Exception as e:
        logger.warning("Heuristic classification failed: %s", e)

    # 3. Enrich with static analysis
    graph = enrich_graph(graph, static_info)

    # 4. Validate
    report = validate_graph(graph)
    logger.info("Validation: %s", json.dumps(report["summary"], indent=2))

    # 5. Serialize
    screenmap = serialize_screenmap(graph, metadata, report)

    output_path = config.output_dir / config.screenmap_output_filename
    output_path.write_text(json.dumps(screenmap, indent=2, ensure_ascii=False), encoding="utf-8")

    report_path = config.output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("ScreenMap generated: %d nodes, %d edges", len(graph.get("nodes", [])), len(graph.get("edges", [])))


def _inject_walk_transitions(graph: dict, transitions: list[dict],
                              screen_cards: list[dict], walk_screens: list[dict]) -> None:
    """Inject observed walk transitions into the graph.

    Maps walk IDs to page_ids by matching structure_str.
    """
    # Build structure_str → page_id mapping from context units
    struct_to_page: dict[str, str] = {}
    for cu in screen_cards:
        struct = cu.get("structure_str", "")
        if struct:
            struct_to_page[struct] = cu["screen_id"]
            struct_to_page[struct[:16]] = cu["screen_id"]

    # Build walk state_str → structure_str mapping
    exp_to_struct: dict[str, str] = {}
    for s in walk_screens:
        ss = s.get("state_str", "")
        struct = s.get("structure_str", "")
        if ss and struct:
            exp_to_struct[ss] = struct

    # Combined: walk_id → page_id
    def resolve(exp_id: str) -> str | None:
        # Direct page match
        if exp_id in {n["screen_id"] for n in graph.get("nodes", [])}:
            return exp_id
        # Via structure_str
        struct = exp_to_struct.get(exp_id, "")
        if struct:
            page = struct_to_page.get(struct) or struct_to_page.get(struct[:16])
            if page:
                return page
        # By canonical index
        if exp_id.startswith("screen_"):
            try:
                idx = int(exp_id.split("_")[1])
                pages = sorted({n["screen_id"] for n in graph.get("nodes", [])})
                if idx < len(pages):
                    return pages[idx]
            except (ValueError, IndexError):
                pass
        return None

    node_ids = {n["screen_id"] for n in graph.get("nodes", [])}
    existing_edges = {(e["from"], e["to"]) for e in graph.get("edges", [])}
    added = 0

    for t in transitions:
        from_id = t.get("from_screen", "")
        to_id = t.get("to_screen", "")

        from_node = resolve(from_id)
        to_node = resolve(to_id)

        # Check if nodes exist in graph, or find closest match
        if from_node not in node_ids:
            from_node = _find_matching_node(from_id, node_ids)
        if to_node not in node_ids:
            to_node = _find_matching_node(to_id, node_ids)

        if from_node and to_node and from_node != to_node and (from_node, to_node) not in existing_edges:
            import hashlib
            edge_id = f"e_exp_{hashlib.sha256(f'{from_node}|{to_node}'.encode()).hexdigest()[:12]}"
            event_type = t.get("event_type", "click")
            event_str = t.get("event_str", "").replace("click ", "")

            # Determine edge kind: back > overlay > contains(same activity) > navigate
            kind = "navigate"
            if "press_back" in event_type.lower() or "keycode_back" in event_str.lower():
                kind = "back"
            else:
                # Same-activity fragment transition → contains
                from_node_obj = next((n for n in graph.get("nodes", []) if n.get("screen_id") == from_node), {})
                to_node_obj = next((n for n in graph.get("nodes", []) if n.get("screen_id") == to_node), {})
                if from_node_obj.get("activity") and from_node_obj.get("activity") == to_node_obj.get("activity"):
                    kind = "contains"
                elif to_node_obj.get("functional_category") == "dialog":
                    kind = "overlay"

            graph["edges"].append({
                "edge_id": edge_id,
                "from": from_node,
                "to": to_node,
                "trigger_action": event_type,
                "trigger_widget": event_str,
                "kind": kind,
                "confidence": "observed",  # directly seen during walk
                "source": "walk",
                "condition": None,
                "passed_params": [],
                "returned_params": [],
            })
            existing_edges.add((from_node, to_node))
            added += 1

    if added:
        logger.info("Injected %d walk edges into graph", added)


def _find_matching_node(candidate: str, node_ids: set[str]) -> str | None:
    """Try to find a matching node ID by substring or index."""
    # Direct match
    if candidate in node_ids:
        return candidate

    # screen_XXX → try matching by index to page list
    if candidate.startswith("screen_"):
        try:
            idx = int(candidate.split("_")[1])
            sorted_nodes = sorted(node_ids)
            if idx < len(sorted_nodes):
                return sorted_nodes[idx]
        except (ValueError, IndexError):
            pass

    # Substring match
    for nid in node_ids:
        if candidate[:12] in nid or nid[:12] in candidate:
            return nid

    return None


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


def _apply_manifest_scan(config, graph: dict) -> None:
    """Flip status to 'probed' for every declared activity the scan could launch.

    Input: dynamic/manifest_scan.json — {fqn: {launched: bool, foreground: str}}
    Effect: matching declared nodes get status='probed'. Entry/resolved/unknown
    are left alone (they were touched naturally).
    """
    scan_path = Path(config.dynamic_dir) / "manifest_scan.json"
    if not scan_path.exists():
        return
    try:
        scan = json.loads(scan_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("manifest_scan.json parse failed: %s", e)
        return

    by_activity: dict[str, dict] = {}
    for n in graph.get("nodes", []):
        act = n.get("activity", "")
        if act:
            by_activity[act] = n

    promoted = 0
    for act, result in scan.items():
        if not result.get("launched"):
            continue
        node = by_activity.get(act)
        if not node:
            continue
        if node.get("status") == "declared":
            node["status"] = "probed"
            promoted += 1
    if promoted:
        logger.info("Manifest scan promoted %d nodes to status='probed'", promoted)

    # Also promote classifier-B (plumbing) and classifier-C (deep-link) nodes
    # to `probed` status — they're reachable-by-design (manifest declared +
    # scan intentionally skipped to save time). Without this they stay as
    # `declared` which undercounts true reachability.
    by_prio_promoted = 0
    for n in graph.get("nodes", []):
        if n.get("screen_id") == "system:external_entry":
            continue
        if n.get("status") != "declared":
            continue
        prio = n.get("capture_priority", "")
        if prio in ("B", "C"):
            n["status"] = "probed"
            by_prio_promoted += 1
    if by_prio_promoted:
        logger.info("Classifier promoted %d B/C nodes to status='probed'", by_prio_promoted)


def _link_fragment_hierarchy(graph: dict) -> None:
    """Tag fragment nodes with parent_activity_id and emit `contains` edges.

    For each node where `node_type == 'fragment'`, find a sibling Activity
    node (same `host_activity` / `activity`) and:
      - set fragment.parent_activity_id = activity.screen_id
      - add an edge `activity -- contains --> fragment` (if not present)

    If no host activity node exists yet (walk captured a fragment for
    an activity whose wireframe stub was dropped), create a wireframe-like
    host node so the fragment isn't orphaned.
    """
    import hashlib as _hl
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # Index: activity FQN → activity-type node
    activity_hosts: dict[str, dict] = {}
    for n in nodes:
        if n.get("node_type") == "activity" and n.get("activity"):
            activity_hosts[n["activity"]] = n

    existing_edge_keys = {(e.get("from"), e.get("to"), e.get("kind"))
                          for e in edges}
    new_edges = 0
    new_hosts = 0
    linked = 0

    for n in list(nodes):
        if n.get("node_type") != "fragment":
            continue
        host_fqn = n.get("host_activity") or n.get("activity") or ""
        if not host_fqn:
            continue
        host = activity_hosts.get(host_fqn)
        if not host:
            # Synthesize a host node so the fragment has somewhere to hang.
            sid = f"act_{_hl.sha256(host_fqn.encode()).hexdigest()[:12]}"
            short = host_fqn.rsplit(".", 1)[-1] if "." in host_fqn else host_fqn
            host = {
                "screen_id": sid,
                "activity": host_fqn,
                "label": short,
                "functional_category": "other",
                "screen_purpose": "",
                "params": {"inputs": [], "outputs": [], "displays": []},
                "widgets": [],
                "screenshot_ref": "",
                "structure_str": "",
                "confidence": "low",
                "status": "declared",
                "is_launcher": False,
                "is_system": False,
                "intent_filters": [],
                "node_type": "activity",
            }
            nodes.append(host)
            activity_hosts[host_fqn] = host
            new_hosts += 1

        # Link fragment → parent activity id (don't overwrite if already set)
        if not n.get("parent_activity_id"):
            n["parent_activity_id"] = host["screen_id"]
        linked += 1

        # Emit `contains` edge host → fragment (coalesceed)
        edge_key = (host["screen_id"], n["screen_id"], "contains")
        if edge_key not in existing_edge_keys:
            edges.append({
                "edge_id": f"e_contains_{host['screen_id'][:10]}_{n['screen_id'][:10]}",
                "from": host["screen_id"],
                "to": n["screen_id"],
                "trigger_action": "contains",
                "trigger_widget": "fragment_transaction",
                "kind": "contains",
                "confidence": "static_intent",
                "source": "hierarchy",
                "condition": None,
                "passed_params": [],
                "returned_params": [],
            })
            existing_edge_keys.add(edge_key)
            new_edges += 1

    if linked:
        logger.info("Fragment hierarchy: %d fragments linked, %d new hosts, %d contains edges",
                    linked, new_hosts, new_edges)


def _mark_global_transitions(graph: dict, min_sources: int = 3) -> None:
    """Promote edges whose (to, trigger_widget) appears from many sources to kind=global.

    Intuition: a bottom-nav button or drawer item shows up in every screen, so the
    ScreenMap will have many edges (*-> target) all with the same trigger. Tag those.
    """
    from collections import defaultdict
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in graph.get("edges", []):
        key = (e.get("to", ""), e.get("trigger_widget", "").lower().strip())
        if not key[0] or not key[1]:
            continue
        buckets[key].append(e)

    promoted = 0
    for (to, trig), edges in buckets.items():
        unique_sources = {e.get("from", "") for e in edges}
        if len(unique_sources) >= min_sources:
            for e in edges:
                if e.get("kind") in (None, "navigate"):
                    e["kind"] = "global"
                    promoted += 1
    if promoted:
        logger.info("Promoted %d edges to kind=global", promoted)


def _compute_transition_weights(graph: dict, transitions: list[dict],
                          screen_cards: list[dict], walk_screens: list[dict]) -> None:
    """Compute edge weights from walk transition frequency.

    Frequently traversed edges get lower weight (= preferred path).
    weight = 1 / (frequency + 1)
    """
    from collections import Counter

    struct_to_page: dict[str, str] = {}
    for cu in screen_cards:
        struct = cu.get("structure_str", "")
        if struct:
            struct_to_page[struct] = cu["screen_id"]
            struct_to_page[struct[:16]] = cu["screen_id"]

    exp_to_struct: dict[str, str] = {}
    for s in walk_screens:
        ss = s.get("state_str", "")
        struct = s.get("structure_str", "")
        if ss and struct:
            exp_to_struct[ss] = struct

    node_ids = {n["screen_id"] for n in graph.get("nodes", [])}

    def resolve(exp_id):
        if exp_id in node_ids:
            return exp_id
        struct = exp_to_struct.get(exp_id, "")
        if struct:
            return struct_to_page.get(struct) or struct_to_page.get(struct[:16])
        return None

    freq = Counter()
    for t in transitions:
        fn = resolve(t.get("from_screen", ""))
        tn = resolve(t.get("to_screen", ""))
        if fn and tn:
            freq[(fn, tn)] += 1

    for edge in graph.get("edges", []):
        key = (edge["from"], edge["to"])
        count = freq.get(key, 0)
        edge["weight"] = round(1.0 / (count + 1), 3)
        edge["frequency"] = count

    weighted = sum(1 for e in graph["edges"] if e.get("frequency", 0) > 0)
    logger.info("Edge weights: %d/%d edges have walk frequency", weighted, len(graph["edges"]))
