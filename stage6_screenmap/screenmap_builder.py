"""Merge subflows into a single global app flow graph."""

import hashlib
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

NAVIGATE_ACTION = "navigate"
CONTAINS_ACTION = "contains"
NAVIGATE_EDGE_KIND = "navigate"
FRAGMENT_NAV_EDGE_KIND = "fragment_nav"
CONTAINS_EDGE_KIND = "contains"


def build_graph(
    subflows: list[dict],
    screen_analyses: list[dict],
    screen_cards: list[dict],
    entry_activity: str = "",
) -> dict[str, Any]:
    """Merge subflows + screen analyses into a unified directed graph.

    `entry_activity` is the launcher activity from static analysis; when
    provided it takes precedence over heuristic name matching.
    """
    analysis_map = {a["screen_id"]: a for a in screen_analyses}
    unit_map = {u["screen_id"]: u for u in screen_cards}

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    edge_set: set[tuple] = set()

    for sg in subflows:
        for node in sg.get("nodes", []):
            sid = node["screen_id"]
            if sid in nodes:
                _merge_node(nodes[sid], node)
            else:
                nodes[sid] = _build_node(node, analysis_map.get(sid, {}), unit_map.get(sid, {}))

    for sa in screen_analyses:
        sid = sa["screen_id"]
        if sid not in nodes:
            nodes[sid] = _build_node({}, sa, unit_map.get(sid, {}))

    for unit in screen_cards:
        sid = unit.get("screen_id", "")
        if sid and sid not in nodes:
            nodes[sid] = _build_node({}, {}, unit)

    for sg in subflows:
        for edge in sg.get("edges", []):
            trigger_action = edge.get("trigger_action", "")
            trigger_elem = edge.get("trigger_widget", "")
            key = (edge.get("from", ""), edge.get("to", ""), trigger_action, trigger_elem)
            if key not in edge_set:
                edge_set.add(key)
                edges.append(_build_edge(edge))

    # Synthetic reachability edges from screen_card navigation. Action is
    # deliberately "navigate" (not "click") so downstream validators can skip
    # them when checking trigger-level determinism.
    for unit in screen_cards:
        sid = unit["screen_id"]
        for target in unit.get("navigation_context", {}).get("reachable_screens", []):
            key = (sid, target, NAVIGATE_ACTION, "")
            if key not in edge_set and target in nodes:
                edge_set.add(key)
                edges.append({
                    "edge_id": _make_edge_id(sid, target, NAVIGATE_ACTION, ""),
                    "from": sid,
                    "to": target,
                    "trigger_action": NAVIGATE_ACTION,
                    "trigger_widget": "",
                    "condition": None,
                    "passed_params": [],
                    "returned_params": [],
                })

    _inject_activity_hosts(nodes, edges, edge_set)
    _annotate_edge_kinds(edges, nodes)

    # 2026-04-30: screen_id 매칭 누락 노드도 structure_str 으로 cross-check.
    # Stage 4 의 screen_cards 가 needs_vision_in_revisit 마킹을 unit 에 propagation
    # 했지만 build_graph 의 unit_map (screen_id 기준) lookup 이 실패하면 ScreenMap 노드의
    # is_provisional 이 0 이 됨. structure_str 은 hash 라 stable — 두 번째 매칭 pass.
    unit_by_struct: dict[str, dict] = {
        u.get("structure_str", ""): u
        for u in screen_cards
        if u.get("needs_vision_in_revisit") and u.get("structure_str")
    }
    if unit_by_struct:
        marked_count = 0
        for n in nodes.values():
            s = n.get("structure_str", "")
            if s and s in unit_by_struct and not n.get("is_provisional"):
                n["is_provisional"] = True
                marked_count += 1
        if marked_count:
            import logging as _log
            _log.getLogger(__name__).info(
                "[provisional] %d nodes marked is_provisional via structure_str fallback",
                marked_count,
            )

    entry_node = _find_entry_node(nodes, screen_cards, entry_activity)

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "entry_node": entry_node,
    }


def _make_edge_id(from_id: str, to_id: str, trigger_action: str, trigger_widget: str) -> str:
    """Hash every field that distinguishes two edges — otherwise two edges that
    share (from, to, action) but have different elements collide on edge_id."""
    raw = f"{from_id}|{to_id}|{trigger_action}|{trigger_widget}"
    return f"e_{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def short_activity_name(activity: str, fragment: str = "") -> str:
    """'co.kr.app.ui.main.MainActivity' → 'Main' (+ ' · Fragment')."""
    name = (activity or "").rsplit(".", 1)[-1]
    for suffix in ("Activity", "Fragment"):
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
    frag = (fragment or "").rsplit(".", 1)[-1]
    if frag and frag.upper() != frag:   # 'CLOCKS' 같은 태그는 그대로
        for suffix in ("Fragment",):
            if frag.endswith(suffix) and len(frag) > len(suffix):
                frag = frag[: -len(suffix)]
    return f"{name} · {frag}" if frag and frag.lower() != name.lower() else name


