"""popup_items 추출 + popup item exhaustion 단위 테스트.

검증:
- DeskClock 처럼 5개 menu item 가진 overflow popup 에서 5개 모두 잡힘
- bounds 같은 view 중복 시 coalesce
- in_popup parent_class 광범위 매칭 (Material 3 DropdownMenu 등)
- resource-id hint (title/menu/item) 으로 fallback
"""

from __future__ import annotations

from stage3_walk.view_tree_parser import popup_items, detect_popup_menu


def _v(cls: str, parent: str, clickable=True, text="", rid="", bounds="[0,0][100,50]") -> dict:
    return {
        "class": cls, "parent_class": parent, "clickable": clickable,
        "text": text, "resource_id": rid, "bounds": bounds,
    }


def test_deskclock_overflow_5_items_all_detected():
    """DeskClock overflow popup 의 5 item 이 모두 잡혀야 — 사용자 보고된
    'Settings 만 누른다' 의 root cause 확인용."""
    parent = "androidx.compose.ui.platform.DropdownMenu"
    views = [
        _v("LinearLayout", "RootView", clickable=False),  # 메인 화면 (popup 밖)
        _v("ImageView", "Toolbar"),  # overflow 버튼 (popup 밖)
        # 5 menu items — Material 3 DropdownMenu children
        _v("MenuItemImpl", parent, text="Screen saver", bounds="[300,100][500,150]"),
        _v("MenuItemImpl", parent, text="Settings", bounds="[300,160][500,210]"),
        _v("MenuItemImpl", parent, text="Privacy policy", bounds="[300,220][500,270]"),
        _v("MenuItemImpl", parent, text="Send feedback", bounds="[300,280][500,330]"),
        _v("MenuItemImpl", parent, text="Help", bounds="[300,340][500,390]"),
    ]
    items = popup_items(views)
    assert len(items) == 5, f"5 menu items 다 잡혀야 함 — got {len(items)}"
    texts = [i.get("text") for i in items]
    assert "Screen saver" in texts
    assert "Settings" in texts
    assert "Privacy policy" in texts
    assert "Send feedback" in texts
    assert "Help" in texts


def test_legacy_popupmenu_still_works():
    """이전 4 패턴도 여전히 잡혀야 (회귀 방지)."""
    for parent in ["PopupMenu", "ListPopupWindow", "MenuPopupWindow", "DropdownListView"]:
        views = [
            _v("ListView", parent, text="Action 1", bounds="[0,0][100,30]"),
            _v("ListView", parent, text="Action 2", bounds="[0,30][100,60]"),
        ]
        items = popup_items(views)
        assert len(items) == 2, f"{parent}: expected 2 items, got {len(items)}"


def test_coalesce_same_bounds_same_text():
    """같은 bounds + 같은 text 인 view 는 한번만."""
    parent = "PopupMenu"
    views = [
        _v("Item", parent, text="Settings", bounds="[0,0][100,30]"),
        _v("Item", parent, text="Settings", bounds="[0,0][100,30]"),  # 중복
        _v("Item", parent, text="Help",     bounds="[0,30][100,60]"),
    ]
    items = popup_items(views)
    assert len(items) == 2  # Settings, Help


def test_resource_id_hint_fallback():
    """parent_class 가 popup 가 아니어도 resource-id 에 menu/title/item 있으면 잡힘."""
    views = [
        _v("TextView", "RootView", text="Settings", rid="menu_title",
           bounds="[0,0][100,30]"),
        _v("TextView", "RootView", text="Help", rid="action_item",
           bounds="[0,30][100,60]"),
        _v("TextView", "RootView", text="Other", rid="random_field",
           bounds="[0,60][100,90]"),  # title/menu/item 없음 — 제외
    ]
    items = popup_items(views)
    assert len(items) == 2
    texts = [i.get("text") for i in items]
    assert "Settings" in texts
    assert "Help" in texts
    assert "Other" not in texts


def test_non_clickable_excluded():
    """popup 안이라도 non-clickable 은 제외."""
    parent = "PopupMenu"
    views = [
        _v("Item", parent, text="Active", clickable=True),
        _v("Item", parent, text="Disabled", clickable=False),
    ]
    items = popup_items(views)
    assert len(items) == 1
    assert items[0].get("text") == "Active"


def test_empty_when_no_popup():
    """popup 아닌 일반 화면에서는 빈 리스트."""
    views = [
        _v("Button", "RootView", text="Add"),
        _v("Button", "RootView", text="Save"),
    ]
    items = popup_items(views)
    assert items == []


