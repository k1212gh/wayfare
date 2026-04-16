"""Parse DroidBot output directory into structured walk data."""

import json
import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_droidbot_output(output_dir: str) -> dict:
    """Parse DroidBot's output directory.

    Reads:
      - states/state_*.json → list of UI states
      - utg.js → transition graph (edges), with node_id→state_str mapping

    Returns dict with:
      - states: list of state dicts (state_str, structure_str, activity, views)
      - transitions: list of dicts with from_screen/to_screen as state_str values
      - activities_found: unique activities discovered
    """
    out = Path(output_dir)

    states = _parse_states(out)
    transitions = _parse_utg(out, states)

    activities_found = list({s["activity"] for s in states if s.get("activity")})

    return {
        "states": states,
        "transitions": transitions,
        "activities_found": activities_found,
    }


def _parse_states(out_dir: Path) -> list[dict]:
    """Parse all state_*.json files from DroidBot output."""
    states_dir = out_dir / "states"
    if not states_dir.exists():
        logger.warning("No states/ directory in %s", out_dir)
        return []

    states = []
    seen_state_strs: set[str] = set()  # FIX: Coalescelicate by state_str

    for state_file in sorted(states_dir.glob("state_*.json")):
        try:
            raw = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Failed to parse %s", state_file.name)
            continue

        state_str = raw.get("state_str", "")

        # Skip already-seen states (DroidBot may dump the same state multiple times)
        if state_str and state_str in seen_state_strs:
            logger.debug("Skipping duplicate state_str: %s (%s)", state_str[:16], state_file.name)
            continue
        if state_str:
            seen_state_strs.add(state_str)

        state = {
            "state_str": state_str,
            "structure_str": raw.get("structure_str", ""),
            "activity": raw.get("foreground_activity", ""),
            "views": raw.get("views", []),
            "screenshot_path": _find_screenshot(out_dir, raw),
            "source_file": state_file.name,
        }
        states.append(state)

    logger.info("Parsed %d unique states (skipped %d duplicates)",
                len(states), len(seen_state_strs) - len(states) if len(seen_state_strs) > len(states) else 0)
    return states


def _find_screenshot(out_dir: Path, state_data: dict) -> str:
    """Find the screenshot file associated with a state."""
    screen_path = state_data.get("screenshot_path", "")
    if screen_path:
        p = Path(screen_path)
        if p.exists():
            return str(p)
        # Try relative to output dir
        rel = out_dir / p.name
        if rel.exists():
            return str(rel)
    return ""


def _parse_utg(out_dir: Path, states: list[dict]) -> list[dict]:
    """Parse utg.js to extract state transitions.

    FIX #3: Builds node_id→state_str mapping from UTG nodes,
    so transitions reference state_str (matching state files) not UTG node IDs.

    utg.js contains JavaScript variable assignments like:
        var utg_nodes = [...];
        var utg_edges = [...];
    """
    utg_file = out_dir / "utg.js"
    if not utg_file.exists():
        logger.warning("utg.js not found in %s", out_dir)
        return []

    content = utg_file.read_text(encoding="utf-8", errors="replace")

    # --- Parse nodes to build node_id→state_str mapping ---
    node_id_to_screen_str = _build_node_mapping(content, states)

    # --- Parse edges ---
    # FIX #4: Try multiple variable names used by different DroidBot versions
    edges_raw = None
    for var_name in ("utg_edges", "edges", "defined_edges"):
        edges_match = re.search(
            rf"var\s+{var_name}\s*=\s*(\[.*?\]);",
            content,
            re.DOTALL,
        )
        if edges_match:
            try:
                edges_raw = json.loads(edges_match.group(1))
                logger.info("Found UTG edges via 'var %s' (%d edges)", var_name, len(edges_raw))
                break
            except json.JSONDecodeError:
                continue

    if edges_raw is None:
        logger.warning("Could not find/parse edges in utg.js")
        return []

    transitions = []
    for edge in edges_raw:
        raw_from = str(edge.get("from", ""))
        raw_to = str(edge.get("to", ""))

        # Map UTG node IDs to state_str values
        from_screen = node_id_to_screen_str.get(raw_from, raw_from)
        to_screen = node_id_to_screen_str.get(raw_to, raw_to)

        transitions.append({
            "from_screen": from_screen,
            "to_screen": to_screen,
            "event_type": edge.get("event_type", edge.get("label", "")),
            "event_str": edge.get("event_str", ""),
            "id": edge.get("id", ""),
        })

    logger.info("Parsed %d transitions (with node_id→state_str mapping)", len(transitions))
    return transitions


def _build_node_mapping(utg_content: str, states: list[dict]) -> dict[str, str]:
    """Build a mapping from UTG node IDs to state_str values.

    DroidBot UTG node IDs may differ from state_str. This function
    parses the nodes array and cross-references with parsed state files.
    """
    mapping: dict[str, str] = {}

    # Try to parse UTG nodes
    for var_name in ("utg_nodes", "nodes", "defined_nodes"):
        nodes_match = re.search(
            rf"var\s+{var_name}\s*=\s*(\[.*?\]);",
            utg_content,
            re.DOTALL,
        )
        if nodes_match:
            try:
                nodes_raw = json.loads(nodes_match.group(1))
                for node in nodes_raw:
                    node_id = str(node.get("id", ""))
                    state_str = node.get("state_str", "")
                    if node_id and state_str:
                        mapping[node_id] = state_str
                logger.info("Built node mapping: %d entries from 'var %s'", len(mapping), var_name)
                break
            except json.JSONDecodeError:
                continue

    # Fallback: if UTG nodes use state_str as ID directly, create identity mapping
    if not mapping:
        known_strs = {s["state_str"] for s in states if s.get("state_str")}
        for ss in known_strs:
            mapping[ss] = ss
        logger.info("Using identity mapping (UTG node IDs = state_strs, %d entries)", len(mapping))

    return mapping