def fallback_label(llm_label: str, unit: dict, activity_name: str, sid: str) -> str:
    """라벨 대체 체인 (2026-09-12):
    LLM 라벨 → 제목 텍스트 → 화면에 보이는 첫 텍스트 후보 → 액티비티 짧은 이름 → screen_id.
    LLM 없이도 'page_xxx' 대신 사람이 읽는 이름이 붙는다."""
    if llm_label and llm_label.strip():
        return llm_label.strip()
    title = (unit.get("title_text") or "").strip()
    if title:
        return title
    cands = [str(c).strip() for c in (unit.get("label_candidates") or []) if str(c).strip()]
    # 홍보 문장처럼 긴 후보는 건너뛰고 짧은 제목을 우선 (전부 길면 첫 후보라도 사용)
    for c in cands:
        if len(c) <= 24:
            return c
    if cands:
        return cands[0][:24]
    short = short_activity_name(activity_name, unit.get("fragment") or unit.get("fragment_class") or "")
    return short or sid


def _build_node(sg_node: dict, analysis: dict, unit: dict) -> dict:
    """Build a node in schema format."""
    sid = sg_node.get("screen_id") or analysis.get("screen_id") or unit.get("screen_id", "")
    activity_name = unit.get("activity_name", analysis.get("activity_name", ""))
    label_source = fallback_label(sg_node.get("label") or analysis.get("screen_purpose") or "",
                                  unit, activity_name, sid)
    node_type = (
        unit.get("node_type")
        or analysis.get("node_type")
        or sg_node.get("node_type")
        or "activity"
    )
    parent_activity_id = (
        unit.get("parent_activity_id")
        or analysis.get("parent_activity_id")
        or sg_node.get("parent_activity_id")
        or ""
    )
    host_activity = (
        unit.get("host_activity")
        or analysis.get("host_activity")
        or sg_node.get("host_activity")
        or activity_name
    )
    fragment_class = (
        unit.get("fragment_class")
        or analysis.get("fragment_class")
        or sg_node.get("fragment_class")
        or ""
    )
    return {
        "screen_id": sid,
        "activity": activity_name,
        "node_type": node_type,
        "parent_activity_id": parent_activity_id,
        "host_activity": host_activity,
        "fragment_class": fragment_class,
        "label": label_source[:50] if label_source else sid,
        "label_source": "llm" if (sg_node.get("label") or analysis.get("screen_purpose")) else "fallback",
        "functional_category": analysis.get("functional_category") or ("dialog" if unit.get("is_dialog") else "other"),
        "screen_purpose": analysis.get("screen_purpose", sg_node.get("functional_role", "")),
        "params": sg_node.get("screen_params", {"inputs": [], "outputs": [], "displays": []}),
        "widgets": [
            _build_widget(e)
            for e in analysis.get("key_widgets", unit.get("available_actions", []))
        ],
        # ScreenMap expressivity extensions (sprint 2026-04-27). All optional, default
        # to empty so legacy consumers ignoring these fields keep working.
        # Filled by Stage 5 chip_group_detector + Stage 6 transformations.
        "chip_groups": unit.get("chip_groups", []),
        "state_variables": unit.get("state_variables", []),
        # 2026-09-13 (#4): 오버레이 노드 — 부모와 병합 금지, 에이전트는 닫아야 부모를 조작할 수 있음
        "is_dialog": bool(unit.get("is_dialog", False)),
        "blocks_parent": bool(unit.get("is_dialog", False)),
        "search_outcome": unit.get("search_outcome") or "",
        "infinite_scroll": False,           # set by _mark_infinite_scroll_nodes (Stage 6)
        "scroll_metadata": {},              # populated alongside infinite_scroll
        "screenshot_ref": unit.get("screenshot", ""),
        # Kept so the dashboard can resolve screenshots by structure hash.
        "structure_str": unit.get("structure_str", ""),
        # P0-14 (2026-05-07): byte-equal screenshot 시그널 — semantic_merge 의
        # L0 authoritative override 용. unit 또는 sg_node 어느 쪽에 있어도 보존.
        "screenshot_md5": (
            unit.get("screenshot_md5", "")
            or sg_node.get("screenshot_md5", "")
        ),
        "confidence": analysis.get("confidence", "medium"),
        # 2026-04-30 (Pass 1 → Pass 2 흐름):
        # Stage 3 의 quality fail 화면 (Compose wrapper / dominant WebView /
        # Flutter Canvas-only 등) — primary extractor 가 actionable 추출 못 함.
        # Stage 3 안에서 vision 호출 안 하고 flag 만 남김 — Pass 2 (Stage 5
        # LLM 후 재탐색) 가 ScreenMap 풍부해진 상태에서 batch 호출.
        # 기본 False — 안 마킹된 정상 노드는 영향 없음 (additive).
        "is_provisional": bool(
            unit.get("needs_vision_in_revisit")
            or analysis.get("needs_vision_in_revisit")
            or sg_node.get("needs_vision_in_revisit"),
        ),
        # 2026-04-30 (Bug 1 fix — PLANNABLE 0 의 진짜 원인):
        # walk 의 dynamic capture 노드 (page_* prefix) 는 sg_node 가 비어있고
        # unit/analysis 가 채워짐. 그런데 status 미설정으로 screenmap_serializer 가 default
        # 'declared' 부여 → PLANNABLE (status ∈ {enriched, probed} 요구) 에서 제외.
        # 메가커피 35cbb9a4: 14 page_* 노드 모두 declared → PLANNABLE 0.
        # 분기:
        #   sg_node 만 (wireframe 만 — 미방문 declared activity)        → "declared"
        #   unit/analysis 있음 (walk capture)                   → "probed"
        #   sg_node + unit/analysis (wireframe_merge.py:79 가 enriched 승격) → 그대로
        "status": (
            "probed" if (analysis or unit) and not sg_node else "declared"
        ),
        # 2026-05-01 (C+D 결합 — 캡쳐 보존):
        # title_text = page_id 분리 시 사용된 신호. 같은 structure_str 라도 title
        # 다른 화면은 별 노드 → C 분리 효과 추적용.
        # aliases = 같은 page_id (structure+title) cluster 의 다른 PNG 들. 보통 비어있고,
        # screen_clusterer 가 같은 cluster 안 다른 PNG 변종 보존 시 채워짐 → D 보존.
        "title_text": unit.get("title_text", ""),
        # 화면에 실제로 보이는 텍스트 후보 — Stage 5 grounded 라벨링이 이 중에서 고른다
        "label_candidates": list(unit.get("label_candidates") or [])[:8],
        "aliases": [
            {"screenshot_ref": s} for s in (unit.get("variant_screenshots") or [])
        ],
    }


