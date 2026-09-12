"""Vision namer — 스크린샷을 보고 화면 이름을 *짓고*, 화면 텍스트 후보에 스냅해 근거를 남긴다.

왜 (2026-09-13, docs/labeling_method_comparison.md):
  같은 34개 화면에서 최종 라벨 정확도 — 텍스트 후보 선택 62~65% (LLM 없는 휴리스틱 65% 와 동률),
  스크린샷 자유 생성 74~82%. 후보 목록에 제목이 없는 오버레이(영수증·날짜 선택·확인창)를
  자유 생성만 맞힌다. 생성 라벨을 후보 텍스트에 스냅해도 정확도는 같으므로 근거성도 유지된다.

동작:
  - 대상: screenshot_ref 가 있고 아직 vision 라벨이 없는 노드 (system: 제외).
  - 노드당 스크린샷 1장 + 짧은 프롬프트 → {"label","category"} (max_tokens 200, 화면당 1~1.5초).
  - 스냅: 라벨과 후보 텍스트가 포함 관계(공백 무시)면 후보 원문으로 교체 → label_source="vision_snapped",
    아니면 생성 라벨 그대로 → label_source="vision". 생성 라벨이 generic/지점명/너무 길면 기존 라벨 유지.
  - 배치마다 저장, cancel.flag 확인. 실패한 노드는 건너뛴다 (뒤이어 label_picker 가 텍스트로 채움).

사용: run_stage5(config, mode="vision_name")  /  env LLM_STAGE5_MODE=vision_name
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from config import PipelineConfig

from .label_picker import _GENERIC, _STORE_NAME, MAX_CANDIDATES, _atomic_write
from .llm_client import create_client, parse_json_response
from .screenmap_annotator import CATEGORY_ENUM, _push_progress, _raise_if_cancelled
from .vision_labeler import _resolve_screenshot

logger = logging.getLogger(__name__)

MAX_LABEL = 24
_SUFFIX = re.compile(r"\s*(화면|페이지|화면입니다|screen|page)\s*$", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"^(page|act|screen|state|node)_[0-9a-f]{6,}$", re.IGNORECASE)

_SYSTEM_PROMPT = (
    "You look at one Android app screenshot and name the screen for a screen-flow map.\n"
    "Answer with the screen's title as a user would call it — the page header if visible, or the title of a "
    "dialog/bottom sheet if one is open on top, otherwise a short descriptive name. Korean, at most 12 characters. "
    "Do not append 화면/페이지. Never use store/branch names, prices, dates, or a single generic button word.\n"
    "Also classify functional_category as one of: " + ", ".join(CATEGORY_ENUM) + ".\n"
    'Respond with strict JSON only: {"label": "<title>", "category": "<enum>"}'
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def snap_to_candidates(label: str, candidates: list[str]) -> str | None:
    """생성 라벨이 화면 텍스트 후보와 겹치면 후보 원문을 돌려준다.

    규칙 (2026-09-13 실측 보정):
      1. 공백 무시 완전 일치 → 그 후보.
      2. 후보가 라벨을 포함 (라벨 ⊂ 후보) → 후보가 라벨보다 3자 이상 길면 의미가 바뀌므로 스냅 안 함
         ("CJ ONE" → "CJ ONE 유의사항" 방지). 허용되면 가장 짧은 후보.
      3. 라벨이 후보를 포함 (후보 ⊂ 라벨) → 후보가 라벨의 절반 이상을 덮어야 함
         ("메가퀵결제 등록" → "등록" 방지). 허용되면 가장 긴 후보.
    """
    l = _norm(label)
    if len(l) < 2:
        return None
    exact, contains_label, inside_label = [], [], []
    for c in candidates:
        c = str(c).strip()
        cn = _norm(c)
        if len(cn) < 2 or len(c) > MAX_LABEL:
            continue
        if cn == l:
            exact.append(c)
        elif l in cn and len(cn) <= len(l) + 3:
            contains_label.append(c)
        elif cn in l and len(cn) * 2 >= len(l) and cn not in _GENERIC:
            inside_label.append(c)
    if exact:
        return exact[0]
    if contains_label:
        return min(contains_label, key=len)
    if inside_label:
        return max(inside_label, key=len)
    return None


def clean_label(raw: str) -> str:
    s = _SUFFIX.sub("", (raw or "").strip()).strip().strip('"\'')
    if not s or s.lower() in _GENERIC or _STORE_NAME.match(s) or _PLACEHOLDER.match(s):
        return ""
    return s[:MAX_LABEL]


def _needs_name(n: dict) -> bool:
    if not n.get("screenshot_ref") or str(n.get("screen_id", "")).startswith("system:"):
        return False
    return n.get("label_source") not in ("vision", "vision_snapped", "llm")


def apply_answer(n: dict, ans: dict) -> bool:
    """모델 응답을 노드에 적용. 라벨이 바뀌었으면 True."""
    label = clean_label(str(ans.get("label") or ""))
    changed = False
    if label:
        cands = [str(c) for c in (n.get("label_candidates") or [])][:MAX_CANDIDATES]
        snapped = snap_to_candidates(label, cands)
        n["label"] = (snapped or label)[:50]
        n["label_source"] = "vision_snapped" if snapped else "vision"
        n["confidence"] = n.get("confidence") or "medium"
        changed = True
    cat = str(ans.get("category") or "").strip().lower()
    if cat in CATEGORY_ENUM:
        n["functional_category"] = cat
    return changed


def name_screens_with_vision(config: PipelineConfig, client=None) -> dict:
    """screen_map.json 의 스크린샷 노드에 비전 라벨을 붙인다. 통계 dict 반환."""
    screenmap_path = config.output_dir / config.screenmap_output_filename
    if not screenmap_path.exists():
        raise FileNotFoundError(f"ScreenMap not found: {screenmap_path}")
    screenmap = json.loads(screenmap_path.read_text(encoding="utf-8"))
    graph = screenmap.get("screen_map", screenmap).get("graph", {})
    nodes = graph.get("nodes", [])
    targets = [n for n in nodes if _needs_name(n)]
    stats = {"total": len(nodes), "targets": len(targets), "named": 0, "snapped": 0, "failed": 0, "no_image": 0}
    if not targets:
        logger.info("[vision_namer] nothing to name")
        return stats

    if client is None:
        client = create_client(
            api_key=config.anthropic_api_key,
            model_screen=config.llm_model_screen,
            model_widget=config.llm_model_widget,
            temperature=config.llm_temperature,
            max_retries=config.llm_max_retries,
        )

    for i, n in enumerate(targets):
        _raise_if_cancelled(config)
        _push_progress(config, f"Vision naming {i + 1}/{len(targets)}")
        ss = _resolve_screenshot(n.get("screenshot_ref", ""), config.tour_dir)
        if not ss or not ss.exists():
            stats["no_image"] += 1
            continue
        try:
            img = ss.read_bytes()
            media = "image/jpeg" if ss.suffix.lower() in (".jpg", ".jpeg") else "image/png"
            txt = client.query_with_image(_SYSTEM_PROMPT, f"screen id: {n['screen_id']}. Name this screen.",
                                          img, image_media_type=media, max_tokens=200)
            try:
                ans = parse_json_response(txt)
            except Exception:  # noqa: BLE001 — JSON 이 아니면 본문을 라벨로
                ans = {"label": txt.strip()[:MAX_LABEL]}
            if apply_answer(n, ans if isinstance(ans, dict) else {}):
                stats["named"] += 1
                stats["snapped"] += int(n.get("label_source") == "vision_snapped")
        except Exception as e:  # noqa: BLE001 — 한 노드 실패가 전체를 막지 않게
            stats["failed"] += 1
            logger.warning("[vision_namer] %s failed: %s", n.get("screen_id"), str(e)[:160])
            # 비전을 못 받는 모델이면 첫 두 노드가 연속 실패한다 → 나머지는 텍스트 피커에 맡기고 중단
            if stats["named"] == 0 and stats["failed"] >= 2:
                logger.warning("[vision_namer] vision model unavailable — stopping, label_picker will fill labels")
                break
        if (i + 1) % 5 == 0:
            _atomic_write(screenmap_path, screenmap)
    _atomic_write(screenmap_path, screenmap)
    logger.info("[vision_namer] %s", stats)
    return stats
