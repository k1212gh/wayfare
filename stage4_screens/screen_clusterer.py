"""Cluster DroidBot states into pages using structure_str (Wayfare approach)."""

import hashlib
import logging
import re
from collections import defaultdict

logger = logging.getLogger(__name__)


# 2026-05-01 (C+D 결합): title text 까지 page_id 에 포함시켜 같은 structure 라도
# 카테고리 다른 화면 (커피 vs 디카페인 메뉴) 을 별 노드로 분리. webview 앱에서
# 같은 RecyclerView 레이아웃이지만 화면 의미는 다른 케이스 보존.
#
# 시스템 bar (시간 / 통신사 / 배터리) 와 의미 없는 짧은 text 는 제외.
_SYSTEM_BAR_KEYWORDS = (
    "T-Mobile", "Battery", "Wi-Fi", "Wifi", "signal", "percent",
    "AT&T", "Verizon", "Sprint", "bars", "bar",
    # 2026-09-12 (SM-S908N 실측): status bar 의 알림 아이콘 desc 가 제목 후보 1순위로 잡힘
    "알림:", "알림", "Android 시스템", "notification", "Edge 패널", "Edge panel",
)
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}(\s*[AP]M)?$")
# 화면 높이 대비 status bar / 상단 제목 영역 비율. 고정 250px 은 1080p 기준이라
# 1440×3088 폰에서는 제목("매장 정보" y=287) 이 잘렸다 (2026-09-12).
_STATUS_BAR_RATIO = 0.035   # 이 위쪽은 status bar 로 보고 무시
_TITLE_ZONE_RATIO = 0.15    # 이 안쪽 텍스트만 제목 후보
_TITLE_MIN_Y = 250          # 작은 해상도 폴백
# 제목이 되기 어려운 버튼/내비 문구 — 후보 순서를 뒤로 미룬다 (제외는 안 함)
_GENERIC_TEXTS = {
    "이전", "뒤로", "닫기", "취소", "확인", "새로고침", "추가", "더보기", "전체", "홈", "메뉴",
    "back", "close", "cancel", "ok", "confirm", "refresh", "add", "more", "home", "menu", "search", "검색",
}


def _screen_height(views: list[dict]) -> int:
    """view bounds 의 최대 y2 로 화면 높이 추정 (없으면 0)."""
    h = 0
    for v in views:
        b = v.get("bounds")
        if isinstance(b, str):
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
            if m:
                h = max(h, int(m.group(4)))
        elif isinstance(b, list) and len(b) >= 4:
            try:
                h = max(h, int(b[3]))
            except (TypeError, ValueError):
                pass
    return h


def _is_noise_text(text: str) -> bool:
    if not text or len(text) < 2 or len(text) > 40:
        return True
    if not re.search(r"[A-Za-z가-힣぀-ヿ一-鿿]", text):
        return True   # "/3", "1/10", "···" 같은 글자 없는 조각
    if _TIME_RE.match(text):
        return True
    if any(kw.lower() in text.lower() for kw in _SYSTEM_BAR_KEYWORDS):
        return True
    return False


def extract_label_candidates(state: dict, k: int = 8) -> list[str]:
    """화면에 실제로 보이는 텍스트 중 라벨 후보 k개 (위→아래, 왼→오른 순, 중복 제거).

    LLM 은 이 후보 중 하나를 '고르기만' 하고(환각 없음), LLM 이 없으면 첫 후보를 라벨로 쓴다.
    후보 순서: 제목 영역(상단 15%) 의 TextView text 우선 → 그 아래 텍스트.
    """
    # view_tree_cleaner 가 bounds 를 지우므로 좌표가 필요한 여기서는 원본 views 를 쓴다
    # (cleaned_views 만 쓰면 모든 뷰가 y=9999 로 탈락 → 제목이 항상 빈 문자열이던 원인).
    views = state.get("views") or state.get("cleaned_views") or []
    h = _screen_height(views)
    top_cut = max(int(h * _STATUS_BAR_RATIO), 60) if h else 60
    title_cut = max(int(h * _TITLE_ZONE_RATIO), _TITLE_MIN_Y) if h else _TITLE_MIN_Y
    rows: list[tuple[int, int, int, str]] = []
    for v in views:
        y1 = _parse_y1(v.get("bounds"))
        if y1 == 9999 or y1 < top_cut:
            continue
        text = (v.get("text") or "").strip()
        is_text = bool(text)
        if not text:
            text = (v.get("content_desc") or "").strip()
        if _is_noise_text(text):
            continue
        zone = 0 if y1 <= title_cut else 1
        x1 = 0
        b = v.get("bounds")
        if isinstance(b, str):
            m = re.match(r"\[(\d+),", b)
            x1 = int(m.group(1)) if m else 0
        # (영역, 버튼성 문구 뒤로, 실제 text 우선, y, x)
        generic = 1 if text.strip().lower() in _GENERIC_TEXTS else 0
        longish = 1 if len(text) > 16 else 0   # 문장형 안내문보다 짧은 제목 우선
        rows.append((zone, generic, longish, 0 if is_text else 1, y1, x1, text))
    rows.sort()
    out: list[str] = []
    seen: set[str] = set()
    for _z, _g, _l, _t, _y, _x, text in rows:
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= k:
            break
    return out


