"""PoG-style task navigator: natural language task → optimal ScreenMap path.

3-stage approach adapted from the Paths-over-Graph (PoG) line of work on
ScreenMap-guided LLM reasoning:
  1. Topic Entity Search — find nodes matching task keywords
  2. Graph Path Search   — Yen's K-shortest simple paths between entry → topic
  3. LLM Ranking         — Claude scores candidate paths by task relevance

This is the only PoC component that explicitly borrows from external
literature; all other components (TapWalker, 3-Level Screen Signature,
Activity Classifier A/B/C, Stage 6 9-step ETL, 5-tier Coalesce Cascade,
Manifest Scan with focus_mismatch tolerance, framework-aware view_tree_readers)
are independently designed.

Note: unrelated to arXiv:2601.17418 despite the similar topic.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# P2.1 (2026-04-29): 한/영 synonym map.
# 모든 비-게임 앱에 공통 적용. 카테고리 무관 — 같은 동사/명사가 다른 앱에서도 의미 동일.
_SYNONYMS: dict[str, list[str]] = {
    # 알림 / 시간 관련
    "알람": ["alarm", "alarms", "시계", "clock", "ringer"],
    "알림": ["notification", "notifications", "alert"],
    "시간": ["time", "clock"],
    "타이머": ["timer", "countdown"],
    "스톱워치": ["stopwatch", "stop watch"],
    "예약": ["reservation", "schedule", "booking"],
    "일정": ["event", "schedule", "appointment"],
    "캘린더": ["calendar"],
    # 검색 / 발견
    "검색": ["search", "find", "lookup", "query"],
    "찾기": ["find", "search"],
    "필터": ["filter", "sort"],
    # 입력 / 선택
    "옵션": ["option", "options", "choice"],
    "선택": ["select", "choose", "pick"],
    "수량": ["quantity", "amount", "count", "qty"],
    "메모": ["note", "memo"],
    "노트": ["note", "notebook"],
    # 통신 / 메시지
    "메시지": ["message", "msg", "chat"],
    "전송": ["send", "submit"],
    "전화": ["call", "phone", "dial"],
    "이메일": ["email", "mail"],
    # 미디어 / 사진
    "사진": ["photo", "picture", "image", "gallery"],
    "영상": ["video", "movie"],
    "음악": ["music", "audio", "song", "track"],
    "재생": ["play", "playback"],
    "녹음": ["record", "recording"],
    # 결제 / 주문 / 쇼핑 (포함하지만 한정 안 함)
    "주문": ["order"],
    "결제": ["pay", "payment", "checkout"],
    "장바구니": ["cart", "basket", "bag"],
    "구매": ["buy", "purchase"],
    "송금": ["transfer", "remit", "send money"],
    # 설정 / 토글
    "설정": ["setting", "settings", "preferences", "config"],
    "켜기": ["enable", "on", "turn on", "activate"],
    "끄기": ["disable", "off", "turn off", "deactivate"],
    "다크모드": ["dark mode", "dark", "night"],
    # 일반 액션
    "추가": ["add", "create", "new", "+"],
    "삭제": ["delete", "remove", "trash"],
    "저장": ["save", "store"],
    "공유": ["share", "send to"],
    "편집": ["edit", "modify", "change"],
    # 위치 / 지도
    "지도": ["map", "maps"],
    "위치": ["location", "place"],
    # 인증 / 로그인 (navigator 가 logout/login 화면 navigate)
    "로그인": ["login", "log in", "sign in", "signin"],
    "로그아웃": ["logout", "log out", "sign out"],
}


def _expand_keywords(task: str) -> list[str]:
    """task 문자열을 (lowercase) word 단위로 자른 뒤 synonym 까지 확장.

    예: "알람 설정" → ["알람", "alarm", "alarms", "시계", "clock", "ringer",
                       "설정", "setting", "settings", "preferences", "config"]
    중복 제거.
    """
    words = task.lower().split()
    expanded: list[str] = []
    seen: set[str] = set()
    for w in words:
        if w in seen:
            continue
        seen.add(w)
        expanded.append(w)
        # 한국어 키 → 영어 synonym
        for syn in _SYNONYMS.get(w, []):
            if syn not in seen:
                seen.add(syn); expanded.append(syn)
        # 영어 단어 → 한국어 키 역매핑
        for ko_key, en_list in _SYNONYMS.items():
            if w in en_list and ko_key not in seen:
                seen.add(ko_key); expanded.append(ko_key)
                for syn in en_list:
                    if syn not in seen:
                        seen.add(syn); expanded.append(syn)
    return expanded


# P2.3 (2026-04-29): task 의 verb (또는 verb-object) → primitive type 매핑.
# "다크모드 켜기" → toggle / "이름 검색" → input(+list_view) / "옵션 선택" → selector
# Phase 3 executor 가 어느 primitive 를 어떻게 조작할지 결정할 때 사용.
_VERB_TO_PRIMITIVE: dict[str, list[str]] = {
    # toggle — on/off
    "켜기": ["toggle"], "끄기": ["toggle"], "활성화": ["toggle"],
    "비활성화": ["toggle"], "사용함": ["toggle"],
    "enable": ["toggle"], "disable": ["toggle"],
    "turn_on": ["toggle"], "turn_off": ["toggle"],
    # input — 텍스트/숫자 입력
    "입력": ["input"], "작성": ["input"], "타이핑": ["input"],
    "search": ["input", "list_view"], "검색": ["input", "list_view"],
    "찾기": ["input", "list_view"], "찾아": ["input", "list_view"],
    "type": ["input"], "input": ["input"],
    # submit — 확정/저장/전송
    "저장": ["submit"], "전송": ["submit"], "보내": ["submit"],
    "확인": ["submit"], "완료": ["submit"], "등록": ["submit"],
    "결제": ["submit"], "주문": ["submit"], "신청": ["submit"],
    "save": ["submit"], "send": ["submit"], "submit": ["submit"],
    "confirm": ["submit"], "checkout": ["submit"], "post": ["submit"],
    # add/create — submit 이지만 list_view 도 (목록에 추가)
    "추가": ["submit", "list_view"], "만들기": ["submit"], "새": ["submit"],
    "add": ["submit", "list_view"], "create": ["submit"], "new": ["submit"],
    # selector — 선택
    "선택": ["selector"], "고르": ["selector"], "필터": ["selector"],
    "정렬": ["selector"], "옵션": ["selector"],
    "select": ["selector"], "filter": ["selector"], "sort": ["selector"],
    "choose": ["selector"], "pick": ["selector"],
    # stepper — 수량/숫자 조절
    "수량": ["stepper"], "개수": ["stepper"], "개": ["stepper"],
    "잔": ["stepper"], "병": ["stepper"], "박스": ["stepper"],
    "quantity": ["stepper"], "qty": ["stepper"],
    # slider — 연속 조절
    "조절": ["slider"], "밝기": ["slider"], "볼륨": ["slider"],
    "brightness": ["slider"], "volume": ["slider"],
    # media — 재생/녹음/촬영/시작/정지
    "재생": ["media"], "녹음": ["media"], "촬영": ["media"],
    "찍": ["media"], "시작": ["media", "submit"], "정지": ["media"],
    "일시정지": ["media"],
    "play": ["media"], "record": ["media"], "shutter": ["media"],
    "start": ["media", "submit"], "stop": ["media"], "pause": ["media"],
    # navigate — 이동 (primitive 가 아니라 edge — but navigator 는 통과)
    "이동": [], "가": [], "open": [], "go": [],
    # delete/remove — destructive submit
    "삭제": ["submit"], "제거": ["submit"],
    "delete": ["submit"], "remove": ["submit"],
    # share — submit (전송)
    "공유": ["submit"], "share": ["submit"],
}


def _infer_target_primitives(task: str) -> list[str]:
    """task 자연어 → 어느 primitive type 들이 관여하는지 추정.

    - "다크모드 켜기" → ["toggle"]
    - "메모 검색" → ["input", "list_view"]
    - "사진 공유" → ["submit"]
    - 매칭 안 되면 빈 list (= 일반 navigate task — Phase 3 executor 가 step 보고 결정)
    """
    expanded = _expand_keywords(task)
    types: list[str] = []
    seen: set[str] = set()
    for w in expanded:
        for prim in _VERB_TO_PRIMITIVE.get(w, []):
            if prim not in seen:
                seen.add(prim); types.append(prim)
    return types


def _node_primitive_match(node: dict, target_types: list[str]) -> dict | None:
    """타겟 노드의 primitives 에서 target_types 와 매칭하는 첫 항목 반환.

    Phase 3 executor 가 어느 element 를 어떤 액션으로 조작할지 결정할 때 사용.
    """
    if not target_types:
        return None
    prims = node.get("primitives") or {}
    for ptype in target_types:
        # primitive type → schema key 매핑
        key = {
            "input": "inputs", "toggle": "toggles", "selector": "selectors",
            "stepper": "steppers", "slider": "sliders", "submit": "submits",
            "display": "displays", "list_view": "list_views", "media": "media",
        }.get(ptype, ptype + "s")
        items = prims.get(key) or []
        if items:
            return {"primitive_type": ptype, "primitive_key": key,
                    "candidate": items[0], "alternatives": len(items)}
    return None


def plan_task(screenmap: dict, task: str, llm_client=None) -> dict:
    """Plan a path through the ScreenMap for a natural language task.

    Args:
        screenmap: screen_map.json content
        task: natural language task (e.g., "알림 설정 끄기")
        llm_client: optional LLM for path ranking

    Returns:
        dict with planned path, steps, and reasoning
    """
    graph = screenmap.get("screen_map", {}).get("graph", {})
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # Stage 1: Topic entity search — find nodes related to task (synonym 확장)
    topic_nodes = _find_topic_nodes(nodes, task)
    if not topic_nodes:
        # P2.2: 표준화된 failure_reason. 클라이언트가 이걸 보고 적절히 분기.
        return {
            "error": "No relevant screens found for task",
            "failure_reason": "no_topic_match",
            "task": task,
            "expanded_keywords": _expand_keywords(task),
        }

    # Stage 2: Graph path search — find paths between topic nodes
    candidate_paths = _find_candidate_paths(nodes, edges, topic_nodes, graph.get("entry_node", ""))

    if not candidate_paths:
        return {
            "error": "No path found between relevant screens",
            "failure_reason": "topic_found_but_no_path",
            "task": task,
            "topic_nodes": [n["screen_id"] for n in topic_nodes],
        }

    # P2.2: target 이 actionable 인지 검사 — agent 가 step 으로 사용 가능한지
    target_id = topic_nodes[0]["screen_id"]
    target_node = next((n for n in nodes if n.get("screen_id") == target_id), None)
    if target_node is not None:
        is_actionable = bool(
            target_node.get("widgets") or
            target_node.get("primary_affordances") or
            target_node.get("chip_groups")
        )
        if not is_actionable:
            # 경로는 찾았지만 타겟이 실제로 조작 불가 (declared/스크린샷 없음)
            # 막지는 않고 path 는 반환하되 warning 추가
            logger.info("[navigator] target %s not actionable — partial result", target_id)

    # Stage 3: LLM ranking (if client available)
    if llm_client:
        best = _llm_rank_paths(llm_client, candidate_paths, task, nodes)
    else:
        best = candidate_paths[0]  # Shortest path as default

    # Stage 4: Expand steps with option-group actions when the destination
    # node has chip_groups (radio/checkbox/stepper/dropdown). Sprint 2026-04-27.
    enriched_steps = _expand_option_steps(best.get("steps", []), nodes, task, llm_client)

    # P2.3 (2026-04-29): task verb → primitive type 추론, 마지막 step 에
    # target_primitive 첨부. Phase 3 executor 가 어느 element 를 어떻게 조작할지
    # 결정 가능 (예: toggle 이면 tap, input 이면 adb input text, stepper 이면 +/− 반복).
    target_primitive_types = _infer_target_primitives(task)
    target_primitive = None
    if target_primitive_types and target_node is not None:
        target_primitive = _node_primitive_match(target_node, target_primitive_types)
        if target_primitive and enriched_steps:
            # 마지막 step 에 target_primitive 첨부 — 여기서 액션 발생
            enriched_steps[-1]["target_primitive"] = target_primitive

    return {
        "task": task,
        "planned_path": best["path"],
        "steps": enriched_steps,
        "cost": best.get("cost", 0),
        "reasoning": best.get("reasoning", "Shortest path selected"),
        "topic_nodes": [n["screen_id"] for n in topic_nodes],
        "alternatives": len(candidate_paths),
        "target_primitive_types": target_primitive_types,  # 디버깅 + Phase 3 input
    }


def _expand_option_steps(steps: list[dict],
                         nodes: list[dict],
                         task: str,
                         llm_client=None) -> list[dict]:
    """Inject option-group steps when the path lands on a node with chip_groups.

    Strategy: after each navigation step whose destination has chip_groups,
    append placeholder action steps the agent should perform before the next
    navigation. Uses simple keyword match to extract option values from the
    task ("라지" → size=Large, "2잔" → quantity=2). When no value matches,
    leaves the step parametric ({value: "<choose>"}) for the agent.
    """
    node_map = {n["screen_id"]: n for n in nodes}
    out: list[dict] = []
    task_lower = task.lower()

    for step in steps:
        out.append(step)
        target_node = node_map.get(step.get("to", ""), {})
        groups = target_node.get("chip_groups") or []
        for g in groups:
            gtype = g.get("type")
            if gtype in ("radio", "checkbox"):
                # Try to extract a value from the task by keyword match
                chosen = None
                for opt in g.get("options", []):
                    val = (opt.get("value") or "").lower()
                    if val and val in task_lower:
                        chosen = opt["value"]
                        break
                if chosen is None:
                    # Use default if any
                    for opt in g.get("options", []):
                        if opt.get("selected_default"):
                            chosen = opt["value"]
                            break
                out.append({
                    "action": "select",
                    "node": step["to"],
                    "group": g.get("group_id", ""),
                    "value": chosen or "<choose>",
                    "required": bool(g.get("required")),
                })
            elif gtype == "stepper":
                qty = _extract_quantity(task_lower)
                out.append({
                    "action": "set_quantity",
                    "node": step["to"],
                    "group": g.get("group_id", ""),
                    "value": qty if qty is not None else g.get("default", 1),
                    "min": g.get("min", 1),
                    "max": g.get("max", 99),
                    "minus_widget": g.get("minus_widget", ""),
                    "plus_widget": g.get("plus_widget", ""),
                })
            elif gtype == "dropdown":
                out.append({
                    "action": "open_dropdown",
                    "node": step["to"],
                    "group": g.get("group_id", ""),
                    "trigger_widget": g.get("trigger_widget", ""),
                })
    return out


def _extract_quantity(task_lower: str) -> Optional[int]:
    """Extract a leading integer count from common Korean/English task phrasings.

    "2잔", "3개", "two cups", "x 5" → int. Returns None if no clear count.
    """
    import re
    # Korean counters
    m = re.search(r"(\d+)\s*(잔|개|컵|병|봉|박스|세트)", task_lower)
    if m:
        return int(m.group(1))
    # English "x N" / "N cups" / "N items"
    m = re.search(r"(?:x|×)\s*(\d+)", task_lower)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*(cups?|items?|bottles?|servings?)", task_lower)
    if m:
        return int(m.group(1))
    return None


def _find_topic_nodes(nodes: list[dict], task: str) -> list[dict]:
    """Find nodes whose labels/purposes match the task keywords (with synonyms)."""
    # P2.1: synonym 확장 적용 — "알람" 도, "alarm" 도 같은 토픽 매칭
    keywords = _expand_keywords(task)
    # 원래 task 의 exact word 는 가중치 더 (synonym 확장은 보조)
    original_words = set(task.lower().split())

    scored = []
    for node in nodes:
        score = 0.0
        searchable = (
            (node.get("label", "") + " " +
             node.get("screen_purpose", "") + " " +
             node.get("description", "") + " " +
             node.get("activity", "") + " " +
             node.get("functional_category", "")).lower()
        )
        for kw in keywords:
            weight = 1.0 if kw in original_words else 0.6  # synonym 은 0.6
            if kw in searchable:
                score += weight
            # Partial match
            if any(kw in word for word in searchable.split()):
                score += weight * 0.5

        if score > 0:
            scored.append((score, node))

    scored.sort(key=lambda x: -x[0])
    return [n for _, n in scored[:5]]  # Top 5 relevant nodes


def _find_candidate_paths(nodes: list[dict], edges: list[dict],
                          topic_nodes: list[dict], entry_node: str) -> list[dict]:
    """Find paths from entry to topic nodes using BFS."""
    import networkx as nx

    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n["screen_id"])
    for e in edges:
        G.add_edge(e["from"], e["to"], weight=1,
                    action=e.get("trigger_action", ""),
                    element=e.get("trigger_widget", ""))

    paths = []
    # Find paths from entry to each topic node
    start = entry_node or (nodes[0]["screen_id"] if nodes else "")

    for topic in topic_nodes:
        target = topic["screen_id"]
        if start == target:
            continue
        try:
            for path in nx.shortest_simple_paths(G, start, target, weight="weight"):
                steps = []
                for i in range(len(path) - 1):
                    edge_data = G.edges.get((path[i], path[i + 1]), {})
                    steps.append({
                        "from": path[i],
                        "to": path[i + 1],
                        "action": edge_data.get("action", ""),
                        "element": edge_data.get("element", ""),
                    })
                cost = len(path) - 1
                paths.append({
                    "path": path,
                    "steps": steps,
                    "cost": cost,
                    "target_label": topic.get("label", target),
                })
                if len(paths) >= 3:  # Max 3 paths per topic
                    break
        except nx.NetworkXNoPath:
            continue

    # Sort by cost
    paths.sort(key=lambda p: p["cost"])
    return paths[:5]


def _llm_rank_paths(llm_client, paths: list[dict], task: str, nodes: list[dict]) -> dict:
    """Use LLM to rank candidate paths by task relevance."""
    node_map = {n["screen_id"]: n for n in nodes}

    paths_desc = []
    for i, p in enumerate(paths[:3]):
        steps_desc = " → ".join(
            f"{node_map.get(s['from'], {}).get('label', s['from'])} --[{s['action']}]--> {node_map.get(s['to'], {}).get('label', s['to'])}"
            for s in p["steps"]
        )
        paths_desc.append(f"Path {i+1} ({p['cost']} steps): {steps_desc}")

    prompt = f"""Task: {task}

Candidate paths through the app:
{chr(10).join(paths_desc)}

Which path best accomplishes the task? Reply with JSON:
{{"best_path": 1, "reasoning": "why this path"}}"""

    try:
        result = llm_client.query_json(
            "You are a mobile app navigation expert. Pick the best path for the task.",
            prompt,
        )
        idx = result.get("best_path", 1) - 1
        best = paths[min(idx, len(paths) - 1)]
        best["reasoning"] = result.get("reasoning", "")
        return best
    except Exception as e:
        logger.warning("LLM ranking failed: %s", e)
        return paths[0]