def test_detect_popup_recognizes_dropdownmenu():
    """detect_popup_menu 가 DropdownMenu 도 인식."""
    views = [
        _v("LinearLayout", "RootView", clickable=False),
        _v("MenuItemImpl", "DropdownMenu", text="Screen saver"),
    ]
    # 현재 detect_popup_menu 는 popup_class_kw 만 확인. dropdownmenu 가 매칭되는지.
    # 매칭 안 되면 popup 로 인식 못 해 dialog 로 간주 → dismiss. fix 필요.
    assert detect_popup_menu(views), "DropdownMenu 도 popup 으로 인식해야 함"


# ─── Cross-fragment popup tracking (사용자 보고된 root cause) ─────


def test_popup_items_have_same_action_desc_across_fragments():
    """같은 overflow popup 을 서로 다른 fragment 에서 띄워도 popup item 의
    action_desc 는 동일해야 함 (label + bounds). 이게 tried_popup_items
    cross-fragment 추적의 전제."""
    from stage3_walk.view_tree_readers.xml_view_tree import XMLViewTreeReader

    parent = "DropdownMenu"
    # popup 자체는 같은 절대 좌표에 뜸 (overflow 메뉴는 우상단에 고정)
    settings_in_alarm = _v("MenuItemImpl", parent, text="Settings", bounds="[300,160][500,210]")
    settings_in_clock = _v("MenuItemImpl", parent, text="Settings", bounds="[300,160][500,210]")
    settings_in_timer = _v("MenuItemImpl", parent, text="Settings", bounds="[300,160][500,210]")

    extractor = XMLViewTreeReader()
    desc_a = extractor.get_action_desc(settings_in_alarm)
    desc_c = extractor.get_action_desc(settings_in_clock)
    desc_t = extractor.get_action_desc(settings_in_timer)
    assert desc_a == desc_c == desc_t, (
        "같은 popup 의 같은 item 은 fragment 가 달라도 동일 action_desc — "
        "그래야 cross-fragment tried 추적 가능"
    )


def test_tried_popup_items_set_coalesces_across_fragments():
    """tried_popup_items set 에 등록된 action_desc 가 다른 fragment 에서 다시
    뜬 같은 popup 과 매칭되어 untried 가 정확히 줄어드는 것 검증."""
    from stage3_walk.view_tree_readers.xml_view_tree import XMLViewTreeReader

    extractor = XMLViewTreeReader()
    parent = "DropdownMenu"
    # 5 menu items in popup
    items = [
        _v("MenuItemImpl", parent, text="Screen saver",   bounds="[300,100][500,150]"),
        _v("MenuItemImpl", parent, text="Settings",       bounds="[300,160][500,210]"),
        _v("MenuItemImpl", parent, text="Privacy policy", bounds="[300,220][500,270]"),
        _v("MenuItemImpl", parent, text="Send feedback",  bounds="[300,280][500,330]"),
        _v("MenuItemImpl", parent, text="Help",           bounds="[300,340][500,390]"),
    ]

    # Alarm 탭에서 popup 띄움 — 첫 untried = Screen saver 누름
    tried_global: set[str] = set()
    untried = [it for it in items if extractor.get_action_desc(it) not in tried_global]
    assert len(untried) == 5
    tried_global.add(extractor.get_action_desc(untried[0]))  # Screen saver 시도

    # 다른 fragment (Clock 탭) 로 이동 후 popup 다시 띄움.
    # 같은 popup_items, tried_global 에 Screen saver 이미 있음.
    untried = [it for it in items if extractor.get_action_desc(it) not in tried_global]
    assert len(untried) == 4, "Screen saver 가 fragment 무관 tried 에 들어있어야"
    assert untried[0].get("text") == "Settings"  # 다음 item
    tried_global.add(extractor.get_action_desc(untried[0]))

    # Timer 탭으로 이동 → popup 다시 → Privacy policy 차례
    untried = [it for it in items if extractor.get_action_desc(it) not in tried_global]
    assert len(untried) == 3
    assert untried[0].get("text") == "Privacy policy"

    # 5 fragment 다 돌면 모두 tried — 무한 루프 안 됨
    for _ in range(3):  # 3 more fragments
        untried = [it for it in items if extractor.get_action_desc(it) not in tried_global]
        if untried:
            tried_global.add(extractor.get_action_desc(untried[0]))
    untried = [it for it in items if extractor.get_action_desc(it) not in tried_global]
    assert untried == [], "모든 5 item 시도 후 untried 비어야 — 다음 popup 은 dismiss"
