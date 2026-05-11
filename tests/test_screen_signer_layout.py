"""P0-12: screen_signer layout fallback for low-info webview screens.

Without the layout signature, screens with all-anonymous clickable views
(empty text/desc/rid — typical of WebView-rendered Compose screens like
메가커피 home / 메가오더) collapse to the same canonical → walk gets stall
in BACK→soft_restart loop.

The fallback must:
  - Activate only when a11y info is sparse (≤2 unique a11y entries with 5+
    clickables) — DeskClock / Mattermost (rich rid/desc) unaffected.
  - Be stable: same view set + same Y positions → same hash.
  - Discriminate: different Y bucket distribution → different hash.
"""

from __future__ import annotations

from stage3_walk.screen_signer import ScreenSigner, _parse_bounds_y_top


def _v(cls="View", clickable=True, text="", desc="", rid="", bounds=""):
    return {
        "class": cls,
        "clickable": clickable,
        "scrollable": False,
        "editable": False,
        "text": text,
        "content_desc": desc,
        "resource_id": rid,
        "bounds": bounds,
    }


# ─── _parse_bounds_y_top ────────────────────────────────────


def test_parse_bounds_y_top_simple():
    assert _parse_bounds_y_top("[0,100][1080,200]") == 100


def test_parse_bounds_y_top_negative():
    assert _parse_bounds_y_top("[0,-50][100,0]") == -50


def test_parse_bounds_y_top_empty():
    assert _parse_bounds_y_top("") is None


def test_parse_bounds_y_top_garbage():
    assert _parse_bounds_y_top("not bounds") is None


# ─── Layout fallback activation ─────────────────────────────


def test_rich_a11y_unaffected_by_layout():
    """DeskClock-like — every clickable has rid/desc → layout signature OFF.

    Two screens with same class hierarchy but different bounds should NOT
    diverge on layout alone (a11y already discriminates them).
    """
    h = ScreenSigner()
    views_a = [
        _v(rid="com.x:id/btn_alarm", desc="Alarm", bounds="[0,100][100,200]"),
        _v(rid="com.x:id/btn_clock", desc="Clock", bounds="[0,300][100,400]"),
        _v(rid="com.x:id/btn_timer", desc="Timer", bounds="[0,500][100,600]"),
        _v(rid="com.x:id/btn_stopwatch", desc="Stopwatch", bounds="[0,700][100,800]"),
        _v(rid="com.x:id/btn_bedtime", desc="Bedtime", bounds="[0,900][100,1000]"),
    ]
    # Same a11y, slightly shifted bounds (e.g. orientation jitter)
    views_b = [
        _v(rid="com.x:id/btn_alarm", desc="Alarm", bounds="[0,150][100,250]"),
        _v(rid="com.x:id/btn_clock", desc="Clock", bounds="[0,350][100,450]"),
        _v(rid="com.x:id/btn_timer", desc="Timer", bounds="[0,550][100,650]"),
        _v(rid="com.x:id/btn_stopwatch", desc="Stopwatch", bounds="[0,750][100,850]"),
        _v(rid="com.x:id/btn_bedtime", desc="Bedtime", bounds="[0,950][100,1050]"),
    ]
    assert h._structural_hash(views_a, "MainActivity") == h._structural_hash(views_b, "MainActivity")


def test_anonymous_views_same_layout_same_hash():
    """Same anonymous-view screen captured twice → same hash."""
    h = ScreenSigner()
    views = [
        _v(bounds="[0,100][1080,200]"),
        _v(bounds="[0,300][1080,400]"),
        _v(bounds="[0,500][1080,600]"),
        _v(bounds="[0,700][1080,800]"),
        _v(bounds="[0,900][1080,1000]"),
        _v(bounds="[0,1100][1080,1200]"),
    ]
    h1 = h._structural_hash(views, "MainActivity")
    h2 = h._structural_hash(list(views), "MainActivity")
    assert h1 == h2


