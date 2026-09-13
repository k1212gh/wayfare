"""위젯 표 — 에이전트가 "이 화면에서 무엇을 누를 수 있나"를 지도만 보고 알 수 있게 원본 뷰 계층에서
셀렉터 정보(resource_id / text / content_desc / class / bounds / clickable)를 그대로 싣는다.

왜 (2026-09-13, docs/agent_readiness_plan.md #2):
  기존 `_extract_interactive_widgets_union` 은 cleaned_views(bounds 제거)에서 clickable 플래그가 있는 뷰만 뽑아
  {id, type, role} 로 압축했다. 메가커피 지도에서 30개 화면 중 10개만 위젯을 갖고 텍스트·좌표는 0 이었다.

WebView 앱의 접근성 트리 특성 (메가커피 실측):
  - 눌리는 노드와 글자가 있는 노드가 다르다: 클릭 가능한 `View` 컨테이너(텍스트 없음) 안에 `TextView`(클릭 불가) 가 있다.
    → 컨테이너에 자식 텍스트를 라벨로 귀속(label attribution).
  - 검색창은 EditText 가 아니라 클릭 가능한 `View` + 힌트 TextView("매장이나 지역명을 검색해 주세요.") 로 나온다.
    → 힌트 패턴이면 `editable_hint: true`.
  - 전체 뷰의 절반이 `com.android.systemui`(상태바 알림 아이콘·Edge 패널) 다. → 앱 패키지 외 뷰와 상태바 영역은 버린다.
"""

from __future__ import annotations

import hashlib
import re

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_STATUS_BAR_RATIO = 0.035
_MAX_WIDGETS = 80
_MAX_TEXT = 60
# "즐겨찾기" 의 "찾기" 같은 오탐을 막기 위해 검색/찾기는 앞에 한글이 붙지 않을 때만
_HINT_RE = re.compile(r"((?<![가-힣])검색|(?<![가-힣])찾기|\bsearch\b|입력해\s*주세요|입력하세요|입력해주세요)", re.IGNORECASE)
_SYSTEM_PACKAGES = ("com.android.systemui", "com.sec.android.app.launcher", "com.google.android.inputmethod",
                    "com.samsung.android.honeyboard", "com.android.launcher")


def parse_bounds(b) -> tuple[int, int, int, int] | None:
    if isinstance(b, (list, tuple)) and len(b) >= 4:
        try:
            return int(b[0]), int(b[1]), int(b[2]), int(b[3])
        except (TypeError, ValueError):
            return None
    if isinstance(b, str):
        m = _BOUNDS_RE.match(b)
        if m:
            return tuple(int(x) for x in m.groups())  # type: ignore[return-value]
    return None


def _contains(outer: tuple[int, int, int, int], inner: tuple[int, int, int, int]) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def _area(b: tuple[int, int, int, int]) -> int:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def _short_class(cls: str) -> str:
    return (cls or "").rsplit(".", 1)[-1]


def _strip_rid(rid: str) -> str:
    return rid.split("/", 1)[1] if rid and "/" in rid else (rid or "")


def widget_id_for(w: dict) -> str:
    """안정 id: resource_id > content_desc > label/text > class+bounds."""
    if w.get("resource_id"):
        return w["resource_id"]
    if w.get("content_desc"):
        return "desc_" + hashlib.sha256(w["content_desc"].encode()).hexdigest()[:12]
    txt = w.get("label") or w.get("text") or ""
    if txt:
        return "elem_" + hashlib.sha256(f"{w.get('class', '')}|{txt}".encode()).hexdigest()[:12]
    return "pos_" + hashlib.sha256(f"{w.get('class', '')}|{w.get('bounds')}".encode()).hexdigest()[:12]