def _build_widget(element: dict) -> dict:
    out = {
        "id": element.get("widget_id", ""),
        "type": element.get(
            "action_type",
            element.get("action_types", ["click"])[0] if element.get("action_types") else element.get("type", "click"),
        ),
        "role": element.get("role", element.get("content_desc", element.get("description", ""))),
    }
    for key in ("bounds", "bbox", "rect", "text", "label", "content_desc", "resource_id", "class",
                "clickable", "editable", "scrollable", "editable_hint", "action_types"):
        value = element.get(key)
        if value not in (None, "", [], {}):
            out[key] = value
    return out


def _build_edge(sg_edge: dict) -> dict:
    edge = {
        "edge_id": _make_edge_id(
            sg_edge.get("from", ""),
            sg_edge.get("to", ""),
            sg_edge.get("trigger_action", ""),
            sg_edge.get("trigger_widget", ""),
        ),
        "from": sg_edge.get("from", ""),
        "to": sg_edge.get("to", ""),
        "trigger_action": sg_edge.get("trigger_action", ""),
        "trigger_widget": sg_edge.get("trigger_widget", ""),
        "condition": sg_edge.get("condition"),
        "passed_params": sg_edge.get("passed_params", []),
        "returned_params": sg_edge.get("returned_params", []),
        "kind": sg_edge.get("kind", ""),
    }
    trigger_bounds = _parse_trigger_bounds(edge["trigger_widget"])
    if trigger_bounds:
        edge["trigger_bounds"] = trigger_bounds["bounds"]
        edge["trigger_label"] = trigger_bounds["label"]
    return edge


def _parse_trigger_bounds(trigger_widget: str) -> dict | None:
    match = re.match(
        r"^(.*?)@?\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]"
        r"\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]",
        str(trigger_widget or ""),
    )
    if not match:
        return None
    label, x1, y1, x2, y2 = match.groups()
    left, top, right, bottom = map(float, (x1, y1, x2, y2))
    if right <= left or bottom <= top:
        return None
    return {
        "label": label.strip(),
        "bounds": {"left": left, "top": top, "right": right, "bottom": bottom},
    }


