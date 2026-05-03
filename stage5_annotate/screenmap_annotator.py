"""ScreenMap-first LLM enrichment.

Reads the wireframe `screen_map.json` produced by Stage 6 and asks the LLM
to fill in label / screen_purpose / functional_category for each node, using
the rest of the graph as cacheable context.

Why this design:
- Graph structure (nodes + edges) is the same across every batch → can be sent
  once with `cache_control: ephemeral` so Claude re-uses the cached prefix.
- Output is bounded per batch (≤10 nodes) so the JSON fits under Claude's output
  token limit.
- Failure isolation: if any batch fails, the wireframe ScreenMap on disk stays intact
  (we only merge after a successful parse).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from config import PipelineConfig

logger = logging.getLogger(__name__)

BATCH_SIZE = 10


def _push_progress(config: PipelineConfig, detail: str) -> None:
    """Stage 5 detail 을 pipeline_state.json 에 실시간 반영 — frontend 가 polling 으로 본다."""
    try:
        state_path = Path(config.workspace_root) / config.tour_id / "pipeline_state.json"
        d = json.loads(state_path.read_text(encoding="utf-8"))
        d.setdefault("stages", {}).setdefault("stage5", {})["detail"] = detail
        d["updated_at"] = time.time()
        state_path.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _raise_if_cancelled(config: PipelineConfig) -> None:
    """screenmap_annotator batch 사이마다 cancel.flag 감지 시 InterruptedError."""
    flag = Path(config.workspace_root) / config.tour_id / "cancel.flag"
    if flag.exists():
        raise InterruptedError("Stage 5 (screenmap_annotator) cancelled by user")

CATEGORY_ENUM = [
    "home", "list", "detail", "form", "auth", "settings",
    "dialog", "media", "search", "other",
]


def annotate_screenmap(config: PipelineConfig) -> None:
    """Read wireframe ScreenMap → LLM annotates → write back in place."""
    screenmap_path = config.output_dir / config.screenmap_output_filename
    if not screenmap_path.exists():
        logger.warning("No ScreenMap at %s — stage6 must run first; skipping LLM", screenmap_path)
        return

    screenmap = json.loads(screenmap_path.read_text(encoding="utf-8"))
    graph = screenmap.get("screen_map", {}).get("graph", {})
    nodes = graph.get("nodes", [])
    if not nodes:
        logger.warning("ScreenMap has 0 nodes — nothing to annotate")
        return

    # Pull minimal graph summary once, used as cached prefix across every batch
    context_block = _build_kg_context(screenmap, graph)

    # Build client
    from .llm_client import create_client
    client = create_client(
        api_key=config.anthropic_api_key,
        model_screen=config.llm_model_screen,
        model_widget=config.llm_model_widget,
        temperature=config.llm_temperature,
        max_retries=config.llm_max_retries,
    )

    system_prompt = _system_prompt()

    total = len(nodes)
    annotated_count = 0
    edges = graph.get("edges", []) or []
    for i in range(0, total, BATCH_SIZE):
        _raise_if_cancelled(config)  # batch 시작 전 (batch 당 ~10s)
        batch = nodes[i : i + BATCH_SIZE]
        logger.info("Annotating batch %d-%d / %d", i + 1, i + len(batch), total)
        _push_progress(config, f"ScreenMap annotating {i + 1}-{i + len(batch)}/{total}")
        user_prompt = _build_batch_prompt(context_block, batch, all_nodes=nodes, all_edges=edges)
        try:
            # P2.2 (2026-04-29): 8192 — primitive_outcomes + edge_outcomes 추가로
            # 응답 token 늘었음. 4096 이면 JSON 중간에서 truncation → parse fail.
            resp = client.query_json(system_prompt, user_prompt, max_tokens=8192)
        except Exception as e:
            logger.warning("Batch %d failed: %s — skipping", i // BATCH_SIZE, e)
            continue

        annotations = resp.get("annotations", []) if isinstance(resp, dict) else []
        applied = _apply_annotations(nodes, annotations, edges=edges)
        annotated_count += applied

        # Atomic save after each successful batch so partial progress persists
        screenmap_path.write_text(
            json.dumps(screenmap, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    logger.info("LLM enrichment done: %d / %d nodes annotated", annotated_count, total)


# ───────────────────────────────────────────────────────────
# Prompt builders
# ───────────────────────────────────────────────────────────

def _system_prompt() -> str:
    return (
        "You label Android screens in an app-flow screen map for a "
        "MobileGPT-style agent. 노드별로 7 필드를 채워라.\n\n"
        "각 batch 의 노드는 두 종류:\n"
        "  • has_screenshot=true  → vision_labeler 가 이미 라벨함. category/label/purpose 가 "
        "    채워져 있을 수 있음. description/data_displayed/entry_hint 가 비어있다면 "
        "    activity/이웃 정보로 보강.\n"
        "  • has_screenshot=false → 화면 캡처 없음. <b>이런 노드일수록 더 자세히 추론</b>: "
        "    activity FQN, intent_filters, 이웃 노드(어디서 와서/어디로), status, capture_priority 를 "
        "    종합해서 description 을 풍부하게.\n\n"
        f"functional_category: one of {', '.join(CATEGORY_ENUM)}.\n"
        "label: 한국어 또는 영어 짧은 화면 이름 (≤40 chars). raw FQN/ID 금지.\n"
        "screen_purpose: 한 문장 (≤80 chars). 사용자가 여기서 무엇을 하나.\n"
        "description: 2-4문장 (≤240 chars), 한국어. 무엇을 보여주는 화면인지, 어떤 데이터/폼/액션이 "
        "있는지, 사용자 흐름 상 어디 위치하는지. 화면 없는 노드면 activity 이름·intent·이웃 노드 "
        "기반으로 합리적 추정.\n"
        "data_displayed: ≤4 항목, 화면(또는 추정)에 노출되는 데이터 종류. 모르면 빈 배열.\n"
        "entry_hint: 어떻게 이 화면에 도달하는지 한 문장 (≤80). 이웃 incoming 엣지 보고 추론.\n"
        "confidence: 'high'(스크린샷 + 명확) | 'medium' | 'low'(추정 위주).\n"
        "<b>primitive_outcomes</b>: dict (선택). 노드의 primitives 가 보일 때 각 primitive id "
        "(예: 'submits:s_save', 'toggles:t_dark') 키로 'outcome_hint' 한 줄(≤60 chars). "
        "예: {'submits:btn_save': '메모를 저장하고 목록으로 돌아감', 'toggles:dark_switch': "
        "'다크모드 즉시 적용 (앱 전체 색 반전)'}. primitive 없으면 빈 dict.\n"
        "<b>edge_outcomes</b>: list (선택). 노드의 outgoing 엣지마다 {to: target_id, outcome: 한 줄}. "
        "이 액션을 누르면 어떤 변화가 있는지 한 줄 추정. 모르면 빈 list.\n\n"
        'Respond ONLY with valid JSON: {"annotations": [{"screen_id":...}...]}.\n'
        "screen_id 는 요청한 것만. 새로 만들지 말 것."
    )


def _build_kg_context(screenmap: dict, graph: dict) -> str:
    """Compact, cacheable graph summary sent with every batch."""
    app = screenmap.get("screen_map", {})
    meta = app.get("metadata", {}) or {}
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    node_lines = []
    for n in nodes:
        sid = n.get("screen_id", "")
        act = n.get("activity", "") or ""
        short = act.rsplit(".", 1)[-1] if "." in act else act
        node_lines.append(f"- {sid}  [{short}]")

    edge_lines = []
    for e in edges[:300]:  # cap to keep prompt bounded
        edge_lines.append(
            f"- {e.get('from','')} --[{e.get('trigger_action','')}]--> {e.get('to','')}"
        )

    return (
        f"# App: {app.get('app_name','')} ({app.get('package_name','')})\n"
        f"# Entry node: {graph.get('entry_node','')}\n"
        f"# Nodes ({len(nodes)}):\n"
        + "\n".join(node_lines)
        + f"\n\n# Edges ({len(edges)}):\n"
        + "\n".join(edge_lines)
        + "\n"
    )


def _build_batch_prompt(context_block: str, batch: list[dict],
                        all_nodes: list[dict] | None = None,
                        all_edges: list[dict] | None = None) -> str:
    """Batch-specific prompt. context block 은 cacheable, batch detail 은 노드별로 동적.

    각 노드에 이웃 정보 (incoming/outgoing 엣지 + 상대편 노드 label) 를 주입해서
    화면 없는 노드도 graph 위치 기반으로 추론 가능하게.
    """
    nodes = all_nodes or []
    edges = all_edges or []
    by_id = {n.get("screen_id", ""): n for n in nodes}

    def neighbor_label(sid: str) -> str:
        n = by_id.get(sid)
        if not n:
            return sid[:14]
        lbl = n.get("label") or ""
        act = (n.get("activity") or "").rsplit(".", 1)[-1]
        return (lbl or act or sid)[:30]

    batch_detail = []
    for n in batch:
        sid = n.get("screen_id", "")
        act = n.get("activity", "") or ""
        short = act.rsplit(".", 1)[-1] if "." in act else act
        has_ss = bool(n.get("screenshot_ref"))
        status = n.get("status") or "unknown"
        prio = n.get("capture_priority") or ""

        elems = n.get("widgets", []) or []
        elem_lines = []
        for el in elems[:6]:
            eid = el.get("id", "")
            role = el.get("role", "") or el.get("type", "")
            if eid or role:
                elem_lines.append(f"    - {eid}: {role}")

        # incoming / outgoing edges (각 ≤ 3개 sample)
        ins = [e for e in edges if e.get("to") == sid][:3]
        outs = [e for e in edges if e.get("from") == sid][:3]
        in_lines = [
            f"    ← from {neighbor_label(e.get('from',''))} via {e.get('trigger_action','') or e.get('kind','')}"
            for e in ins
        ]
        out_lines = [
            f"    → to {neighbor_label(e.get('to',''))} via {e.get('trigger_action','') or e.get('kind','')}"
            for e in outs
        ]

        # intent_filters / launcher / fragment 등 추가 시그널
        ifs = n.get("intent_filters") or []
        intent_actions = []
        if ifs and isinstance(ifs, list) and isinstance(ifs[0], dict):
            intent_actions = ifs[0].get("actions", [])[:2]
        flags = []
        if n.get("is_launcher"): flags.append("launcher")
        if intent_actions: flags.append(f"intent={','.join(intent_actions)}")
        if n.get("fragment_class"): flags.append(f"fragment={n['fragment_class'].rsplit('.',1)[-1]}")
        if prio: flags.append(f"priority={prio}")

        # 기존 LLM 결과가 있으면 살짝 보여줘서 보강 모드로 동작
        prev_label = n.get("label") or ""
        prev_purpose = n.get("screen_purpose") or ""

        # P2.2 (2026-04-29): primitives 가 채워져 있으면 LLM 에 보여줘서
        # outcome_hint 추론 가능하게. 각 primitive id + label 만 (cap 5/type)
        prim_lines = []
        prims = n.get("primitives") or {}
        for ptype, items in prims.items():
            if not items:
                continue
            sample = []
            for it in items[:3]:
                iid = it.get("id", "")
                lbl = it.get("label", "")
                sample.append(f"{iid}({lbl})" if lbl else iid)
            prim_lines.append(f"    {ptype}: {', '.join(sample)}")

        batch_detail.append(
            f"* {sid}\n"
            f"  activity:        {short}\n"
            f"  has_screenshot:  {str(has_ss).lower()}\n"
            f"  status:          {status}\n"
            + (f"  flags:           {', '.join(flags)}\n" if flags else "")
            + (f"  prev_label:      {prev_label}\n" if prev_label else "")
            + (f"  prev_purpose:    {prev_purpose}\n" if prev_purpose else "")
            + f"  widgets:\n" + ("\n".join(elem_lines) if elem_lines else "    (none — 화면 없음 → graph 위치로 추론)")
            + (f"\n  incoming:\n" + "\n".join(in_lines) if in_lines else "")
            + (f"\n  outgoing:\n" + "\n".join(out_lines) if out_lines else "")
            + (f"\n  primitives:\n" + "\n".join(prim_lines) if prim_lines else "")
        )

    return (
        context_block
        + "\n\n---\nLabel the following nodes (각 노드 7 필드 + primitive_outcomes + edge_outcomes):\n"
        + "\n".join(batch_detail)
        + "\n\nRespond in JSON: "
        '{"annotations": [{"screen_id": "...", "label": "...", '
        '"screen_purpose": "...", "description": "...", "data_displayed": [...], '
        '"entry_hint": "...", "functional_category": "...", "confidence": "...", '
        '"primitive_outcomes": {"submits:btn_save": "..."}, '
        '"edge_outcomes": [{"to": "...", "outcome": "..."}]}, ...]}'
    )


# ───────────────────────────────────────────────────────────
# Merge
# ───────────────────────────────────────────────────────────

def _apply_annotations(nodes: list[dict], annotations: list[dict],
                       edges: list[dict] | None = None) -> int:
    """Merge LLM output into nodes. Only updates if screen_id matches a real node.

    P2.2: primitive_outcomes / edge_outcomes 도 적용 — primitive 별 outcome_hint
    및 edge.outcome 추가.
    """
    by_id = {n.get("screen_id", ""): n for n in nodes}
    edges = edges or []
    applied = 0
    for ann in annotations:
        if not isinstance(ann, dict):
            continue
        sid = ann.get("screen_id", "")
        n = by_id.get(sid)
        if n is None:
            continue
        label = (ann.get("label") or "").strip()
        purpose = (ann.get("screen_purpose") or "").strip()
        cat = (ann.get("functional_category") or "").strip()
        conf = (ann.get("confidence") or "").strip()

        if label:
            n["label"] = label[:50]
        if purpose:
            n["screen_purpose"] = purpose
        if cat in CATEGORY_ENUM:
            n["functional_category"] = cat
        if conf in ("high", "medium", "low"):
            n["confidence"] = conf
        # 새 풍부 필드 (2026-04-27) — vision_labeler 와 동일 schema
        desc = (ann.get("description") or "").strip()
        if desc:
            n["description"] = desc[:280]
        data_disp = ann.get("data_displayed") or []
        if isinstance(data_disp, list) and data_disp:
            n["data_displayed"] = [str(x)[:40] for x in data_disp[:4]]
        entry_hint = (ann.get("entry_hint") or "").strip()
        if entry_hint:
            n["entry_hint"] = entry_hint[:90]

        # P2.2 (2026-04-29): primitive_outcomes — 각 primitive 의 outcome_hint
        # 응답 키 형식: "{primitive_type_plural}:{primitive_id}" e.g. "submits:btn_save"
        prim_outcomes = ann.get("primitive_outcomes") or {}
        if isinstance(prim_outcomes, dict) and prim_outcomes and n.get("primitives"):
            for key, hint in prim_outcomes.items():
                if not isinstance(hint, str) or not hint.strip():
                    continue
                if ":" not in key:
                    continue
                ptype_key, pid = key.split(":", 1)
                items = n["primitives"].get(ptype_key) or []
                for it in items:
                    if it.get("id") == pid:
                        it["outcome_hint"] = hint.strip()[:80]
                        break

        # P2.2: edge_outcomes — outgoing edge 마다 outcome 추가
        edge_outcomes = ann.get("edge_outcomes") or []
        if isinstance(edge_outcomes, list) and edges:
            outgoing = [e for e in edges if e.get("from") == sid]
            for eo in edge_outcomes:
                if not isinstance(eo, dict):
                    continue
                target = eo.get("to", "")
                outcome = (eo.get("outcome") or "").strip()
                if not target or not outcome:
                    continue
                # 매칭되는 edge 찾기 (target 일치)
                for e in outgoing:
                    if e.get("to") == target:
                        e["outcome"] = outcome[:120]
                        break
        applied += 1
    return applied