def extract_widget_table(views: list[dict], app_package: str = "") -> list[dict]:
    """원본 뷰(bounds·package 포함)에서 위젯 표를 만든다.

    포함: clickable / long_clickable / scrollable / editable 뷰 (+ 자식 텍스트 라벨), EditText,
          그리고 어떤 클릭 컨테이너에도 속하지 않은 텍스트 리프(정보성, clickable=false).
    제외: 앱 외 패키지, 상태바 영역, 보이지 않는 뷰, 화면 밖/면적 0.
    """
    rows: list[dict] = []
    h = 0
    for v in views:
        b = parse_bounds(v.get("bounds"))
        if b:
            h = max(h, b[3])
    top_cut = max(int(h * _STATUS_BAR_RATIO), 60) if h else 60

    for v in views:
        if v.get("visible") is False:
            continue
        pkg = v.get("package") or ""
        if pkg and (pkg in _SYSTEM_PACKAGES or (app_package and pkg != app_package)):
            continue
        b = parse_bounds(v.get("bounds"))
        if not b or _area(b) <= 0 or b[3] <= top_cut:
            continue
        cls = _short_class(v.get("class", ""))
        text = (v.get("text") or "").strip()[:_MAX_TEXT]
        desc = (v.get("content_desc") or v.get("content_description") or "").strip()[:_MAX_TEXT]
        editable = bool(v.get("editable")) or cls in ("EditText", "AutoCompleteTextView", "MultiAutoCompleteTextView")
        interactive = bool(v.get("clickable") or v.get("long_clickable") or v.get("scrollable") or editable)
        rows.append({
            "resource_id": _strip_rid(v.get("resource_id", "")),
            "class": cls,
            "text": text,
            "content_desc": desc,
            "bounds": list(b),
            "_b": b,
            "clickable": bool(v.get("clickable")),
            "long_clickable": bool(v.get("long_clickable")),
            "scrollable": bool(v.get("scrollable")),
            "editable": editable,
            "interactive": interactive,
        })

    # 라벨 귀속: 텍스트 없는 인터랙티브 컨테이너 ← 안에 있는 가장 위쪽 텍스트 (면적이 가장 작은 컨테이너에만 귀속)
    texts = [r for r in rows if (r["text"] or r["content_desc"])]
    containers = sorted([r for r in rows if r["interactive"] and not r["text"] and not r["content_desc"]],
                        key=lambda r: _area(r["_b"]))
    claimed: set[int] = set()
    for c in containers:
        # 이 컨테이너 안의 텍스트 중 아직 다른(더 작은) 컨테이너가 안 가져간 것
        inner = [t for t in texts if id(t) not in claimed and t is not c and _contains(c["_b"], t["_b"])
                 and _area(t["_b"]) < _area(c["_b"])]
        if not inner:
            continue
        # 화면 전체를 덮는 터치 영역(touch_outside 등)은 라벨을 삼키지 않게 — 화면의 60% 이상이면 건너뜀
        if h and _area(c["_b"]) > 0.6 * (max(r["_b"][2] for r in rows) * h):
            continue
        inner.sort(key=lambda t: (t["_b"][1], t["_b"][0]))
        label = inner[0]["text"] or inner[0]["content_desc"]
        c["label"] = label
        for t in inner[:3]:
            claimed.add(id(t))

    out: list[dict] = []
    for r in rows:
        if r["interactive"]:
            keep = True
        else:
            # 정보성 텍스트 리프 — 컨테이너에 귀속됐으면 중복이므로 제외
            keep = bool(r["text"] or r["content_desc"]) and id(r) not in claimed
        if not keep:
            continue
        label = r.get("label") or r["text"] or r["content_desc"]
        w = {
            "resource_id": r["resource_id"], "class": r["class"], "text": r["text"], "content_desc": r["content_desc"],
            "label": label, "bounds": r["bounds"],
            "clickable": r["clickable"] or (r["interactive"] and not r["scrollable"] and not r["editable"]),
            "editable": r["editable"] or bool(_HINT_RE.search(label or "")) and r["interactive"],
            "scrollable": r["scrollable"],
        }
        if w["editable"] and not r["editable"]:
            w["editable_hint"] = True   # WebView 검색창: 클릭 컨테이너 + 힌트 텍스트
        w["id"] = widget_id_for(w)
        actions = []
        if w["clickable"]:
            actions.append("click")
        if r["long_clickable"]:
            actions.append("long_click")
        if w["editable"]:
            actions.append("input_text")
        if w["scrollable"]:
            actions.append("scroll")
        w["action_types"] = actions
        out.append(w)

    # 우선순위: 인터랙티브(라벨 있음) > 인터랙티브 > 텍스트 리프, 같은 급이면 위→아래
    out.sort(key=lambda w: (0 if w["action_types"] and w["label"] else 1 if w["action_types"] else 2, w["bounds"][1], w["bounds"][0]))
    # id 중복 제거 (같은 텍스트 리스트 행은 첫 것만)
    seen: set[str] = set()
    dedup = []
    for w in out:
        if w["id"] in seen:
            continue
        seen.add(w["id"])
        dedup.append(w)
    return dedup[:_MAX_WIDGETS]


def find_widget_at(widgets: list[dict], bounds, text: str = "") -> dict | None:
    """탭 이벤트의 bounds(또는 텍스트)와 맞는 위젯. 정확 일치 > 텍스트 일치 > 중심점 포함(가장 작은 것)."""
    b = parse_bounds(bounds)
    if b:
        for w in widgets:
            if tuple(w.get("bounds") or ()) == b:
                return w
    if text:
        for w in widgets:
            if text in (w.get("label") or "", w.get("text") or "", w.get("content_desc") or ""):
                return w
    if b:
        cx, cy = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
        hits = [w for w in widgets if (pb := parse_bounds(w.get("bounds"))) and pb[0] <= cx <= pb[2] and pb[1] <= cy <= pb[3]]
        if hits:
            return min(hits, key=lambda w: _area(parse_bounds(w["bounds"]) or (0, 0, 0, 0)))
    return None


def build_selector(event_str: str, widgets: list[dict] | None = None) -> dict:
    """탐색 이벤트 문자열("확인@[666,1649][774,1724]")을 셀렉터 객체로. 위젯 표가 있으면 rid/desc/class 보강.

    우선순위(에이전트가 쓸 때): resource_id > content_desc > text > bounds.
    """
    s = (event_str or "").strip()
    s = re.sub(r"^(click|tap|press|longclick|long_click|scroll)\s+", "", s, flags=re.IGNORECASE)
    m = _BOUNDS_RE.search(s)
    bounds = list(map(int, m.groups())) if m else None
    head = s[:m.start()].rstrip("@").strip() if m else s.strip()
    text = "" if re.fullmatch(r"[A-Z][A-Za-z.]*", head or "") else head   # "ImageView" 같은 클래스명은 텍스트가 아님
    cls = head if not text else ""
    sel: dict = {"text": text, "bounds": bounds}
    if cls:
        sel["class"] = cls
    w = find_widget_at(widgets or [], bounds, text) if widgets else None
    if w:
        if w.get("resource_id"):
            sel["resource_id"] = w["resource_id"]
        if w.get("content_desc"):
            sel["content_desc"] = w["content_desc"]
        if w.get("class"):
            sel["class"] = w["class"]
        if not sel["text"] and w.get("label"):
            sel["text"] = w["label"]
        sel["widget_id"] = w.get("id")
    sel["by"] = "resource_id" if sel.get("resource_id") else "content_desc" if sel.get("content_desc") else "text" if sel.get("text") else "bounds"
    return sel