def _merge_node(existing: dict, new: dict) -> None:
    for key in (
        "label",
        "functional_role",
        "screen_purpose",
        "node_type",
        "parent_activity_id",
        "host_activity",
        "fragment_class",
    ):
        if new.get(key) and not existing.get(key):
            existing[key] = new[key]
    if new.get("screen_params"):
        existing.setdefault("params", {}).update(new["screen_params"])
    # 2026-05-01: 머지 시 다른 노드의 screenshot_ref 도 aliases 에 흡수 (D 보존).
    # 같은 page_id 인데 PNG 다르면 둘 다 추적 가능. 중복은 제거.
    new_shot = new.get("screenshot_ref") or ""
    if new_shot and new_shot != existing.get("screenshot_ref"):
        existing.setdefault("aliases", [])
        if not any(a.get("screenshot_ref") == new_shot for a in existing["aliases"]):
            existing["aliases"].append({"screenshot_ref": new_shot})


def _find_entry_node(
    nodes: dict[str, dict],
    screen_cards: list[dict],
    entry_activity: str,
) -> str:
    """Pick an entry node in order of decreasing trust:
    1. The manifest's launcher activity (when it maps to an existing node).
    2. A node whose functional_category is 'home'.
    3. A node whose activity string contains main/launcher/splash/home.
    4. The first context unit / node as a last resort.
    """
    def _is_real_screen(node: dict) -> bool:
        return node.get("node_type", "activity") != "activity" or not any(
            child.get("parent_activity_id") == node.get("screen_id", "")
            for child in nodes.values()
        )

    if entry_activity:
        for sid, node in nodes.items():
            if node.get("activity") == entry_activity and _is_real_screen(node):
                return sid
        # Try a relative-name match (".MainActivity" vs "com.x.MainActivity").
        short = entry_activity.rsplit(".", 1)[-1]
        for sid, node in nodes.items():
            act = node.get("activity", "")
            if (act.endswith("." + short) or act == short) and _is_real_screen(node):
                return sid

    for sid, node in nodes.items():
        if node.get("functional_category") == "home" and _is_real_screen(node):
            return sid

    for sid, node in nodes.items():
        if not _is_real_screen(node):
            continue
        activity = node.get("activity", "").lower()
        if any(k in activity for k in ("main", "launcher", "splash", "home")):
            return sid

    if screen_cards:
        for unit in screen_cards:
            sid = unit.get("screen_id", "")
            if sid and sid in nodes and _is_real_screen(nodes[sid]):
                return sid
    for sid, node in nodes.items():
        if _is_real_screen(node):
            return sid
    return next(iter(nodes), "")


def _inject_activity_hosts(
    nodes: dict[str, dict],
    edges: list[dict],
    edge_set: set[tuple],
) -> None:
    fragments = [
        node for node in nodes.values()
        if node.get("node_type") == "fragment" and node.get("parent_activity_id")
    ]

    for fragment in fragments:
        parent_id = fragment.get("parent_activity_id", "")
        host_activity = fragment.get("host_activity") or fragment.get("activity", "")
        if not parent_id or not host_activity:
            continue

        if parent_id not in nodes:
            nodes[parent_id] = _build_activity_host_node(parent_id, host_activity)

        key = (parent_id, fragment["screen_id"], CONTAINS_ACTION, "")
        if key in edge_set:
            continue
        edge_set.add(key)
        edges.append({
            "edge_id": _make_edge_id(parent_id, fragment["screen_id"], CONTAINS_ACTION, ""),
            "from": parent_id,
            "to": fragment["screen_id"],
            "trigger_action": CONTAINS_ACTION,
            "trigger_widget": "",
            "condition": None,
            "passed_params": [],
            "returned_params": [],
            "kind": CONTAINS_EDGE_KIND,
        })


def _build_activity_host_node(screen_id: str, activity: str) -> dict:
    short = activity.rsplit(".", 1)[-1] if activity else screen_id
    return {
        "screen_id": screen_id,
        "activity": activity,
        "node_type": "activity",
        "parent_activity_id": "",
        "host_activity": activity,
        "fragment_class": "",
        "label": short[:50],
        "functional_category": "navigation",
        "screen_purpose": f"Host activity for {short}",
        "params": {"inputs": [], "outputs": [], "displays": []},
        "widgets": [],
        "screenshot_ref": "",
        "structure_str": "",
        "confidence": "low",
    }


def _annotate_edge_kinds(edges: list[dict], nodes: dict[str, dict]) -> None:
    for edge in edges:
        if edge.get("kind") == CONTAINS_EDGE_KIND:
            continue
        edge["kind"] = _infer_edge_kind(edge, nodes)


def _infer_edge_kind(edge: dict, nodes: dict[str, dict]) -> str:
    src = nodes.get(edge.get("from", ""), {})
    dst = nodes.get(edge.get("to", ""), {})

    if (
        src.get("node_type") == "fragment"
        and dst.get("node_type") == "fragment"
        and src.get("parent_activity_id")
        and src.get("parent_activity_id") == dst.get("parent_activity_id")
    ):
        return FRAGMENT_NAV_EDGE_KIND
    return NAVIGATE_EDGE_KIND
