"""ListView detector — 동일 패턴 sibling 묶음을 list_view 으로 마킹.

목적 (2026-04-30, megacoffee evidence):
  e-commerce / 이벤트 / 메뉴 grid 같은 list_view-heavy 화면에서 모든 항목
  반복 클릭 → 같은 detail 패턴 N번 캡쳐 → ScreenMap 노드 폭증 + 시간 낭비.
  대신 "첫 1개만 클릭 → detail 패턴 학습 → 같은 list_view 의 N번째는 score
  대폭 감점". 사용자 표현: "리스트 뷰 1개만 선택해서 일반화".

탐지 신호:
  1. RecyclerView / GridView / ListView / ViewPager 의 직접 자식
  2. WebView 내부 — 같은 parent + 같은 class + 같은 height +
     인접한 idx (sibling pattern). N>=3 이어야 list_view 인정.
  3. ScrollView 내부 같은 자식 패턴 (드물지만 exist).

Caveats (screenatlas_phash_coalesce_caveats 메모리 참조):
  - list_view 의 항목 detail 화면들은 가격/이름만 다른 noise. 단, 옵션
    선택 (size/temp) 은 진짜 다른 노드 — list_view 자식이라 해서 무조건
    합치지 말 것. detail 후 coalesce 은 structure_str + widgets 시그니처
    조합으로.
  - 자식 N=2 는 list_view 아님 (header + content 같은 패턴).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

# RecyclerView 류 — 직접 자식이 list_view items
LIST_CONTAINER_CLASSES = (
    "RecyclerView",
    "GridView",
    "ListView",
    "ViewPager",
    "LazyColumn",
    "LazyVerticalGrid",
    "FlatList",
    "ScrollableTabRow",
)

# 각 list_view 그룹의 최소 자식 수 (header+content 패턴 회피)
MIN_LIST_VIEW_SIZE = 3


def _bounds_height(bounds) -> int:
    """bounds 의 height 를 안전하게 계산. list[int] 또는 '[x1,y1][x2,y2]' string 모두 처리."""
    if isinstance(bounds, list) and len(bounds) >= 4:
        try:
            return int(bounds[3]) - int(bounds[1])
        except (TypeError, ValueError):
            return 0
    if isinstance(bounds, str):
        import re as _re
        m = _re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
        if m:
            return int(m.group(4)) - int(m.group(2))
    return 0


def detect_list_views(views: list[dict]) -> list[dict]:
    """동일 sibling pattern N>=3 자식 묶음을 list_view 으로 마킹.

    Returns: [
        {
            "group_id": "list_123",
            "container_class": "RecyclerView",
            "item_indices": [5, 6, 7, 8],
            "pattern": "recyclerview",  # 또는 "sibling_uniform"
        }, ...
    ]

    각 view 객체에는 mutate 안 함 — score_action 에서 group lookup 만 사용.
    """
    if not views:
        return []
    groups: list[dict] = []

    # 1) 명시적 list container — 그 자식들 모음
    container_indices = [
        i for i, v in enumerate(views)
        if any(c in str(v.get("class", "")) for c in LIST_CONTAINER_CLASSES)
    ]
    for ci in container_indices:
        # 자식 = parent_index 가 ci 인 view (parent_index 가 있으면 사용,
        # 없으면 parent_class 매칭으로 fallback).
        children: list[int] = []
        container = views[ci]
        container_cls = str(container.get("class", "")).split(".")[-1] or "list"
        for i, v in enumerate(views):
            if i == ci:
                continue
            pidx = v.get("parent_index")
            if pidx == ci:
                children.append(i)
                continue
            # fallback — parent_class 매칭
            if pidx is None and container_cls in str(v.get("parent_class", "")):
                children.append(i)
        if len(children) >= MIN_LIST_VIEW_SIZE:
            groups.append({
                "group_id": f"list_{ci}",
                "container_class": container_cls,
                "item_indices": children,
                "pattern": "recyclerview" if "Recycler" in container_cls else "list_container",
            })

    # 2) Sibling uniform pattern — WebView 내부 메뉴/제품 그리드 + native 하단 탭
    # 같은 (parent_index, class, ~height) 묶음이 N>=3 이면 list_view.
    #
    # 2026-05-03 (R1 — F2 좁히기): clickable 무관 sibling 매칭은 parent 가
    # **webview / nav container** 인 경우에만. 이전 09817587 회귀:
    # 메뉴 카테고리 (커피/디카페인) 도 sibling 으로 잡혀서 list_view penalty 너무
    # 강하게 작용 → 메뉴 click 38회 → 6회 감소. parent_class 가 webview 또는
    # nav container hint (BottomNavigation, TabLayout, NavigationBar) 일 때만
    # non-clickable text view sibling 인정.
    NAV_PARENT_HINTS = (
        "webview", "chromewebview", "rncwebview", "rctwebview", "x5webview",
        "bottomnavigation", "tablayout", "tabbar", "navigationbar", "navrail",
    )
    sibling_buckets: dict[tuple, list[int]] = defaultdict(list)
    for i, v in enumerate(views):
        clickable = v.get("clickable")
        text = (v.get("text") or "").strip()
        desc = (v.get("content_desc") or "").strip()
        parent_cls_lower = (v.get("parent_class") or "").lower()
        in_nav_container = any(kw in parent_cls_lower for kw in NAV_PARENT_HINTS)

        if clickable:
            pass  # 기존 동작 (일반 list_view)
        elif (text or desc) and in_nav_container:
            pass  # F2 좁힘: webview 또는 nav container 안 텍스트만 인정
        else:
            continue

        pidx = v.get("parent_index")
        if pidx is None:
            continue
        cls = str(v.get("class", "")).split(".")[-1]
        # bounds 는 list[int] 또는 "[x1,y1][x2,y2]" string 둘 다 가능. 방어적 파싱.
        h = _bounds_height(v.get("bounds")) // 10
        key = (pidx, cls, h)
        sibling_buckets[key].append(i)

    # 위 1) 에서 이미 잡은 인덱스는 제외
    already = {idx for g in groups for idx in g["item_indices"]}

    for (pidx, cls, _h), idxs in sibling_buckets.items():
        idxs = [i for i in idxs if i not in already]
        if len(idxs) < MIN_LIST_VIEW_SIZE:
            continue
        groups.append({
            "group_id": f"sib_{pidx}_{cls or 'view'}",
            "container_class": cls or "?",
            "item_indices": idxs,
            "pattern": "sibling_uniform",
        })

    # 2026-05-02 (F2): group 멤버 view 들에 _list_view_group 마킹.
    # is_actionable 이 nav-tab 같은 non-clickable text view 도 인정하도록.
    # in-place mutation (views list 자체를 변경 — caller 가 같은 list 객체 사용 시 반영).
    for g in groups:
        for idx in g["item_indices"]:
            if 0 <= idx < len(views):
                views[idx]["_list_view_group"] = g["group_id"]

    if groups:
        sizes = [len(g["item_indices"]) for g in groups]
        logger.debug(
            "[list_view] detected %d groups — sizes=%s patterns=%s",
            len(groups), sizes,
            [g["pattern"] for g in groups],
        )
    return groups


def view_to_group(
    list_views: list[dict],
    view_index: int,
) -> dict | None:
    """view_index 가 어느 list_view 그룹에 속하는지 lookup. 없으면 None."""
    for g in list_views:
        if view_index in g["item_indices"]:
            return g
    return None


def list_view_redundancy_penalty(
    view_index: int,
    list_views: list[dict],
    list_view_visit_count: dict[str, int],
) -> float:
    """동일 list_view 의 N번째 클릭에 대한 score 감점.

    정책:
      - 첫 항목 (visited == 0) — score 변경 없음 (RecyclerView 의 -2.0
        기본 페널티는 그대로 받음)
      - visited >= 1 → -3.0 추가 감점 (대폭)
      - visited >= 3 → -6.0 (이미 충분히 봄)

    sampling 효과: list_view 30 항목 → 첫 1개 detail 진입 → 나머지 29 는
    score 매우 낮아 다른 영역 우선.
    """
    g = view_to_group(list_views, view_index)
    if not g:
        return 0.0
    visited = list_view_visit_count.get(g["group_id"], 0)
    if visited == 0:
        return 0.0
    if visited >= 3:
        return -6.0
    return -3.0