def _parse_y1(bounds) -> int:
    """bounds 의 y1 좌표를 list 또는 '[x1,y1][x2,y2]' string 둘 다에서 추출."""
    if isinstance(bounds, list) and len(bounds) >= 2:
        try:
            return int(bounds[1])
        except (TypeError, ValueError):
            return 9999
    if isinstance(bounds, str):
        m = re.match(r"\[(\d+),(\d+)\]", bounds)
        if m:
            return int(m.group(2))
    return 9999


def _extract_title(state: dict, max_y: int = 250) -> str:
    """state 의 상단 (y < max_y) 에서 의미있는 text 1개 추출.

    필터:
      - 시스템 bar (시간 HH:MM, T-Mobile, Battery, Wifi 등) 제외
      - 길이 2~40 자
      - text 또는 content_desc

    제목 같은 화면 → 같은 page_id, 제목 다른 화면 → 별 page_id (C 분리).
    """
    views = state.get("views") or state.get("cleaned_views") or []   # bounds 필요 → 원본
    h = _screen_height(views)
    # 2026-09-12: 고정 250px → 화면 높이 비율. status bar 영역은 제외.
    top_cut = max(int(h * _STATUS_BAR_RATIO), 60) if h else 60
    if h:
        max_y = max(int(h * _TITLE_ZONE_RATIO), max_y)
    candidates: list[tuple[int, str]] = []
    for v in views:
        y1 = _parse_y1(v.get("bounds"))
        if y1 < top_cut or y1 > max_y:
            continue
        text = (v.get("text") or "").strip() or (v.get("content_desc") or "").strip()
        if _is_noise_text(text):
            continue
        candidates.append((1 if text.strip().lower() in _GENERIC_TEXTS else 0, 1 if len(text) > 16 else 0, y1, text))
    candidates.sort()
    return candidates[0][3] if candidates else ""


