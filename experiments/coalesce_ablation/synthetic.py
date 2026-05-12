"""합성 입력 단위 ablation — 알고리즘만 검증.

production 잡 데이터에 의존하지 않고, 우리가 통제할 수 있는 합성 view
입력으로 각 정책이 어떤 동일성 가설을 잡고 어떤 케이스에서 깨지는지 확인.

ground truth 명확:
  - 같은 화면 캡처 두 번 (시간만 다름) → 같은 화면 — 모든 정책 hit 해야 함
  - webview 익명 view jitter → 같은 화면 — L1 strict 깨짐, L2 catch 가능
  - 같은 레이아웃 다른 메뉴 카테고리 → 다른 화면 — L1 catch, L2 위험
  - 시계 앱 4 탭 (사고 케이스) → 다른 화면 — L1 catch, L3 fallback 위험

CLI:
  python -m experiments.coalesce_ablation.synthetic
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from stage3_walk.screen_signer import ScreenSigner
from .policies import POLICIES
from .metrics import PolicyResult, format_table


def _view(cls="View", clickable=True, text="", desc="", rid="", bounds="[0,0][100,100]"):
    return {
        "class": cls, "clickable": clickable, "scrollable": False, "editable": False,
        "text": text, "content_desc": desc, "resource_id": rid, "bounds": bounds,
    }


@dataclass
class Case:
    """합성 ablation 케이스. ground truth 와 함께."""
    name: str
    views_a: list[dict]
    views_b: list[dict]
    activity: str
    screenshot_a: str
    screenshot_b: str
    same_screen_truth: bool   # 사람이 보기에 같은 화면인가
    description: str


def build_cases() -> list[Case]:
    cases = []

    # ─── Case 1: 같은 화면 두 캡처, 시간만 다름 ──────────
    cases.append(Case(
        name="ticking_clock_same_screen",
        views_a=[
            _view(cls="TextClock", desc="3:45 PM"),
            _view(rid="com.x:id/alarm_btn", desc="Alarm"),
            _view(rid="com.x:id/clock_btn", desc="Clock"),
        ],
        views_b=[
            _view(cls="TextClock", desc="3:46 PM"),
            _view(rid="com.x:id/alarm_btn", desc="Alarm"),
            _view(rid="com.x:id/clock_btn", desc="Clock"),
        ],
        activity="DeskClock", screenshot_a="", screenshot_b="",
        same_screen_truth=True,
        description="시계 ticking 만 다른 같은 화면 — stabilizer 가 마스킹해야",
    ))

    # ─── Case 2: webview 익명 view 카운트 jitter ────────
    base_btns = [
        _view(cls="View", clickable=True, desc="이전",     bounds="[0,200][100,300]"),
        _view(cls="View", clickable=True, desc="새로고침", bounds="[100,200][200,300]"),
        _view(cls="View", clickable=True, desc="공유",     bounds="[900,200][1000,300]"),
        _view(cls="View", clickable=True, desc="닫기",     bounds="[1000,200][1100,300]"),
    ]
    body_a = [_view(bounds=f"[0,{300+i*50}][1080,{350+i*50}]") for i in range(107)]
    body_b = [_view(bounds=f"[0,{300+i*50}][1080,{350+i*50}]") for i in range(109)]
    cases.append(Case(
        name="webview_anonymous_jitter",
        views_a=base_btns + body_a,
        views_b=base_btns + body_b,
        activity="WebActivity", screenshot_a="", screenshot_b="",
        same_screen_truth=True,
        description="webview 익명 view 107 vs 109 — 같은 화면 (P0-14 카운트 bucket 대상)",
    ))

    # ─── Case 3: 같은 레이아웃 다른 메뉴 카테고리 ─────────
    cases.append(Case(
        name="same_layout_different_category",
        views_a=[
            _view(rid="com.x:id/category_tab", desc="추천"),
            _view(rid="com.x:id/menu_item",    desc="아메리카노"),
            _view(rid="com.x:id/menu_item",    desc="라떼"),
        ],
        views_b=[
            _view(rid="com.x:id/category_tab", desc="디카페인"),
            _view(rid="com.x:id/menu_item",    desc="디카페인 아메리카노"),
            _view(rid="com.x:id/menu_item",    desc="디카페인 라떼"),
        ],
        activity="MenuActivity", screenshot_a="", screenshot_b="",
        same_screen_truth=False,
        description="추천 vs 디카페인 — 같은 layout 이지만 다른 의미 화면 (분리되어야)",
    ))

    # ─── Case 4: 시계 앱 4 탭 (L3 fallback 사고 케이스) ──
    cases.append(Case(
        name="deskclock_alarm_vs_timer",
        views_a=[
            _view(rid="com.x:id/alarm_tab",       desc="Alarm"),
            _view(rid="com.x:id/alarm_list_item", desc="Alarm 1"),
            _view(rid="com.x:id/alarm_list_item", desc="Alarm 2"),
        ],
        views_b=[
            _view(rid="com.x:id/timer_tab",       desc="Timer"),
            _view(rid="com.x:id/timer_set_btn",   desc="Set timer"),
            _view(rid="com.x:id/timer_start_btn", desc="Start"),
        ],
        activity="DeskClock", screenshot_a="", screenshot_b="",
        same_screen_truth=False,
        description="알람 탭 vs 타이머 탭 — view 분포 비슷해서 L3 fallback 이 0.95+ 매칭한 사고",
    ))

    # ─── Case 5: 캡처 실패 (L1 = 0) + 같은 png ──────────
    # 실제로는 screenshot 파일 경로 필요. 합성 케이스에선 pHash 빈값이라
    # L2 도 작동 안 함 — 정책간 동일하게 매칭 X 가 정답.
    cases.append(Case(
        name="empty_ui_dump_no_screenshot",
        views_a=[],
        views_b=[],
        activity="MainActivity", screenshot_a="", screenshot_b="",
        same_screen_truth=False,   # 데이터 부족으로 매칭 안 하는 게 보수적 정답
        description="양쪽 다 view 없음 + screenshot 없음 — 매칭 정보 0",
    ))

    return cases


def run_cases() -> None:
    cases = build_cases()
    hasher = ScreenSigner()

    # 케이스별 매트릭스: 행=정책, 열=케이스
    by_policy: dict[str, list[bool]] = {p.name: [] for p in POLICIES}
    truth = [c.same_screen_truth for c in cases]

    for c in cases:
        # Ablation 비교에는 모든 신호가 채워져야 — eager 강제.
        # production walk 의 lazy 모드와 별개.
        fp_a = hasher.compute_fingerprint(c.views_a, c.activity, c.screenshot_a, eager=True)
        fp_b = hasher.compute_fingerprint(c.views_b, c.activity, c.screenshot_b, eager=True)
        for p in POLICIES:
            verdict = p.matcher(fp_a, fp_b)
            by_policy[p.name].append(verdict)

    # 표 출력 — 각 정책이 각 케이스에 어떤 결정 내렸는지
    print("=== 합성 케이스 ablation ===")
    print(f"{'case':40s} truth | " + " | ".join(p.name[:18] for p in POLICIES))
    print("-" * 120)
    for i, c in enumerate(cases):
        truth_str = "SAME" if truth[i] else "DIFF"
        verdicts = [by_policy[p.name][i] for p in POLICIES]
        verdict_strs = []
        for p, v in zip(POLICIES, verdicts):
            correct = "  " if v == truth[i] else "X "
            verdict_strs.append(f"{correct}{('SAME' if v else 'DIFF'):4s}")
        print(f"{c.name[:40]:40s} {truth_str:5s} | " + " | ".join(s[:18].ljust(18) for s in verdict_strs))

    # precision/recall 계산
    print("\n=== 정책별 precision / recall ===")
    rows = []
    for p in POLICIES:
        tp = fp_ = tn = fn = 0
        for v, t in zip(by_policy[p.name], truth):
            if v and t: tp += 1
            elif v and not t: fp_ += 1
            elif not v and not t: tn += 1
            else: fn += 1
        precision = tp / (tp + fp_) if (tp + fp_) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        rows.append({
            "policy": p.name,
            "TP": tp, "FP": fp_, "TN": tn, "FN": fn,
            "precision": f"{precision:.2f}",
            "recall": f"{recall:.2f}",
            "f1": f"{f1:.2f}",
        })
    print(format_table(rows, ["policy", "TP", "FP", "TN", "FN", "precision", "recall", "f1"]))

    print("\n해석:")
    print("  - precision 1.0 = 매칭한 쌍이 모두 정답.")
    print("  - recall 1.0 = 같은 화면 쌍을 모두 잡음.")
    print("  - 시계 사고 케이스 (case 4) 에서 L3_only 가 FP 만들면 정책 약점 입증.")


if __name__ == "__main__":
    run_cases()
