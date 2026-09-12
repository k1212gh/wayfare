"""Grounded label picker — LLM 이 이름을 '짓지' 않고 화면에 보이는 텍스트 후보 중에서 '고른다'.

동기 (2026-09-12):
  - 기존 screenmap_annotate 는 그래프 전체 컨텍스트 + 노드당 7개 필드를 생성 → 비용이 크고
    작은 로컬 모델은 JSON 이 자주 깨진다.
  - 라벨은 대부분 화면 상단에 이미 적혀 있다 ("매장 정보", "결제", "추천메뉴").
    후보 목록(Stage 4 `label_candidates`) 중 번호 하나를 고르게 하면 환각이 없고,
    프롬프트가 짧아 Claude 든 로컬 7B 든 싸고 안정적이다.

동작:
  - 대상: screen_map.json 의 노드 중 label 이 자리표시(page_xxx) 이거나 label_source 가
    'fallback' 인 것. 이미 LLM 라벨이 있으면 건너뜀 (재실행 안전).
  - 배치 20노드, 텍스트만 (스크린샷 없음). 응답: {"nodes":[{"id","pick","label","category"}]}
      pick  : 후보 번호 (0-base). 마땅한 후보가 없으면 -1 + label 에 ≤12자 새 이름.
      category: functional_category enum.
  - 적용: pick 이 유효하면 후보 텍스트를 그대로 라벨로 (모델이 고친 표기는 무시 → grounded).
  - 배치마다 저장, cancel.flag 확인. LLM 없이 돌리면 후보 0번을 그대로 쓰는 것과 동일.

사용: run_stage5(config, mode="grounded")  /  env LLM_STAGE5_MODE=grounded
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from config import PipelineConfig

from .llm_client import create_client
from .screenmap_annotator import CATEGORY_ENUM, _push_progress, _raise_if_cancelled

logger = logging.getLogger(__name__)

BATCH_SIZE = 20
MAX_CANDIDATES = 8
MAX_NEW_LABEL = 14
_PLACEHOLDER = re.compile(r"^(page|act|screen|state|node)_[0-9a-f]{6,}$", re.IGNORECASE)

# 자유 라벨(pick=-1) 허용 여부 — 기본 off. 3B 급 로컬 모델은 자유 라벨에서 "메뉴 메뉴", "취소" 같은
# 잡음을 만든다 (2026-09-12 메가커피 실측). 기본은 후보 중 '고르기만' 하고, 못 고르면 대체 라벨 유지.
ALLOW_FREE_LABEL = os.environ.get("LLM_PICK_ALLOW_FREE", "").lower() in ("1", "true", "yes")
_GENERIC = {"이전", "뒤로", "닫기", "취소", "확인", "새로고침", "추가", "더보기", "전체", "홈", "메뉴", "검색",
            "back", "close", "cancel", "ok", "confirm", "refresh", "add", "more", "home", "menu", "search"}
# 지점명("화성마도산업단지점", "신대방역점")은 제목이 아니다 — 모델이 골라도 거부. 공백 없이 4자 이상 + '점' 으로 끝나는 것만
# (2026-09-12 벤치에서 Qwen3.5 가 매장 선택 화면의 지점명을 pick). "지점"·"매장 정보" 는 걸리지 않음.
_STORE_NAME = re.compile(r"^\S{3,}점$")

_SYSTEM_PROMPT = (
    "You name screens of an Android app for a screen-flow map.\n"
    "For each screen you get: id, activity, and a numbered list of texts that are actually "
    "visible on that screen, ordered top-to-bottom (the first ones are the header area).\n"
    "Pick the ONE candidate that is the screen's title — the page header a user would call this "
    "screen by (e.g. 매장 정보, 결제, 장바구니, 주문내역).\n"
    "Never pick: store/branch names, product names, prices, dates, counts, promotional sentences, "
    "list rows, or generic buttons (이전/닫기/취소/확인/더보기). "
    "When the first candidate already looks like a header, keep pick=0. "
    "If truly none is a title, answer pick=-1.\n"
    "Also classify functional_category as one of: " + ", ".join(CATEGORY_ENUM) + ".\n\n"
    "Respond with strict JSON only (an object with a nodes array):\n"
    '{"nodes": [{"id": "<screen id>", "pick": <int>, "category": "<enum>"}]}'
)


def _needs_label(n: dict) -> bool:
    label = (n.get("label") or "").strip()
    if not label or label == n.get("screen_id") or _PLACEHOLDER.match(label):
        return True
    return n.get("label_source") in (None, "", "fallback", "candidate")


def _short_activity(activity: str) -> str:
    name = (activity or "").rsplit(".", 1)[-1]
    return name[:-8] if name.endswith("Activity") and len(name) > 8 else name


def _build_batch_prompt(batch: list[dict]) -> str:
    lines: list[str] = []
    for n in batch:
        cands = [str(c) for c in (n.get("label_candidates") or [])][:MAX_CANDIDATES]
        lines.append(f"## {n['screen_id']}  (activity: {_short_activity(n.get('activity', ''))})")
        if cands:
            for i, c in enumerate(cands):
                lines.append(f"  [{i}] {c}")
        else:
            lines.append("  (no visible texts)")
        lines.append("")
    return "\n".join(lines)


def _apply(batch: list[dict], resp) -> int:
    by_id = {n["screen_id"]: n for n in batch}
    applied = 0
    # 작은 모델은 {"nodes":[...]} 대신 [...] 나 {"<id>": {...}} 로 답하기도 한다.
    if isinstance(resp, list):
        items = resp
    elif isinstance(resp, dict) and isinstance(resp.get("nodes"), list):
        items = resp["nodes"]
    elif isinstance(resp, dict):
        items = [dict(v, id=k) for k, v in resp.items() if isinstance(v, dict)]
    else:
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        n = by_id.get(str(item.get("id", "")))
        if n is None:
            continue
        cands = [str(c) for c in (n.get("label_candidates") or [])][:MAX_CANDIDATES]
        pick = item.get("pick")
        label = ""
        if isinstance(pick, str) and pick.strip().lstrip("-").isdigit():
            pick = int(pick)
        if isinstance(pick, int) and 0 <= pick < len(cands):
            chosen = cands[pick].strip()
            # 모델이 골랐어도 버튼 문구/긴 문장이면 신뢰하지 않고 대체 라벨 유지
            if chosen.lower() not in _GENERIC and len(chosen) <= 24 and not _STORE_NAME.match(chosen):
                label = chosen
                n["label_source"] = "picked"
        elif (pick == -1 or pick is None) and ALLOW_FREE_LABEL:
            free = str(item.get("label") or "").strip()
            if free and len(free) <= MAX_NEW_LABEL + 6 and free.lower() not in _GENERIC:
                label = free[:MAX_NEW_LABEL]
                n["label_source"] = "llm"
        if label:
            n["label"] = label[:50]
            n["confidence"] = n.get("confidence") or "medium"
            applied += 1
        cat = str(item.get("category") or "").strip().lower()
        if cat in CATEGORY_ENUM and (not n.get("functional_category") or n.get("functional_category") == "other"):
            n["functional_category"] = cat
    return applied


def pick_labels(config: PipelineConfig, client=None) -> dict:
    """screen_map.json 의 라벨 없는 노드에 후보 선택형 라벨을 붙인다. 통계 dict 반환."""
    screenmap_path = config.output_dir / config.screenmap_output_filename
    if not screenmap_path.exists():
        raise FileNotFoundError(f"ScreenMap not found: {screenmap_path}")
    screenmap = json.loads(screenmap_path.read_text(encoding="utf-8"))
    graph = screenmap.get("screen_map", screenmap).get("graph", {})
    nodes = graph.get("nodes", [])

    targets = [n for n in nodes if _needs_label(n) and not str(n.get("screen_id", "")).startswith("system:")]
    stats = {"total": len(nodes), "targets": len(targets), "applied": 0, "batches": 0, "failed_batches": 0}
    if not targets:
        logger.info("[label_picker] nothing to label")
        return stats

    if client is None:
        client = create_client(
            api_key=config.anthropic_api_key,
            model_screen=config.llm_model_screen,
            model_widget=config.llm_model_widget,
            temperature=config.llm_temperature,
            max_retries=config.llm_max_retries,
        )

    for start in range(0, len(targets), BATCH_SIZE):
        _raise_if_cancelled(config)
        batch = targets[start:start + BATCH_SIZE]
        _push_progress(config, f"Label pick {start + 1}-{start + len(batch)}/{len(targets)}")
        try:
            resp = client.query_json(_SYSTEM_PROMPT, _build_batch_prompt(batch), max_tokens=2048)
            stats["applied"] += _apply(batch, resp)
            stats["batches"] += 1
        except Exception as e:  # noqa: BLE001 — 한 배치 실패가 전체를 막지 않게
            stats["failed_batches"] += 1
            logger.warning("[label_picker] batch %d failed: %s", start // BATCH_SIZE, str(e)[:200])
        _atomic_write(screenmap_path, screenmap)

    logger.info("[label_picker] %s", stats)
    return stats


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