def cluster_screens_to_pages(
    states: list[dict],
    transitions: list[dict],
) -> list[dict]:
    """Cluster states by structure_str into logical pages.

    Wayfare insight: Same UI structure (ignoring text content) = same "page".
    Multiple DroidBot states with different data but same layout become one page.

    Returns list of page dicts, each containing:
      - page_id: structure_str hash (SHA256[:12])
      - state_strs: list of DroidBot state_strs in this cluster
      - activity: foreground activity
      - node_type / parent_activity_id: hierarchy metadata for stage 6
      - elements: interactive elements (union across all states in cluster)
    """
    # Coalescelicate states by state_str before clustering
    seen_strs: set[str] = set()
    unique_screens: list[dict] = []
    for state in states:
        ss = state.get("state_str", "")
        if ss and ss in seen_strs:
            continue
        if ss:
            seen_strs.add(ss)
        unique_screens.append(state)

    if len(unique_screens) < len(states):
        logger.info("Coalesceed %d → %d unique states before clustering", len(states), len(unique_screens))

    # Group by canonical_id (Stage 3's 3-level hash result), falling back to
    # structure_str when canonical_id is absent (older runs / synthesized states).
    # Without this, Stage 4 throws away pHash/GNN merges done in Stage 3 and
    # re-splits clusters using only the L1 structural hash.
    groups: dict[str, list[dict]] = defaultdict(list)
    for state in unique_screens:
        key = state.get("canonical_id") or state.get("structure_str", "")
        if not key:
            key = _fallback_structure_hash(state)
            logger.warning(
                "Missing canonical_id and structure_str for state %s, using fallback hash",
                state.get("state_str", "?")[:16],
            )
        groups[key].append(state)

    pages = []
    for cluster_key, group in groups.items():
        # Deterministic representative — min by structure_str so page_id stays
        # stable across runs even when group[0] order shifts.
        representative = min(
            group,
            key=lambda s: (s.get("structure_str") or "", s.get("state_str") or ""),
        )
        structure_str = representative.get("structure_str", "") or cluster_key

        # 2026-05-01 (C+D 결합):
        # page_id seed = structure_str + title — 같은 레이아웃이지만 다른 카테고리/
        # 의미 화면을 별 노드로. title 빈 문자열이면 fallback 으로 structure_str
        # 만 사용 (기존 동작 유지).
        title = _extract_title(representative)
        seed = structure_str + ("|" + title if title else "")
        page_id = f"page_{hashlib.sha256(seed.encode()).hexdigest()[:12]}"

        # 2026-09-13: 위젯 표 — 원본 views(bounds·package) 로 셀렉터 정보를 그대로 싣는다 (widget_table.py).
        # 대표 상태를 기본으로, 군집의 다른 상태에만 있는 인터랙티브 위젯을 id 기준으로 합친다.
        elements = _extract_widget_table_union(group, representative)

        # variant_screenshots — 같은 page_id 이지만 다른 PNG 들 보존 (D 부분).
        # screenmap_builder 가 노드 머지 시 aliases 로 흡수. semantic_merge 와 별개.
        rep_shot = representative.get("processed_screenshot", "") or representative.get("screenshot_path", "")
        variant_shots = []
        seen_shots = {rep_shot} if rep_shot else set()
        for s in group:
            shot = s.get("processed_screenshot", "") or s.get("screenshot_path", "")
            if shot and shot not in seen_shots:
                seen_shots.add(shot)
                variant_shots.append(shot)

        # 2026-09-13 (#4): 오버레이 여부 — 탐색 시 판정 + 확장된 detect_dialog 재판정. 노드까지 전달돼
        # coalesce 가 아래 화면과 합치지 못하게 하고, 관측 전이는 overlay 엣지가 된다.
        try:
            from stage3_walk.view_tree_parser import detect_dialog
            is_dialog = any(bool(s.get("is_dialog")) or detect_dialog(s.get("views") or []) for s in group)
        except Exception:  # noqa: BLE001
            is_dialog = any(bool(s.get("is_dialog")) for s in group)
        page = {
            "page_id": page_id,
            "is_dialog": is_dialog,
            "structure_str": structure_str,
            "title_text": title,
            "label_candidates": extract_label_candidates(representative),
            "state_strs": [s.get("state_str", "") for s in group],
            "activity": representative.get("activity", ""),
            "fragment_class": _select_fragment_class(group),
            "elements": elements,
            "screenshot_path": rep_shot,
            "variant_screenshots": variant_shots,
            "screen_count": len(group),
            # P0-14: representative 의 byte-equal screenshot 시그널 보존.
            # semantic_merge 가 같은 md5 page 끼리 L0 authoritative 머지.
            "screenshot_md5": representative.get("screenshot_md5", ""),
        }
        pages.append(page)

    _annotate_activity_hierarchy(pages)

    # Build page-level transitions
    screen_to_page = {}
    for page in pages:
        for ss in page["state_strs"]:
            screen_to_page[ss] = page["page_id"]

    # FIX: Pre-group transitions by from/to for O(T) instead of O(P*T)
    page_transitions = []
    seen = set()
    for t in transitions:
        from_page = screen_to_page.get(t.get("from_screen", ""), "")
        to_page = screen_to_page.get(t.get("to_screen", ""), "")
        if from_page and to_page:
            key = (from_page, to_page, t.get("event_type", ""))
            if key not in seen:
                seen.add(key)
                page_transitions.append({
                    "from_page": from_page,
                    "to_page": to_page,
                    "event_type": t.get("event_type", ""),
                    "event_str": t.get("event_str", ""),
                })

    # Pre-build lookup dicts for O(1) per page
    outgoing_map: dict[str, list[dict]] = defaultdict(list)
    incoming_map: dict[str, list[dict]] = defaultdict(list)
    for t in page_transitions:
        outgoing_map[t["from_page"]].append(t)
        incoming_map[t["to_page"]].append(t)

    for page in pages:
        page["outgoing_transitions"] = outgoing_map.get(page["page_id"], [])
        page["incoming_transitions"] = incoming_map.get(page["page_id"], [])

    logger.info(
        "Clustered %d states into %d pages, %d page-level transitions",
        len(states), len(pages), len(page_transitions),
    )
    return pages


