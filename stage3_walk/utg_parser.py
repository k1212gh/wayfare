"""Parse DroidBot output directory into structured walk data."""

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_NODE_VAR_NAMES = ("utg_nodes", "nodes", "defined_nodes")
_EDGE_VAR_NAMES = ("utg_edges", "edges", "defined_edges")


def parse_droidbot_output(output_dir: str) -> dict:
    """Read DroidBot's state files and utg.js into a structured walk dict."""
    out = Path(output_dir)
    states = _parse_states(out)
    transitions = _parse_utg(out, states)
    activities_found = sorted({s["activity"] for s in states if s.get("activity")})

    return {
        "states": states,
        "transitions": transitions,
        "activities_found": activities_found,
    }


def _parse_states(out_dir: Path) -> list[dict]:
    """Parse `states/state_*.json`, coalesceing by `state_str`."""
    states_dir = out_dir / "states"
    if not states_dir.exists():
        logger.warning("No states/ directory in %s", out_dir)
        return []

    states: list[dict] = []
    seen_state_strs: set[str] = set()
    dup_count = 0

    for state_file in sorted(states_dir.glob("state_*.json")):
        try:
            raw = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Failed to parse %s", state_file.name)
            continue

        state_str = raw.get("state_str", "")
        if state_str and state_str in seen_state_strs:
            dup_count += 1
            logger.debug("Skipping duplicate state_str: %s (%s)", state_str[:16], state_file.name)
            continue
        if state_str:
            seen_state_strs.add(state_str)

        states.append({
            "state_str": state_str,
            "structure_str": raw.get("structure_str", ""),
            "activity": raw.get("foreground_activity", ""),
            "fragment_class": _extract_fragment(raw),
            "views": raw.get("views", []),
            "screenshot_path": _find_screenshot(out_dir, raw),
            "source_file": state_file.name,
        })

    logger.info("Parsed %d unique states (skipped %d duplicates)", len(states), dup_count)
    return states


def _find_screenshot(out_dir: Path, state_data: dict) -> str:
    screen_path = state_data.get("screenshot_path", "")
    if not screen_path:
        return ""
    p = Path(screen_path)
    if p.exists():
        return str(p)
    rel = out_dir / p.name
    return str(rel) if rel.exists() else ""


def _extract_fragment(raw: dict) -> str:
    """Return the best-effort foreground fragment/class name from a state dump."""
    candidate_keys = (
        "fragment_class",
        "foreground_fragment",
        "top_fragment",
        "visible_fragment",
        "current_fragment",
        "fragment",
        "fragment_name",
    )

    for key in candidate_keys:
        value = raw.get(key, "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _parse_utg(out_dir: Path, states: list[dict]) -> list[dict]:
    """Parse `utg.js` to extract transitions and map UTG node IDs to `state_str`."""
    utg_file = out_dir / "utg.js"
    if not utg_file.exists():
        logger.warning("utg.js not found in %s", out_dir)
        return []

    content = utg_file.read_text(encoding="utf-8", errors="replace")

    node_id_to_screen_str = _build_node_mapping(content, states)

    edges_raw: list | None = None
    for var_name in _EDGE_VAR_NAMES:
        edges_raw = _extract_js_array(content, var_name)
        if edges_raw is not None:
            logger.info("Found UTG edges via 'var %s' (%d edges)", var_name, len(edges_raw))
            break

    if edges_raw is None:
        logger.warning("Could not find/parse edges in utg.js")
        return []

    transitions = []
    for edge in edges_raw:
        raw_from = str(edge.get("from", ""))
        raw_to = str(edge.get("to", ""))
        transitions.append({
            "from_screen": node_id_to_screen_str.get(raw_from, raw_from),
            "to_screen": node_id_to_screen_str.get(raw_to, raw_to),
            "event_type": edge.get("event_type", edge.get("label", "")),
            "event_str": edge.get("event_str", ""),
            "id": edge.get("id", ""),
        })

    logger.info("Parsed %d transitions (with node_id→state_str mapping)", len(transitions))
    return transitions


def _build_node_mapping(utg_content: str, states: list[dict]) -> dict[str, str]:
    """Map UTG node IDs to `state_str` values, falling back to an identity map."""
    mapping: dict[str, str] = {}

    for var_name in _NODE_VAR_NAMES:
        nodes_raw = _extract_js_array(utg_content, var_name)
        if nodes_raw is None:
            continue
        for node in nodes_raw:
            node_id = str(node.get("id", ""))
            state_str = node.get("state_str", "")
            if node_id and state_str:
                mapping[node_id] = state_str
        if mapping:
            logger.info("Built node mapping: %d entries from 'var %s'", len(mapping), var_name)
            break

    if not mapping:
        for ss in {s["state_str"] for s in states if s.get("state_str")}:
            mapping[ss] = ss
        logger.info("Using identity mapping (UTG node IDs = state_strs, %d entries)", len(mapping))

    return mapping


def _extract_js_array(content: str, var_name: str) -> list | None:
    """Extract `var <var_name> = [...]` using bracket balancing so `]` inside
    strings or nested arrays doesn't end the match early (which a plain
    `\\[.*?\\]` regex would)."""
    pattern = re.compile(rf"var\s+{re.escape(var_name)}\s*=\s*\[")
    match = pattern.search(content)
    if not match:
        return None

    start = match.end() - 1  # position of the opening '['
    end = _find_matching_bracket(content, start)
    if end == -1:
        return None

    try:
        return json.loads(content[start:end + 1])
    except json.JSONDecodeError:
        return None


def _find_matching_bracket(s: str, open_idx: int) -> int:
    """Return the index of the `]` that balances `s[open_idx] == '['`, or -1.

    Respects JSON string literals (including backslash escapes) so brackets
    inside strings do not confuse the counter.
    """
    depth = 0
    in_string = False
    escape = False
    for i in range(open_idx, len(s)):
        ch = s[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i
    return -1
