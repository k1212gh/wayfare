"""Inject walk transitions as ScreenMap edges with coverage-driven remapping.

Extracted from stage6_screenmap/transformations.py (Step 4 세분화).

D-옵션 (2026-04-24): from/to 가 resolve 실패 + _find_matching_node 도 실패 시
환경변수 ``WALK_SYNTH_NODES=1`` 또는 framework 가 react-native/flutter 인
경우 신규 노드를 합성한다. XML 앱은 기존 매칭 로직이 잘 동작하므로 default off.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _should_synthesize_nodes(framework: str | None) -> bool:
    """Synth on/off 결정 — env flag 우선, 그 다음 framework 자동.

    1) WALK_SYNTH_NODES=0 → 강제 off
    2) WALK_SYNTH_NODES=1 → 강제 on
    3) framework ∈ {react-native, flutter} → 자동 on (XML 회귀 방지)
    """
    flag = os.environ.get("WALK_SYNTH_NODES", "auto").lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    return (framework or "").lower() in ("react-native", "rn", "flutter")


def _inject_walk_transitions(graph: dict, transitions: list[dict],
                              screen_cards: list[dict], walk_screens: list[dict],
                              framework: str | None = None) -> None:
    """Inject observed walk transitions into the graph.

    Maps walk IDs to page_ids by matching structure_str. When auto-synth
    is enabled (RN/Flutter or env flag), creates new nodes for unresolvable endpoints.
    """
    # Build structure_str → page_id mapping from context units
    struct_to_page: dict[str, str] = {}
    for cu in screen_cards:
        struct = cu.get("structure_str", "")
        if struct:
            struct_to_page[struct] = cu["screen_id"]
            struct_to_page[struct[:16]] = cu["screen_id"]

    # F 안 (2026-05-12): state_str → page_id 역색인.
    # stage4 cluster_screens_to_pages 가 page.state_strs 에 묶어둔 멤버십을 그대로 사용.
    # 4월 27일 fix (canonical_id 우선) 의 사상을 stage6 까지 cascade.
    state_str_to_page: dict[str, str] = {}
    for cu in screen_cards:
        pid = cu["screen_id"]
        for ss in cu.get("state_strs", []):
            if ss:
                state_str_to_page[ss] = pid

    # Build walk state_str → state mapping (for synthesis)
    exp_to_struct: dict[str, str] = {}
    exp_to_screen: dict[str, dict] = {}
    for s in walk_screens:
        ss = s.get("state_str", "")
        struct = s.get("structure_str", "")
        if ss and struct:
            exp_to_struct[ss] = struct
            exp_to_screen[ss] = s

    synth_enabled = _should_synthesize_nodes(framework)
    # 동일 structure_str 에 대해 한 노드만 합성 (76 collapse 같은 케이스 방지)
    synth_struct_to_page: dict[str, str] = {}
    synth_count = 0

    def synthesize_node(exp_id: str) -> str | None:
        """미해결 transition endpoint를 새 노드로 합성. struct 가 없으면 포기."""
        nonlocal synth_count
        if not synth_enabled:
            return None
        struct = exp_to_struct.get(exp_id, "")
        if not struct:
            return None
        # 같은 struct 가 이미 합성됐으면 재사용 (중복 노드 방지)
        if struct in synth_struct_to_page:
            return synth_struct_to_page[struct]
        # 새 노드 생성 — page_id = SHA256(struct)[:12] (screen_clusterer와 동일 규칙)
        page_id = "syn_" + hashlib.sha256(struct.encode()).hexdigest()[:12]
        st = exp_to_screen.get(exp_id, {})
        new_node = {
            "screen_id": page_id,
            "activity": st.get("activity", "unknown"),
            "label": "",
            "functional_category": "other",
            "screen_purpose": "",
            "params": {"inputs": [], "outputs": [], "displays": []},
            "widgets": [],
            "screenshot_ref": st.get("screenshot_path", ""),
            "confidence": "low",
            "status": "probed",  # 시각으로는 잡혔지만 manifest 에 없음
            "source": "walk_synthesized",  # 회귀 디버깅용
            "fragment_class": st.get("fragment", "") or st.get("fragment_class", ""),
        }
        graph.setdefault("nodes", []).append(new_node)
        synth_struct_to_page[struct] = page_id
        # 신규 노드도 lookup 인덱스에 추가
        struct_to_page[struct] = page_id
        struct_to_page[struct[:16]] = page_id
        node_ids.add(page_id)
        synth_count += 1
        return page_id

    # Combined: walk_id → page_id
    def resolve(exp_id: str) -> str | None:
        # Direct page match
        if exp_id in {n["screen_id"] for n in graph.get("nodes", [])}:
            return exp_id
        # F (2026-05-12): state_str 역색인 우선. stage4 cluster 멤버십 그대로 사용.
        page = state_str_to_page.get(exp_id)
        if page:
            return page
        # Via structure_str (old path — F 매칭 미스 시 backup)
        struct = exp_to_struct.get(exp_id, "")
        if struct:
            page = struct_to_page.get(struct) or struct_to_page.get(struct[:16])
            if page:
                return page
        # By canonical index (step 1 측정용으로 유지 — step 2 에서 제거 예정)
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

        # 3-strategy fallback: existing → matching node → (new) synthesize
        if from_node not in node_ids:
            from_node = _find_matching_node(from_id, node_ids) or synthesize_node(from_id)
        if to_node not in node_ids:
            to_node = _find_matching_node(to_id, node_ids) or synthesize_node(to_id)

        if from_node and to_node and from_node != to_node and (from_node, to_node) not in existing_edges:
            edge_id = f"e_exp_{hashlib.sha256(f'{from_node}|{to_node}'.encode()).hexdigest()[:12]}"
            event_type = t.get("event_type", "click")
            event_str = t.get("event_str", "").replace("click ", "")

            # Determine edge kind: back > overlay > fragment_nav(sibling fragments) > navigate
            # 2026-09-12 (메가커피 실측): 이전 규칙 "같은 activity 면 contains" 는 WebView 앱
            # (거의 모든 페이지가 WebActivity 하나 안) 에서 관측 전이 64건을 전부 구조 엣지로
            # 만들어 뷰어에서 이동 엣지가 9개만 보였다. contains 는 fragment_hierarchy 가
            # 만드는 host→child 포함 관계에만 쓰고, 관측된 페이지 간 이동은 builder 와 같은
            # 규칙(_infer_edge_kind: 같은 부모의 형제 fragment → fragment_nav, 아니면 navigate).
            kind = "navigate"
            if event_type.lower() in ("back", "press_back") or "keycode_back" in event_str.lower():
                kind = "back"
            else:
                from_node_obj = next((n for n in graph.get("nodes", []) if n.get("screen_id") == from_node), {})
                to_node_obj = next((n for n in graph.get("nodes", []) if n.get("screen_id") == to_node), {})
                if to_node_obj.get("functional_category") == "dialog":
                    kind = "overlay"
                elif to_node_obj.get("parent_activity_id") == from_node:
                    kind = "contains"   # host activity → its own fragment: real containment
                else:
                    from .screenmap_builder import _infer_edge_kind
                    kind = _infer_edge_kind({"from": from_node, "to": to_node},
                                            {from_node: from_node_obj, to_node: to_node_obj})

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
    if synth_count:
        logger.info("Synthesized %d new nodes from unresolved transitions (framework=%s)",
                    synth_count, framework)


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


