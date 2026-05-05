"""XMLViewTreeReader — 전통 XML 기반 Android 앱용 (Java/Kotlin).

resource-id, clickable 속성을 신뢰. 가장 잘 동작하는 기준 Extractor.
"""

from __future__ import annotations

from typing import Any

from .base import ViewTreeReader

# 네비게이션 관련 키워드
NAV_KEYWORDS = [
    "tab", "menu", "nav", "drawer", "settings", "more",
    "home", "profile", "search", "toolbar", "option",
    "notification", "account", "calendar", "event",
    "write", "create", "add", "new", "compose", "edit",
    "back", "close", "cancel", "done", "save",
    "detail", "info", "about", "help",
    # Drawer / overflow / avatar / settings entry points (Tier-1 addition)
    "hamburger", "menu_icon", "drawer_toggle", "drawer_indicator",
    "avatar", "user_image", "profile_pic", "account_circle",
    "gear", "cog", "preferences",
    "overflow", "kebab", "three_dot",
]

# Strong signals in content-desc that reveal hidden nav entry points
NAV_DESC_STRONG = [
    "open drawer", "open menu", "open navigation",
    "navigation drawer", "main menu",
    "your profile", "view profile", "account", "settings",
    "more options", "options menu",
]

# Cycle 3 Fix A (2026-04-30) — Picker / Onboarding entry rid 보너스.
# Evidence (workspace/8b72067f):
# - state_006/007 가 Material TimePicker (is_dialog=False — Compose inline)
#   인데 bottom_tab tap 70회, OK 0회. score 가 outside tab 에 밀림.
# - BEDTIME fragment 26 state, 'Get Started' button 0회 누름.
# - Overflow popup 28회 tap, popup item (Screen saver/Settings) 0회.
#
# Specific rid prefix 만 매치 — false positive 최소화.
PICKER_BONUS_PATTERNS = (
    "material_timepicker_",     # TimePicker mode/OK
    "material_clock_period_",   # AM/PM toggle
    "material_clock_face",      # 시계 face (시간 선택)
    "material_minute_tv",       # 분 input
    "material_hour_tv",         # 시간 input
    "datepicker_",
    "numberpicker_",
    "_onboarding_start",
)
# F1 (2026-05-02): submit 의도 키워드 — 메가커피 e8951fef 옵션 화면에서
# "닫기" 12회 vs "담기" 0회. submit element 가 score 낮아 click 안 됨.
# NAV_KEYWORDS 와 동일 +2.0 강도. task path 진입 (옵션→담기→주문) 보장.
SUBMIT_KEYWORDS = (
    # 한국어
    "담기", "주문", "저장", "추가", "확인", "결제",
    "보내", "전송", "완료", "신청", "구매", "예약",
    # 영문
    "submit", "save", "add to", "confirm",
    "checkout", "payment", "send", "buy", "place order",
)
# Cancel 류는 task path 를 깨므로 페널티 — picker 떠있을 때 cancel 누르면
# task fixture 의 add_alarm 같은 sequence 실패.
PICKER_CANCEL_PATTERNS = (
    "_cancel_button", "cancel_button",
)