def test_anonymous_views_different_layout_different_hash():
    """Two screens — same class hierarchy, different Y distribution → different hash.

    This is the megacoffee Home vs 메가오더 case. Without layout fallback,
    both hash identical → walk thinks click did nothing → BACK loop.
    """
    h = ScreenSigner()
    home = [
        _v(bounds="[0,100][1080,200]"),    # banner
        _v(bounds="[100,400][500,600]"),   # quick order
        _v(bounds="[600,400][1000,600]"),  # promo
        _v(bounds="[100,800][500,1000]"),
        _v(bounds="[600,800][1000,1000]"),
        _v(bounds="[0,2174][216,2337]"),   # bottom nav (always present)
        _v(bounds="[216,2174][432,2337]"),
        _v(bounds="[432,2174][648,2337]"),
        _v(bounds="[648,2174][864,2337]"),
        _v(bounds="[864,2174][1080,2337]"),
    ]
    megaorder = [
        _v(bounds="[42,200][1038,500]"),    # store card
        _v(bounds="[42,550][1038,850]"),    # menu category 1
        _v(bounds="[42,900][1038,1200]"),   # menu category 2
        _v(bounds="[42,1250][1038,1550]"),  # menu category 3
        _v(bounds="[42,1600][1038,1900]"),  # menu category 4
        _v(bounds="[0,2174][216,2337]"),    # same bottom nav
        _v(bounds="[216,2174][432,2337]"),
        _v(bounds="[432,2174][648,2337]"),
        _v(bounds="[648,2174][864,2337]"),
        _v(bounds="[864,2174][1080,2337]"),
    ]
    h_home = h._structural_hash(home, "MainActivity")
    h_mega = h._structural_hash(megaorder, "MainActivity")
    assert h_home != h_mega, "home and 메가오더 must hash differently"


def test_layout_quantization_tolerates_pixel_shift():
    """1-pixel jitter in bounds → same Y bucket → same hash.

    Prevents canonical explosion from minor layout reflow.
    """
    h = ScreenSigner()
    views_a = [
        _v(bounds="[0,100][1080,200]"),
        _v(bounds="[0,300][1080,400]"),
        _v(bounds="[0,500][1080,600]"),
        _v(bounds="[0,700][1080,800]"),
        _v(bounds="[0,900][1080,1000]"),
        _v(bounds="[0,1100][1080,1200]"),
    ]
    # All shifted by 1px (within 50px bucket)
    views_b = [
        _v(bounds="[0,101][1080,201]"),
        _v(bounds="[0,301][1080,401]"),
        _v(bounds="[0,501][1080,601]"),
        _v(bounds="[0,701][1080,801]"),
        _v(bounds="[0,901][1080,1001]"),
        _v(bounds="[0,1101][1080,1201]"),
    ]
    assert h._structural_hash(views_a, "MainActivity") == h._structural_hash(views_b, "MainActivity")


def test_few_clickables_no_layout_fallback():
    """<5 clickables → no layout fallback (avoids over-discrimination on
    auth/dialog screens with 2 buttons whose layout shifts slightly)."""
    h = ScreenSigner()
    a = [
        _v(bounds="[100,1000][500,1200]"),
        _v(bounds="[600,1000][1000,1200]"),
    ]
    b = [
        _v(bounds="[100,500][500,700]"),
        _v(bounds="[600,500][1000,700]"),
    ]
    # Same class hierarchy, no a11y, only 2 clickables → layout off → same hash
    assert h._structural_hash(a, "MainActivity") == h._structural_hash(b, "MainActivity")


def test_hash_stable_across_calls():
    """Same input → same output (no time/random dependency)."""
    h = ScreenSigner()
    views = [
        _v(bounds="[0,100][1080,200]"),
        _v(bounds="[0,300][1080,400]"),
        _v(bounds="[0,500][1080,600]"),
        _v(bounds="[0,700][1080,800]"),
        _v(bounds="[0,900][1080,1000]"),
    ]
    hashes = {h._structural_hash(views, "MainActivity") for _ in range(3)}
    assert len(hashes) == 1


