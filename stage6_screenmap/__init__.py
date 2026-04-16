"""Stage 6: Screen Map generation — merge, validate, serialize."""

import json
import logging
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

    # 1. Build graph from subflows + screen analyses
    graph = build_graph(subflows, screen_analyses, screen_cards)

    # 2. Inject walk transitions + compute edge weights from frequency
    _inject_walk_transitions(graph, walk_transitions, screen_cards, walk_screens)

    # 2b. Compute edge weights from transition frequency
    _compute_transition_weights(graph, walk_transitions, screen_cards, walk_screens)

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
            graph["edges"].append({
                "edge_id": edge_id,
                "from": from_node,
                "to": to_node,
                "trigger_action": t.get("event_type", "click"),
                "trigger_widget": t.get("event_str", "").replace("click ", ""),
                "condition": None,
                "passed_params": [],
                "returned_params": [],
                "source": "walk",  # Mark as directly observed
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