def _annotate_activity_hierarchy(pages: list[dict]) -> None:
    """Promote same-activity pages into fragment-like children of a host activity."""
    by_activity: dict[str, list[dict]] = defaultdict(list)
    for page in pages:
        activity = page.get("activity", "")
        if activity:
            by_activity[activity].append(page)

    for page in pages:
        activity = page.get("activity", "")
        has_explicit_fragment = bool(page.get("fragment_class"))
        sibling_count = len(by_activity.get(activity, []))
        parent_id = _make_activity_id(activity) if activity and (has_explicit_fragment or sibling_count > 1) else ""

        page["node_type"] = "fragment" if parent_id else "activity"
        page["parent_activity_id"] = parent_id
        page["host_activity"] = activity


def _select_fragment_class(group: list[dict]) -> str:
    """Pick the most-frequent fragment identifier across states in a cluster.

    Accepts either ``fragment_class`` (older state writers) or ``fragment``
    (current TapWalker — capture.py writes this key). Without this
    fallback the field propagates as empty even though every state knows
    its fragment, because the naming drifted between stages.
    """
    counts: dict[str, int] = {}
    for state in group:
        fragment = (state.get("fragment_class") or state.get("fragment") or "").strip()
        if fragment:
            counts[fragment] = counts.get(fragment, 0) + 1

    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _extract_widget_table_union(group: list[dict], representative: dict) -> list[dict]:
    """대표 상태의 위젯 표 + 다른 상태에만 있는 인터랙티브 위젯(id 기준). 각 항목은 widget_table 행 형식."""
    from .widget_table import extract_widget_table
    rows = extract_widget_table(representative.get("views") or [])
    seen = {r["id"] for r in rows}
    for state in group:
        if state is representative:
            continue
        for r in extract_widget_table(state.get("views") or []):
            if r["id"] in seen or not r.get("action_types"):
                continue
            seen.add(r["id"])
            rows.append(r)
    for r in rows:
        r["widget_id"] = r["id"]
    return rows[:100]


def _extract_interactive_widgets_union(group: list[dict]) -> list[dict]:
    """Extract interactive elements from ALL states in a cluster, coalesceed by widget_id."""
    seen_ids: set[str] = set()
    elements = []

    for state in group:
        views = state.get("cleaned_views", state.get("views", []))
        for view in views:
            is_interactive = (
                view.get("clickable")
                or view.get("scrollable")
                or view.get("editable")
                or view.get("long_clickable")
            )
            if not is_interactive:
                continue

            eid = _compute_widget_id(view)
            if eid in seen_ids:
                continue
            seen_ids.add(eid)

            elements.append({
                "widget_id": eid,
                "resource_id": view.get("resource_id", ""),
                "class": view.get("class", ""),
                "text": view.get("text", ""),
                "content_desc": view.get("content_desc", ""),
                "action_types": _get_action_types(view),
            })

    return elements


def _compute_widget_id(view: dict) -> str:
    """Compute a stable element ID from functional attributes (not bounds).

    Priority: resource_id > content_desc > text > class+structural hash
    Uses SHA256[:12] for collision safety.
    """
    rid = view.get("resource_id", "")
    if rid:
        return rid

    cdesc = view.get("content_desc", "")
    if cdesc:
        return f"desc_{hashlib.sha256(cdesc.encode()).hexdigest()[:12]}"

    text = view.get("text", "")
    cls = view.get("class", "")
    raw = f"{cls}|{text}"
    return f"elem_{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def _get_action_types(view: dict) -> list[str]:
    actions = []
    if view.get("clickable"):
        actions.append("click")
    if view.get("long_clickable"):
        actions.append("long_click")
    if view.get("editable"):
        actions.append("input_text")
    if view.get("scrollable"):
        actions.append("scroll")
    return actions


def _make_activity_id(activity: str) -> str:
    if not activity:
        return ""
    short = activity.rsplit(".", 1)[-1]
    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in short).strip("_") or "activity"
    suffix = hashlib.sha256(activity.encode()).hexdigest()[:6]
    return f"act_{slug}_{suffix}"


def _fallback_structure_hash(state: dict) -> str:
    """Generate a structure hash when structure_str is missing."""
    activity = state.get("activity", "")
    views = state.get("cleaned_views", state.get("views", []))
    clickable_ids = sorted(
        v.get("resource_id", "") for v in views if v.get("clickable")
    )
    raw = f"{activity}|{'|'.join(clickable_ids)}"
    return hashlib.sha256(raw.encode()).hexdigest()