# ─── P0-14 (2026-05-07): WebView count-jitter + SystemUI overlay 방어 ────


def _webview_stamp_screen(extra_anonymous_views: int, clock_text: str, signal_desc: str):
    """메가커피 6caa9768 잡 스탬프 유의사항 화면 시뮬레이션.

    - 4 clickable views (이전 / 새로고침 / stampNotice / 닫기) — 항상 동일
    - 다수의 익명 View (anonymous WebView 콘텐츠) — 캡처 시점마다 ±N 변동
    - SystemUI 시계·신호 — 분 단위 변화 + 신호 강도 변화
    """
    base = [
        _v(cls="View", clickable=True,  desc="이전",       rid="",            bounds="[0,200][100,300]"),
        _v(cls="View", clickable=True,  desc="새로고침",    rid="",            bounds="[100,200][200,300]"),
        _v(cls="View", clickable=True,  desc="유의사항 버튼", rid="stampNotice", bounds="[200,200][400,300]"),
        _v(cls="View", clickable=True,  desc="닫기",       rid="",            bounds="[980,200][1080,300]"),
        # SystemUI overlay — 캡처마다 desc 가 다름
        _v(cls="View", clickable=False, desc=clock_text,  rid="clock"),
        _v(cls="View", clickable=False, desc=signal_desc, rid="mobile_combo"),
        _v(cls="FrameLayout", clickable=False, rid="status_bar_container"),
        _v(cls="FrameLayout", clickable=False, rid="status_bar_contents"),
    ]
    base.extend(_v(cls="View", clickable=False) for _ in range(extra_anonymous_views))
    return base


def test_p0_14_stamp_warning_5_captures_collapse_to_one():
    """동일 WebView 화면을 5번 캡처했을 때 모두 같은 structural_hash 가 나와야 함.

    회귀 트리거: 메가커피 6caa9768 잡, 같은 "스탬프 유의사항" 5번 캡처가
    anonymous View 1~2개 차이 (107 / 109 / 110 / 111) 와 status bar 시계
    분 변화로 5개 다른 canonical 로 갈렸던 케이스.
    """
    h = ScreenSigner()
    captures = [
        _webview_stamp_screen(103, "9:19 PM",  "T-Mobile, no signal."),
        _webview_stamp_screen(105, "9:20 PM",  "T-Mobile, signal full."),
        _webview_stamp_screen(105, "9:21 PM",  "T-Mobile, two bars."),
        _webview_stamp_screen(106, "9:22 PM",  "T-Mobile, two bars."),
        _webview_stamp_screen(107, "9:24 PM",  "T-Mobile, three bars."),
    ]
    hashes = {h._structural_hash(v, "WebActivity") for v in captures}
    assert len(hashes) == 1, (
        f"P0-14 회귀: 5 capture should collapse to 1 hash, got {len(hashes)}"
    )


def test_p0_14_log_bucket_preserves_order_of_magnitude_diff():
    """카운트 1~2 차이는 흡수해도 1↔10 같은 의미있는 차이는 분기 유지."""
    h = ScreenSigner()
    sparse = [_v(cls="View", clickable=False) for _ in range(2)]
    dense  = [_v(cls="View", clickable=False) for _ in range(50)]
    assert h._structural_hash(sparse, "MainActivity") != h._structural_hash(dense, "MainActivity")


def test_p0_14_systemui_clock_change_doesnt_split():
    """status bar 시계만 다른 두 캡처는 같은 hash 여야 함."""
    h = ScreenSigner()
    a = [
        _v(cls="View", clickable=True, desc="홈", rid="home_btn", bounds="[0,0][100,100]"),
        _v(cls="View", clickable=False, desc="9:19 PM", rid="clock"),
    ]
    b = [
        _v(cls="View", clickable=True, desc="홈", rid="home_btn", bounds="[0,0][100,100]"),
        _v(cls="View", clickable=False, desc="11:47 PM", rid="clock"),
    ]
    assert h._structural_hash(a, "MainActivity") == h._structural_hash(b, "MainActivity")
