"""Phase 2 P2.1 — Universal Interactive Primitives detector.

각 ScreenMap 노드에 ``node.primitives`` 객체를 채워, LLM agent 가 화면 안에서 무엇을
어떻게 조작할지 reasoning 가능하게 한다. 카테고리 무관 — 9종 universal primitive.

Schema (additive — 기존 ScreenMap schema 깨지지 않음):

  node.primitives = {
    "inputs":   [{"id","kind","placeholder","required"}],   # 텍스트/숫자/날짜 입력
    "toggles":  [{"id","label","default"}],                  # on/off 스위치
    "selectors":[{"id","kind","options","default"}],         # radio/checkbox/dropdown
    "steppers": [{"id","label","minus","plus","display","min","max","default"}],
    "sliders":  [{"id","label","min","max"}],
    "submits":  [{"id","label","outcome_hint"}],             # 확정/저장/전송
    "displays": [{"id","label","value_hint","kind"}],        # 상태 표시
    "list_views": [{"id","label","item_pattern","sort_options","item_count"}],
    "media":    [{"id","label","kind"}]                       # 재생/녹음/촬영
  }

데이터 소스 (선호 순):
  1. raw states (workspace/<tour>/dynamic/states/state_*.json) — class 정보 풍부
  2. screen_cards (workspace/<tour>/analysis/screen_cards.json) — cleaned XML
  3. node.chip_groups / primary_affordances / data_displayed / infinite_scroll
     — 기존 ScreenMap 의 부분 정보
  4. node.widgets id 의 이름 패턴 — 약한 heuristic (best-effort)

Stage 6 transformations 단계의 ``_detect_primitives()`` 로 호출. 호출 후
metadata_refresh 가 actionable/plannable 재계산할 때 primitive 도 신호로 사용.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── 클래스 시그너 ──────────────────────────────────────────

# class 명 (또는 그 일부) → primitive type 매핑.
# RN 의 ReactEditText, Compose 의 ComposeView 같은 framework 변형 포함.
_CLASS_PATTERNS: dict[str, list[str]] = {
    "input": [
        "edittext", "reactedittext", "textinputedittext",
        "autocompletetextview", "searchview",
    ],
    "toggle": [
        "switchcompat", "switch", "togglebutton",
    ],
    "slider": [
        "seekbar", "slider", "ratingbar",
    ],
    "media": [
        "mediacontroller", "playerview", "playerview2",
        "videoview", "playerctlview",
    ],
}

# id (resource_id) 또는 desc 의 키워드 → primitive type. 보조 신호.
_ID_KEYWORDS: dict[str, list[str]] = {
    "input": ["edit", "input", "field", "search", "query", "text"],
    "toggle": ["switch", "toggle", "_on", "_off"],
    "submit": [
        "save", "submit", "send", "ok", "confirm", "done", "apply",
        "add", "create", "post", "publish", "next", "continue",
        "btn_save", "btn_submit", "btn_send", "btn_ok", "btn_confirm",
    ],
    "slider": ["seekbar", "slider", "rating"],
    "media": ["play", "pause", "record", "shutter", "rec_btn"],
}

# label/text 키워드 (한국어 + 영어) → 9 primitive type 분류.
# 모든 primitive type 에 라벨 매핑 — Compose 같은 raw view 빈약 환경에서
# vision_labeler 의 primary_affordances 를 더 정확히 분류 가능.
_LABEL_KEYWORDS_KO_EN: dict[str, list[str]] = {
    "submit": [
        "저장", "확인", "전송", "완료", "보내기", "등록", "신청",
        "save", "submit", "send", "done", "ok", "confirm", "post",
        "apply", "next", "continue",
    ],
    "media": [
        "재생", "일시정지", "녹음", "촬영", "셔터",
        "play", "pause", "record", "shutter", "capture",
    ],
    "toggle": [
        "켜기", "끄기", "사용함", "활성화", "비활성화",
        "enable", "disable", " on ", " off ",
    ],
    "input": [
        "검색", "입력", "메모 작성", "주소 입력", "이름 입력",
        "search", "input", "type", "enter ", "compose",
    ],
    "selector": [
        "선택", "필터", "정렬", "옵션", "분류", "카테고리", "태그",
        "select", "filter", "sort", "option", "category",
        "drop down", "dropdown", "라디오", "체크",
    ],
    "stepper": [
        "수량", "추가", "더하기", "빼기", "감소", "증가",
        "quantity", "qty", "increment", "decrement",
    ],
    "slider": [
        "조절", "슬라이드", "밝기", "볼륨", "범위",
        "slider", "brightness", "volume", "range",
    ],
    "list_view": [
        "목록", "리스트", "결과", "찾기 결과",
        "list", "results", "items", "recyclerview",
    ],
    "display": [
        "총액", "합계", "현재", "표시",
        "total", "subtotal", "current", "display",
    ],
}


def _classify_view_class(view_class: str) -> str | None:
    """view 의 class 문자열 → primitive type. 매칭 없으면 None."""
    cls = (view_class or "").lower()
    for ptype, patterns in _CLASS_PATTERNS.items():
        if any(p in cls for p in patterns):
            return ptype
    return None


def _classify_view_id(rid: str) -> str | None:
    """resource_id 키워드 매칭. 보조 신호."""
    rid = (rid or "").lower()
    if not rid:
        return None
    for ptype, kws in _ID_KEYWORDS.items():
        if any(k in rid for k in kws):
            return ptype
    return None


def _classify_label(text: str) -> str | None:
    """text/desc 의 한국어/영어 키워드 매칭.

    9 primitive type 모두 가능 — Compose 의 vision-derived primary_affordances
    같이 raw class 정보 없는 데이터 분류에 핵심.

    매칭 우선순위 (specific → generic):
      input > selector > stepper > slider > media > toggle > list_view > display > submit (fallback)
    """
    if not text:
        return None
    low = text.lower()
    # 우선순위 순서로 매칭
    priority = ["input", "selector", "stepper", "slider", "media",
                "toggle", "list_view", "display", "submit"]
    for ptype in priority:
        kws = _LABEL_KEYWORDS_KO_EN.get(ptype, [])
        if any(k in low for k in kws):
            return ptype
    return None


# ─── 화면 단위 detector ────────────────────────────────────

def _detect_primitives_for_views(views: list[dict]) -> dict[str, list[dict]]:
    """raw views (state_*.json 의 views 또는 screen_card 의 elements) 에서
    primitive 9종 추출. 가장 정확한 데이터 소스.

    False positive 제어:
      - 같은 elem_id 한 번만
      - 같은 type 노드당 최대 5개 (primary_affordances cap 와 동일)
      - id 키워드 / label 매칭은 clickable 인 view 에만 적용 (RN 의 ReactView*
        같은 컨테이너가 모든 화면에 'send_status' 같은 generic id 갖는 경우 차단)
    """
    out: dict[str, list[dict]] = defaultdict(list)
    seen_ids: dict[str, str] = {}  # id → 이미 분류된 type (중복 방지)
    PER_TYPE_CAP = 5

    for v in views:
        if not isinstance(v, dict):
            continue
        # 모든 type 매칭 cap 도달했으면 더 안 봄 (조기 종료)
        if all(len(out[k]) >= PER_TYPE_CAP for k in
               ("inputs", "toggles", "sliders", "submits", "media")):
            break
        cls = v.get("class", "") or ""
        rid = v.get("resource_id", "") or ""
        text = v.get("text", "") or ""
        desc = v.get("content_desc", "") or ""
        elem_id = rid or text or desc
        if not elem_id:
            continue
        if elem_id in seen_ids:
            continue

        clickable = bool(v.get("clickable"))
        # 1) class 매칭 (가장 신뢰 — clickable 무관)
        ptype = _classify_view_class(cls)
        # 2) id 키워드 fallback — clickable 일 때만 (false positive 방지)
        if not ptype and clickable:
            ptype = _classify_view_id(rid)
        # 3) label 키워드 fallback — clickable 일 때만
        if not ptype and clickable:
            ptype = _classify_label(text or desc)

        if not ptype:
            continue
        # type 별 cap 도달 시 skip
        target_key = {
            "input": "inputs", "toggle": "toggles", "slider": "sliders",
            "submit": "submits", "media": "media",
        }.get(ptype)
        if target_key and len(out[target_key]) >= PER_TYPE_CAP:
            continue

        # primitive 별 메타데이터
        item: dict[str, Any] = {"id": elem_id}
        label = text or desc
        if label:
            item["label"] = label[:60]

        if ptype == "input":
            # placeholder / kind 추정
            kind = "text"
            for hint, k in (("phone", "tel"), ("email", "email"),
                            ("password", "password"), ("number", "number"),
                            ("date", "date"), ("time", "time"), ("url", "url"),
                            ("search", "search")):
                if hint in (rid + " " + desc + " " + text).lower():
                    kind = k; break
            item["kind"] = kind
            if desc:
                item["placeholder"] = desc[:60]
            out["inputs"].append(item)
        elif ptype == "toggle":
            item["default"] = bool(v.get("checked", False))
            out["toggles"].append(item)
        elif ptype == "slider":
            out["sliders"].append(item)
        elif ptype == "submit":
            out["submits"].append(item)
        elif ptype == "media":
            kind = "play_pause"
            ll = (label or rid).lower()
            if "record" in ll or "녹음" in label:
                kind = "record"
            elif "shutter" in ll or "촬영" in label:
                kind = "shutter"
            item["kind"] = kind
            out["media"].append(item)

        seen_ids[elem_id] = ptype

    return dict(out)


def _convert_chip_groups(node: dict) -> dict[str, list[dict]]:
    """기존 node.chip_groups (radio/checkbox/dropdown/stepper) 를 selectors/steppers 로."""
    out: dict[str, list[dict]] = defaultdict(list)
    for g in node.get("chip_groups", []) or []:
        if not isinstance(g, dict):
            continue
        gtype = g.get("type", "")
        if gtype == "stepper":
            item = {
                "id": g.get("group_id", ""),
                "label": g.get("group_id", ""),
                "minus": g.get("minus_widget", ""),
                "plus": g.get("plus_widget", ""),
                "display": g.get("display_widget", ""),
                "min": g.get("min", 1),
                "max": g.get("max", 99),
                "default": g.get("default", 1),
            }
            out["steppers"].append(item)
        elif gtype in ("radio", "checkbox", "dropdown"):
            item = {
                "id": g.get("group_id", ""),
                "kind": gtype,
                "options": [
                    {"value": o.get("value", ""), "id": o.get("widget_id", ""),
                     "default": bool(o.get("selected_default"))}
                    for o in (g.get("options") or [])
                ],
            }
            for o in (g.get("options") or []):
                if o.get("selected_default"):
                    item["default"] = o.get("value", "")
                    break
            out["selectors"].append(item)
    return dict(out)


def _convert_list_view_signal(node: dict) -> list[dict]:
    """node.infinite_scroll / scroll_metadata → list_views primitive."""
    if not node.get("infinite_scroll"):
        return []
    sm = node.get("scroll_metadata") or {}
    return [{
        "id": "list_view_main",
        "label": "list",
        "item_pattern": sm.get("item_pattern", "recyclerview"),
        "item_count": sm.get("item_count"),
        "sort_options": sm.get("sort_options", []),
    }]


def _convert_data_displayed(node: dict) -> list[dict]:
    """vision_labeler 가 채운 data_displayed → displays primitive."""
    out = []
    for i, d in enumerate(node.get("data_displayed") or []):
        if not d:
            continue
        kind = "text"
        ds = d.lower()
        if any(k in ds for k in ("₩", "원", "$", "price", "가격")):
            kind = "price"
        elif any(k in ds for k in ("count", "개수", "개", "건")):
            kind = "count"
        elif any(k in ds for k in ("time", "시간", ":")):
            kind = "time"
        out.append({"id": f"display_{i}", "value_hint": d[:40], "kind": kind})
    return out


def _convert_primary_affordances(node: dict) -> dict[str, list[dict]]:
    """primary_affordances 를 9 type 으로 분류.

    Compose 같은 raw view 빈약 환경에서 vision_labeler 결과가 거의 유일한 신호 —
    9 type 다 분류해야 primitive coverage 확보.
    label 매칭 안 되는 건 fallback 으로 submit 으로 (clickable 인 것은 액션 가능).
    """
    out: dict[str, list[dict]] = defaultdict(list)
    PER_TYPE_CAP = 5
    for i, a in enumerate(node.get("primary_affordances") or []):
        if not a or not isinstance(a, str):
            continue
        ptype = _classify_label(a) or "submit"  # fallback
        # type 별 schema 매핑
        target_key = {
            "input": "inputs", "toggle": "toggles", "selector": "selectors",
            "stepper": "steppers", "slider": "sliders", "submit": "submits",
            "display": "displays", "list_view": "list_views", "media": "media",
        }.get(ptype)
        if not target_key or len(out[target_key]) >= PER_TYPE_CAP:
            continue
        item = {"id": f"aff_{i}", "label": a[:60]}
        # type 별 추가 메타
        if target_key == "inputs":
            item["kind"] = "search" if "검색" in a or "search" in a.lower() else "text"
        elif target_key == "selectors":
            item["kind"] = "dropdown"
            item["options"] = []  # vision 만으론 옵션 추출 불가
        elif target_key == "submits":
            item["outcome_hint"] = ""
        out[target_key].append(item)
    return dict(out)


# ─── ScreenMap-level entry ────────────────────────────────────────

def detect_primitives_for_screenmap(screenmap: dict, tour_dir: Path | None = None) -> dict:
    """ScreenMap 모든 노드에 node.primitives 채움. in-place.

    Args:
        screenmap: screen_map wrapper.
        tour_dir: workspace/<tour> 경로. raw states 사용 시 필요.

    Returns:
        {"total_nodes": N, "with_primitives": N, "type_counts": {...}}
    """
    afg = screenmap.get("screen_map") or {}
    graph = afg.get("graph") or screenmap.get("graph") or {}
    nodes = graph.get("nodes", [])

    # raw states 인덱싱 (state_str → views) — 가능하면
    raw_views_by_struct: dict[str, list[dict]] = {}
    if tour_dir:
        states_dir = tour_dir / "dynamic" / "states"
        if states_dir.exists():
            for f in states_dir.glob("state_*.json"):
                try:
                    s = json.loads(f.read_text(encoding="utf-8"))
                    sstr = s.get("structure_str", "")
                    if sstr and sstr not in raw_views_by_struct:
                        raw_views_by_struct[sstr] = s.get("views", []) or []
                except Exception:
                    continue

    type_counts: dict[str, int] = defaultdict(int)
    with_primitives = 0

    for node in nodes:
        prims: dict[str, list[dict]] = {
            "inputs": [], "toggles": [], "selectors": [], "steppers": [],
            "sliders": [], "submits": [], "displays": [], "list_views": [],
            "media": [],
        }

        # 1. raw views 가 있으면 그것부터 — 가장 정확
        sstr = node.get("structure_str", "")
        if sstr and sstr in raw_views_by_struct:
            from_views = _detect_primitives_for_views(raw_views_by_struct[sstr])
            for k, v in from_views.items():
                prims[k].extend(v)

        # 2. chip_groups (radio/stepper 등)
        from_og = _convert_chip_groups(node)
        for k, v in from_og.items():
            prims[k].extend(v)

        # 3. list_views — infinite_scroll 신호
        prims["list_views"].extend(_convert_list_view_signal(node))

        # 4. displays — data_displayed
        prims["displays"].extend(_convert_data_displayed(node))

        # 5. primary_affordances → 9 type 분류 (Compose 같은 raw 빈약 환경 대응)
        from_aff = _convert_primary_affordances(node)
        for k, v in from_aff.items():
            prims[k].extend(v)

        # 6. id 안에서 coalescee (같은 id 가 여러 번 들어왔을 수 있음)
        for k, items in prims.items():
            seen_ids: set[str] = set()
            uniq = []
            for it in items:
                iid = it.get("id", "")
                if iid in seen_ids:
                    continue
                seen_ids.add(iid)
                uniq.append(it)
            prims[k] = uniq

        # 빈 primitive 는 키 자체 제거 — ScreenMap 크기 절약
        prims_filtered = {k: v for k, v in prims.items() if v}
        if prims_filtered:
            node["primitives"] = prims_filtered
            with_primitives += 1
            for k, v in prims_filtered.items():
                type_counts[k] += len(v)

    summary = {
        "total_nodes": len(nodes),
        "with_primitives": with_primitives,
        "type_counts": dict(type_counts),
    }
    logger.info("[primitive_detector] %d/%d nodes have primitives — %s",
                with_primitives, len(nodes), dict(type_counts))
    return summary