class XMLViewTreeReader(ViewTreeReader):
    name = "xml"

    def is_actionable(self, view: dict) -> bool:
        """clickable / scrollable / long_clickable 모두 인정.

        2026-05-02 (W6 — webview text actionable):
          메가커피 e2a2c46f 분석 결과 webview 의 메뉴/메가오더/주문/매장 같은
          핵심 텍스트가 clickable=False TextView 로만 노출 (a11y 한계). 그래서
          이런 view 도 webview 안 + 텍스트 + 적당한 height 면 actionable 후보.

        2026-05-03 (R2 — SUBMIT bypass):
          메가커피 581e8cc8 분석 결과 옵션 상세 화면 (state_0168) 의 "담기" /
          "주문하기" / "옵션 선택" 텍스트가 clickable=False + parent="View"
          (webview 도 nav container 도 아님) — W6/F2 가드 모두 통과 X.
          SUBMIT_KEYWORDS hit + 짧은 텍스트 (≤20 char) 는 parent 무관 actionable.
        """
        if not view.get("visible", True):
            return False
        if view.get("clickable") or view.get("scrollable") or view.get("long_clickable"):
            return True

        text = (view.get("text") or "").strip()
        desc = (view.get("content_desc") or "").strip()

        # R2 (2026-05-03): SUBMIT 키워드 hit + 짧은 텍스트 (라벨로 추정) → parent 무관 actionable.
        # license/FAQ 본문 텍스트 (긴 문장) 의 false positive 차단 위해 길이 가드.
        combined = f"{text} {desc}"
        if (text or desc) and len(combined.strip()) <= 20:
            combined_lower = combined.lower()
            if any(kw.lower() in combined_lower for kw in SUBMIT_KEYWORDS):
                return True
        # W6: webview 안 텍스트 view — clickable=False 라도 actionable 후보
        parent_cls = view.get("parent_class") or ""
        inside_webview = any(
            kw in parent_cls for kw in
            ("WebView", "ChromeWebView", "RNCWebView", "RCTWebView", "X5WebView")
        )
        if inside_webview:
            text = (view.get("text") or "").strip()
            desc = (view.get("content_desc") or "").strip()
            if text or desc:
                from ..list_view_detector import _bounds_height
                h = _bounds_height(view.get("bounds"))
                # 20-200 px row — 너무 큰 wrapper 또는 빈 줄 제외
                if 20 <= h <= 200:
                    return True
        # F2 (2026-05-02): native 하단 탭 / nav row — list_view_detector 가 sibling
        # group 으로 마킹한 view 들. parent_class 가 webview 가 아니어도
        # text/desc 있는 sibling group 멤버는 actionable. 메가커피 홈/메뉴/매장/
        # MY/쿠폰 탭 같은 non-clickable 텍스트 row 직격.
        if view.get("_list_view_group"):
            text = (view.get("text") or "").strip()
            desc = (view.get("content_desc") or "").strip()
            if text or desc:
                return True
        return False

    def get_action_desc(self, view: dict) -> str:
        """액션 설명 문자열. long-press 전용이면 'longclick' 접두.

        Canonical key 는 (label, bounds) — 같은 desc 인 두 다른 view 가 같은
        action 으로 collapse 되는 문제 방지 (RN 의 경우 resource_id 거의 없고
        desc 가 같은 EditText 여러 개 있을 수 있음). 같은 화면 내 bounds 는
        stable (state hash 가 매칭됐으니 위치도 같음).
        """
        rid = view.get("resource_id", "")
        text = view.get("text", "")
        desc = view.get("content_desc", "")
        cls = view.get("class", "")
        bounds = view.get("bounds", "")
        label = rid or desc or text or cls or "?"
        # 위치까지 키에 포함: 같은 desc 라도 다른 위치 = 다른 액션
        position = bounds or "noBounds"
        key = f"{label}@{position}"
        # Prefer click if clickable; longpress-only if view is long_clickable
        # but not clickable — avoids conflating a clickable item's longclick
        # variant with its click (handled separately if we expose both later).
        if view.get("clickable"):
            action = "click"
        elif view.get("long_clickable"):
            action = "longclick"
        elif view.get("scrollable"):
            action = "scroll"
        else:
            action = "click"
        return f"{action} {key}"

    # P0-10d (2026-05-05): 결제/카드 입력/외부 게이트웨이 진입 자체 차단.
    # 카드번호/CVC/본인인증/raon 보안 키패드 같은 화면은 자동화 절대 불가
    # 하고, 잘못 클릭하면 진짜 결제 시도되는 위험. element 의 텍스트/desc/rid
    # 에 이 키워드가 hit 하면 score -100 → walk 가 절대 누르지 않음.
    PAYMENT_DANGER_KEYWORDS = (
        # 결제 진입
        "결제하기", "결제 진행", "결제 시도", "주문 확정", "결제 정보",
        # 카드 정보 입력
        "카드번호", "카드 번호", "카드식별번호", "cvc", "유효기간",
        "신용카드", "체크카드", "카드 등록", "카드추가",
        # 외부 결제 게이트웨이
        "ars 결제", "kb국민카드", "kb 국민카드", "삼성카드", "현대카드",
        "신한카드", "우리카드", "하나카드", "롯데카드", "비씨카드",
        # 본인인증
        "본인인증", "본인 인증", "휴대폰 인증", "sms 인증", "인증번호 발송",
        # 보안 키패드
        "raon", "보안 키패드",
    )

    def score_action(self, view: dict, context: dict[str, Any]) -> float:
        rid = view.get("resource_id", "")
        text = view.get("text", "")
        desc = view.get("content_desc", "")
        cls = view.get("class", "")
        combined = (rid + text + desc + cls).lower()

        canonical = context.get("canonical", "")
        visit_count = context.get("visit_count", 0)
        tried_actions: set[str] = context.get("tried_actions", set())
        action_desc = self.get_action_desc(view)

        score = 1.0

        # P0-10d: 결제 진입 / 카드 입력 / 본인인증 키워드는 score -100 →
        # 절대 click 안 함. 진입 막아 외부 게이트웨이로 빠지지 않게.
        for kw in self.PAYMENT_DANGER_KEYWORDS:
            if kw in combined:
                score -= 100.0
                return score   # 다른 보너스 무관 — 절대 누르면 안 됨

        # === 미시도 액션 보너스 (가장 큰 신호) ===
        if action_desc not in tried_actions:
            score += 4.0

        # 2026-05-02 (W6): webview 안 clickable=False 텍스트 view 는 추정 actionable.
        # 진짜 clickable view 보다 신호 약하니 -0.5 페널티 (그래도 양수 score 가능).
        # 메가커피 같은 webview 메뉴 버튼들 actionable list 에 들어오게 하기 위함.
        if not view.get("clickable") and not view.get("scrollable") and not view.get("long_clickable"):
            score -= 0.5

        # 네비게이션 요소 보너스
        if any(k in combined for k in NAV_KEYWORDS):
            score += 2.0
        # F1 (2026-05-02): Submit 의도 키워드 보너스. 메가커피 e8951fef 데이터:
        # 옵션 화면 진입은 됐는데 "닫기" 12회 vs "담기" 0회 — submit 의도 element
        # score 가 낮아 click 안 됨. NAV 와 동일 강도 +2.0.
        if any(k in combined for k in SUBMIT_KEYWORDS):
            score += 2.0
        # 2026-04-29: NAV_DESC_STRONG 보너스 +3.0 → +1.5 강등.
        # Evidence (workspace/9409ae50): overflow 가 NAV_KEYWORDS "overflow" +
        # NAV_DESC_STRONG "more options" 둘 다 매치 → score=10.0 매번. 다른
        # entry (FAB 의 "Add alarm" 등) 묻힘. settings 키워드도 같은 이유로
        # prefs_fragment 56% 발산. 균등화.
        desc_lower = desc.lower()
        if any(s in desc_lower for s in NAV_DESC_STRONG):
            score += 1.5

        # FAB 명시 보너스 — 보통 새 entity 생성 (알람 추가 / 메모 작성 등)
        # 의 entry. NAV_KEYWORDS 의 'add'/'create'/'compose' 와 별도로
        # FAB 자체 클래스/rid 가지면 +2.0. evidence: 9409ae50 의 + FAB
        # tap=0 — score 가중치 부재로 매번 묻혔음.
        fab_signal = ("floatingaction" in cls.lower() or "fab" in rid.lower()
                      or "floatingaction" in (view.get("parent_class") or "").lower())
        if fab_signal:
            score += 2.0

        # Cycle 3 Fix A — Picker / Onboarding entry 보너스.
        # rid_lower 가 specific prefix 매치 시 +2 (보수적 — OK 가 시간 view
        # 보다 너무 압도하지 않게). cancel 은 picker 보너스 받지 않고 -1
        # 페널티만 — task path (예: 8:30 알람 추가) 깨지 않게.
        rid_lower = rid.lower()
        if any(p in rid_lower for p in PICKER_CANCEL_PATTERNS):
            score -= 1.0  # picker bonus 안 받음
        elif any(p in rid_lower for p in PICKER_BONUS_PATTERNS):
            score += 2.0

        # 클래스 보너스
        if "Button" in cls:
            score += 1.5
        elif "ImageView" in cls or "ImageButton" in cls:
            score += 1.0
        elif "Tab" in cls:
            score += 2.0

        # resource-id 있는 요소 보너스 (실제 버튼일 가능성 높음)
        if rid:
            score += 0.5

        # === 패널티 ===
        # 2026-04-29: visit_count penalty 1.5 → 2.5 강화.
        # Evidence (workspace/9409ae50): score max=min — 같은 view 8번 click
        # 해도 visit_count 누적 안 돼 매번 fresh 10점. canonical_id reset
        # 버그가 별개 원인이지만, 일단 페널티 강화로 같은 entry 다시 안 누르게.
        score -= visit_count * 2.5

        if action_desc in tried_actions:
            score -= 8.0  # 이미 시도한 액션

        if "RecyclerView" in str(view.get("parent_class", "")):
            score -= 2.0  # 리스트 아이템

        # 2026-04-30: per-list_view N번째 클릭 차등 페널티 — 사용자 제안
        # "리스트뷰 1개만 선택해서 일반화". context 에 list_views + visit_count
        # 들어와야 동작. 없으면 0 (기존 동작 유지).
        list_views = context.get("list_views") or []
        if list_views:
            from ..list_view_detector import list_view_redundancy_penalty
            view_idx = context.get("view_index")
            if view_idx is not None:
                visits = context.get("list_view_visit_count") or {}
                score += list_view_redundancy_penalty(view_idx, list_views, visits)

        # 2026-05-01: external page blacklist — 이 trigger 는 외부 페이지로
        # 흘러간 적이 있어 영구 강 페널티. 같은 메뉴 재클릭 방지.
        ext_blacklist = context.get("external_blacklist") or set()
        if ext_blacklist:
            from ..outbound_intent_guard import get_blacklist_penalty
            score += get_blacklist_penalty(action_desc, ext_blacklist)

        # R6 (2026-05-03): multi-target action 페널티. 같은 (canonical, action_desc)
        # 에서 ≥3 다른 화면으로 transition 한 적 있으면 random transition wrapper
        # element 로 판정 → 페널티 -3.0. 메가커피 이벤트 hub 의 webview wrapper
        # 처럼 click 마다 다른 화면 가는 노이즈 차단.
        diversity = context.get("action_target_diversity") or {}
        if diversity:
            targets = diversity.get((canonical, action_desc), set())
            if len(targets) >= 3:
                score -= 3.0

        # E (2026-05-03): task fixture 키워드 보너스. fixture 의 task goal 에 등장
        # 한 명사 (메뉴/장바구니/매장/MY/쿠폰/...) 가 view text/desc 에 hit 면
        # +2.0. 메가커피/DeskClock 등 fixture 있는 앱의 진짜 task path 우선화.
        task_keywords = context.get("task_keywords") or []
        if task_keywords:
            combined_for_kw = f"{text} {desc}"
            if any(kw in combined_for_kw for kw in task_keywords):
                score += 2.0

        # P1-6 (2026-05-05): R5+ score boost — list_view_detector 가 마킹한
        # bottom_nav_row 멤버는 진짜 사용자 navigation entry. 0a36c524 잡 분석
        # 결과 메가오더 탭 score 6.5 vs 이벤트 webview wrapper score 7.0 으로
        # walk 가 이벤트로 빠지는 패턴 → +3 boost 로 우선화.
        lg = (view.get("_list_view_group") or "")
        if isinstance(lg, str) and lg.startswith("bottom_nav"):
            score += 3.0

        if view.get("scrollable") and not view.get("clickable"):
            score -= 1.0  # scroll-only

        return score
