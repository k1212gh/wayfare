"""Vision-based screen labeler using Claude 4 multimodal.

Reads nodes that have a screenshot_ref and asks Claude Vision to determine:
- functional_category (home/list/detail/form/auth/settings/media/search/dialog/other)
- screen_purpose (one sentence)
- key_widgets' role (from visible UI)

This complements `screenmap_annotator` which only has XML + graph structure. Vision
sees the actual rendered pixels — critical for Compose/WebView/RN screens where
the XML tree is semantically bare (just ComposeView / ReactViewGroup / WebView).

Cost: ~$0.005 per image (Sonnet 4.6 vision). For a 15-40 shot app, total <$0.20.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from config import PipelineConfig

logger = logging.getLogger(__name__)


def _push_progress(config: PipelineConfig, detail: str) -> None:
    """Stage 5 진행 상황을 pipeline_state.json 에 기록.
    Frontend 가 polling 해서 실시간으로 보여줄 수 있도록 한다.
    실패하면 silent — LLM 진행 방해 금지."""
    try:
        state_path = Path(config.workspace_root) / config.tour_id / "pipeline_state.json"
        d = json.loads(state_path.read_text(encoding="utf-8"))
        d.setdefault("stages", {}).setdefault("stage5", {})["detail"] = detail
        d["updated_at"] = time.time()
        state_path.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _raise_if_cancelled(config: PipelineConfig) -> None:
    """Stage 5 내부에서 cancel.flag 감지 시 InterruptedError 발생.

    pipeline_service 의 outer except 가 InterruptedError → cancel.flag 체크 →
    CANCELLED 상태로 마킹. LLM batch / 노드 단위 사이마다 호출하면 사용자가
    Stop 누른 즉시 (LLM API 응답 한 번 분량 안에) 종료됨.
    """
    flag = Path(config.workspace_root) / config.tour_id / "cancel.flag"
    if flag.exists():
        raise InterruptedError("Stage 5 cancelled by user")

CATEGORY_ENUM = [
    "home", "list", "detail", "form", "auth", "settings",
    "dialog", "media", "search", "other",
]


def label_screens_with_vision(config: PipelineConfig) -> None:
    """Walk ScreenMap nodes with screenshot_ref, send each to Claude Vision.

    Updates in place:
      - functional_category (constrained to enum)
      - screen_purpose
      - label (if LLM produced a better short name)
      - widgets[*].role (from what's visible)
      - confidence

    Skips nodes already labeled with high confidence and non-"other" category.
    """
    screenmap_path = config.output_dir / config.screenmap_output_filename
    if not screenmap_path.exists():
        logger.warning("No ScreenMap at %s — skipping vision labeler", screenmap_path)
        return
    screenmap = json.loads(screenmap_path.read_text(encoding="utf-8"))
    graph = screenmap.get("screen_map", {}).get("graph", {})
    nodes = graph.get("nodes", [])
    if not nodes:
        return

    candidates = [n for n in nodes if _should_label(n)]
    if not candidates:
        logger.info("Vision labeler: no candidate nodes (all already labeled or no screenshots)")
        return
    logger.info("Vision labeler: %d / %d nodes have screenshots to label",
                len(candidates), len(nodes))

    from .llm_client import create_client
    client = create_client(
        api_key=config.anthropic_api_key,
        model_screen=config.llm_model_screen,
        model_widget=config.llm_model_widget,
        temperature=config.llm_temperature,
        max_retries=config.llm_max_retries,
    )

    system_prompt = _system_prompt()
    labeled = 0
    for i, n in enumerate(candidates):
        # 2026-04-29: 매 노드마다 progress push + cancel check (이전엔 5장마다).
        # vision API 가 hang 또는 timeout 시에도 어느 노드인지 frontend 가
        # 즉시 봄. cancel 도 더 빠르게 반응.
        _push_progress(config, f"Vision labeling {i + 1}/{len(candidates)}")
        _raise_if_cancelled(config)

        ss_path = _resolve_screenshot(n.get("screenshot_ref", ""), config.tour_dir)
        if not ss_path or not ss_path.exists():
            continue
        try:
            img_bytes = ss_path.read_bytes()
        except Exception as e:
            logger.debug("Could not read %s: %s", ss_path, e)
            continue

        media = "image/jpeg" if ss_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        user_prompt = _build_user_prompt(n)

        import time as _time
        t_start = _time.time()
        try:
            resp = client.query_with_image(
                system_prompt, user_prompt, img_bytes,
                image_media_type=media, max_tokens=1024,
            )
        except Exception as e:
            elapsed = _time.time() - t_start
            logger.warning(
                "Vision call failed for %s after %.1fs: %s",
                n.get("screen_id"), elapsed, e,
            )
            continue
        elapsed = _time.time() - t_start
        if elapsed > 30:
            logger.warning(
                "Vision call SLOW for %s: %.1fs (>30s) — node %d/%d",
                n.get("screen_id"), elapsed, i + 1, len(candidates),
            )

        ann = _parse_vision_response(resp)
        if ann:
            _apply_vision_annotation(n, ann)
            labeled += 1

        # Persist every 5 labels so partial progress survives
        if labeled and labeled % 5 == 0:
            screenmap_path.write_text(json.dumps(screenmap, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("Vision labeler: %d / %d done (saved)", i + 1, len(candidates))

    # Final save
    screenmap_path.write_text(json.dumps(screenmap, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Vision labeler: %d nodes labeled", labeled)


# ─── Candidate filter ──────────────────────────────────────

def _should_label(node: dict) -> bool:
    if not node.get("screenshot_ref"):
        return False
    if node.get("screen_id") == "system:external_entry":
        return False
    # Re-label if category is 'other' or still the default
    cat = node.get("functional_category", "other")
    has_purpose = bool(node.get("screen_purpose"))
    if cat != "other" and has_purpose:
        return False
    return True


def _resolve_screenshot(ref: str, tour_dir: Path | None = None) -> Path | None:
    if not ref:
        return None
    p = Path(ref)
    if p.is_absolute() and p.exists():
        return p
    # workspace 를 옮긴 뒤 옛 절대경로가 남은 경우: 투어 폴더 안에서 같은 파일명을 찾는다
    if tour_dir is not None:
        for base in ("analysis/screens", "dynamic"):
            root = Path(tour_dir) / base
            if root.exists():
                for cand in root.rglob(p.name):
                    if cand.is_file():
                        return cand
    return None


# ─── Prompts ──────────────────────────────────────────────

def _system_prompt() -> str:
    return (
        "You are an Android-UI labeler for a knowledge-graph pipeline consumed by "
        "a MobileGPT-style agent. Given a single screen screenshot and its "
        "activity metadata, produce structured JSON describing the screen so "
        "downstream tools (graph dashboard, navigator, agent) can reason about it.\n\n"
        "Required fields:\n"
        f"  - functional_category: one of {CATEGORY_ENUM}\n"
        "  - label: 짧고 명확한 한국어 화면 이름 (≤30 chars) — '로그인', '채널 목록', "
        "'알림 설정' 같이 사용자가 부를 만한 이름. raw FQN/ID 금지.\n"
        "  - screen_purpose: 한 문장 (≤80 chars), 한국어. '사용자가 여기서 무엇을 하나'.\n"
        "  - description: 2-3문장 (≤200 chars), 한국어. 화면이 무엇을 보여주고, "
        "어떤 데이터 (목록/폼/미디어) 가 표시되며, 사용자에게 노출되는 핵심 정보.\n"
        "  - primary_affordances: ≤5 클릭 가능 요소. 각 항목은 짧은 한국어 동사구 "
        "('로그인 버튼', '메뉴 열기', '뒤로가기'). icon-only 면 추정 라벨 사용.\n"
        "  - data_displayed: ≤4 항목. 화면에 표시되는 데이터 종류 ('알람 시간', '채널 이름', "
        "'프로필 사진'). 없으면 빈 배열.\n"
        "  - entry_hint: 사용자가 어떻게 이 화면에 도달하는지 한 문장 추정 (≤60 chars).\n"
        "  - confidence: 'high' (선명한 화면) | 'medium' | 'low' (로딩/스캐폴드/애매).\n\n"
        "Respond ONLY with valid JSON, no markdown fences, no preamble.\n"
        "Loading/redirect/empty scaffold → confidence='low' + 그 사실을 description 에 명시."
    )


def _build_user_prompt(node: dict) -> str:
    act = node.get("activity", "") or ""
    short = act.rsplit(".", 1)[-1] if "." in act else act
    elems = node.get("widgets", []) or []
    elem_ids = [e.get("id", "") for e in elems[:10] if e.get("id")]
    intent = ""
    ifs = node.get("intent_filters") or []
    if ifs and isinstance(ifs, list):
        actions = ifs[0].get("actions", []) if isinstance(ifs[0], dict) else []
        if actions:
            intent = ", ".join(actions[:2])
    return (
        f"Activity FQN: {act}\n"
        f"Short name: {short}\n"
        f"UI element IDs present: {elem_ids}\n"
        + (f"Intent actions: {intent}\n" if intent else "")
        + "\nLabel this screen."
    )


# ─── Response parsing / merge ─────────────────────────────

def _parse_vision_response(text: str) -> dict | None:
    import re
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


# 로컬 모델(특히 Qwen3.5)은 라벨 끝에 "화면"/"페이지"를 붙이는 버릇이 있다 (2026-09-12 벤치) — 그래프에선 군더더기.
_LABEL_SUFFIX = re.compile(r"\s*(화면|페이지|화면입니다|screen|page)\s*$", re.IGNORECASE)


def _clean_label(label: str) -> str:
    stripped = _LABEL_SUFFIX.sub("", label).strip()
    return stripped if len(stripped) >= 2 else label


def _apply_vision_annotation(node: dict, ann: dict) -> None:
    cat = (ann.get("functional_category") or "").strip().lower()
    if cat in CATEGORY_ENUM:
        node["functional_category"] = cat
    label = _clean_label((ann.get("label") or "").strip())
    # vision_namer 가 붙인 라벨(전용 프롬프트, 후보 스냅)은 유지 — 여기서는 purpose/description 만 보강
    if label and node.get("label_source") not in ("vision", "vision_snapped"):
        node["label"] = label[:50]
        node["label_source"] = "llm"
    purpose = (ann.get("screen_purpose") or "").strip()
    if purpose:
        node["screen_purpose"] = purpose
    conf = (ann.get("confidence") or "").strip().lower()
    if conf in ("high", "medium", "low"):
        node["confidence"] = conf
    # Attach vision-derived affordances as a separate field (doesn't mangle widgets)
    aff = ann.get("primary_affordances") or []
    if isinstance(aff, list) and aff:
        node["primary_affordances"] = [str(x)[:60] for x in aff[:5]]
    # 새 필드 (2026-04-27, 노드 설명 강화):
    desc = (ann.get("description") or "").strip()
    if desc:
        node["description"] = desc[:240]
    data_disp = ann.get("data_displayed") or []
    if isinstance(data_disp, list) and data_disp:
        node["data_displayed"] = [str(x)[:40] for x in data_disp[:4]]
    entry_hint = (ann.get("entry_hint") or "").strip()
    if entry_hint:
        node["entry_hint"] = entry_hint[:80]
