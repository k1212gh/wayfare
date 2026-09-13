"""widget_table — 에이전트 grounding 용 위젯 표 + 셀렉터 (WebView 접근성 트리 특성 반영)."""
from __future__ import annotations

from stage4_screens.widget_table import extract_widget_table, build_selector, find_widget_at, parse_bounds

APP = "co.kr.example.app"


def _v(cls, bounds, text="", desc="", rid="", clickable=False, pkg=APP, **kw):
    d = {"class": f"android.widget.{cls}", "bounds": bounds, "text": text, "content_desc": desc,
         "resource_id": f"{pkg}:id/{rid}" if rid else "", "clickable": clickable, "package": pkg, "visible": True}
    d.update(kw)
    return d


def test_webview_container_gets_child_text_label_and_hint_becomes_editable():
    views = [
        _v("FrameLayout", "[0,0][1440,3000]"),
        _v("TextView", "[51,25][188,100]", text="12:45", pkg="com.android.systemui"),          # 상태바 — 제외
        _v("ImageView", "[413,25][476,100]", desc="쿠팡 알림: ", pkg="com.android.systemui"),  # 상태바 — 제외
        _v("View", "[70,457][1370,614]", clickable=True),                                      # 검색창 컨테이너
        _v("TextView", "[224,511][861,570]", text="매장이나 지역명을 검색해 주세요."),           # 힌트 (클릭 불가)
        _v("View", "[1151,837][1370,1005]", clickable=True),                                   # 버튼 컨테이너
        _v("TextView", "[1151,894][1307,948]", text="전체보기"),
        _v("TextView", "[126,1080][558,1147]", text="화성마도산업단지점"),                       # 정보성 텍스트 리프
        _v("ImageView", "[903,1031][1071,1199]", desc="즐겨찾기", clickable=True),
    ]
    rows = extract_widget_table(views)
    by_label = {w["label"]: w for w in rows}
    assert "12:45" not in by_label and not any("알림" in (w["content_desc"] or "") for w in rows)
    search = by_label["매장이나 지역명을 검색해 주세요."]
    assert search["clickable"] and search["editable"] and search.get("editable_hint") is True
    assert search["bounds"] == [70, 457, 1370, 614] and "input_text" in search["action_types"]
    btn = by_label["전체보기"]
    assert btn["clickable"] and btn["bounds"] == [1151, 837, 1370, 1005] and not btn["editable"]
    assert by_label["즐겨찾기"]["content_desc"] == "즐겨찾기" and by_label["즐겨찾기"]["id"].startswith("desc_")
    assert not by_label["즐겨찾기"]["editable"]     # "찾기" 오탐 방지
    info = by_label["화성마도산업단지점"]
    assert info["action_types"] == [] and not info["clickable"]
    # 귀속된 텍스트 리프는 중복으로 안 실린다
    assert sum(1 for w in rows if w["label"] == "전체보기") == 1
    # 정렬: 라벨 있는 인터랙티브가 먼저
    assert rows[0]["action_types"] and rows[0]["label"]


def test_fullscreen_touch_outside_does_not_steal_labels():
    views = [
        _v("View", "[0,100][1440,3035]", clickable=True, rid="touch_outside"),
        _v("View", "[70,457][1370,614]", clickable=True),
        _v("TextView", "[224,511][861,570]", text="검색"),
    ]
    rows = extract_widget_table(views)
    labels = {w["resource_id"]: w.get("label") for w in rows}
    assert labels["touch_outside"] == ""            # 화면 전체 터치 영역은 라벨 없음
    assert any(w["label"] == "검색" and w["bounds"][1] == 457 for w in rows)


def test_native_edittext_and_resource_ids():
    views = [
        _v("EditText", "[100,300][1300,420]", text="아이디", rid="input_id"),
        _v("Button", "[100,500][1300,600]", text="로그인", rid="btn_login", clickable=True),
    ]
    rows = extract_widget_table(views)
    ids = {w["id"]: w for w in rows}
    assert ids["input_id"]["editable"] and "input_text" in ids["input_id"]["action_types"]
    assert ids["btn_login"]["clickable"] and ids["btn_login"]["class"] == "Button"


def test_build_selector_enriches_from_widgets():
    widgets = extract_widget_table([
        _v("View", "[1151,837][1370,1005]", clickable=True, rid="btn_all"),
        _v("TextView", "[1151,894][1307,948]", text="전체보기"),
        _v("ImageView", "[903,1031][1071,1199]", desc="즐겨찾기", clickable=True),
    ])
    s = build_selector("click 전체보기@[1151,837][1370,1005]", widgets)
    assert s["by"] == "resource_id" and s["resource_id"] == "btn_all" and s["text"] == "전체보기" and s["bounds"] == [1151, 837, 1370, 1005]
    s2 = build_selector("ImageView@[903,1031][1071,1199]", widgets)
    assert s2["by"] == "content_desc" and s2["content_desc"] == "즐겨찾기" and s2["text"] == "즐겨찾기"
    s3 = build_selector("확인@[10,10][20,20]", widgets)
    assert s3["by"] == "text" and s3["text"] == "확인" and "resource_id" not in s3
    s4 = build_selector("View@[1,1][2,2]", [])
    assert s4["by"] == "bounds" and s4["class"] == "View" and s4["text"] == ""


def test_find_widget_at_center_fallback():
    widgets = extract_widget_table([_v("View", "[0,400][1440,700]", clickable=True), _v("TextView", "[100,500][600,600]", text="행 1")])
    assert find_widget_at(widgets, "[200,520][300,560]")["label"] == "행 1"
    assert parse_bounds("[1,2][3,4]") == (1, 2, 3, 4) and parse_bounds([5, 6, 7, 8]) == (5, 6, 7, 8) and parse_bounds("x") is None
