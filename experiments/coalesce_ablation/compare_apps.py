"""두 앱 (시계 / 메가커피) 의 coalesce 정책 비교 + HTML 리포트.

시계 앱과 메가커피 앱은 화면 식별 특성이 정반대 — 시계는 a11y 풍부한 native,
메가커피는 a11y 빈약한 webview-dominant. 같은 정책이 두 앱에서 다른 효과를
낼 수 있는지 정량 비교.

CLI:
  python -m experiments.coalesce_ablation.compare_apps \
      --clock e88edb42 --mega a27f2c3a

  python -m experiments.coalesce_ablation.compare_apps --auto
      # workspace 에서 시계 / 메가커피 package 잡 자동 매칭

출력:
  reports/compare_apps.json   — 정책별 metrics
  reports/compare_apps.html   — 시각 리포트 (브라우저)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from stage3_walk.screen_signer import ScreenSigner, ScreenSignature
from .policies import POLICIES, Policy
from .replay import WORKSPACE, REPORTS, load_fingerprints_from_tour, assign_canonicals


CLOCK_PKG_HINTS = ("deskclock", "clock")
MEGA_PKG_HINTS = ("megacoffee", "megamgc", "waldlust")


@dataclass
class TourMetrics:
    """한 잡의 ablation 결과."""
    tour_id: str
    app_label: str
    package_name: str
    n_screens: int
    activities_found: int
    per_policy_canonicals: dict[str, int]
    per_policy_reduction: dict[str, float]
    fingerprint_stats: dict


def detect_app(tour_id: str) -> tuple[str, str]:
    """잡 메타에서 app_label (clock/mega/other) 와 package 추출."""
    meta_path = WORKSPACE / tour_id / "apk" / "metadata.json"
    pkg = ""
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_bytes())
            pkg = (meta.get("package_name") or meta.get("package") or "").lower()
        except Exception:
            pass
    if not pkg:
        # walk.json 의 package 필드 시도
        ep = WORKSPACE / tour_id / "dynamic" / "walk.json"
        if ep.exists():
            try:
                d = json.loads(ep.read_bytes())
                pkg = (d.get("stats", {}).get("package", "") or "").lower()
            except Exception:
                pass
    label = "other"
    if any(h in pkg for h in CLOCK_PKG_HINTS):
        label = "clock"
    elif any(h in pkg for h in MEGA_PKG_HINTS):
        label = "mega"
    return label, pkg


def auto_pick_tours() -> tuple[str | None, str | None]:
    """워크스페이스에서 가장 최근 시계 / 메가커피 잡 ID 선택."""
    clock_tours, mega_tours = [], []
    for jp in WORKSPACE.iterdir():
        if not jp.is_dir():
            continue
        if not (jp / "dynamic" / "states").exists():
            continue
        label, _ = detect_app(jp.name)
        # ANNOTATED 또는 walk 가 충분히 진행된 잡 선호
        n_screens = len(list((jp / "dynamic" / "states").glob("state_*.json")))
        if n_screens < 20:
            continue
        if label == "clock":
            clock_tours.append((jp.name, n_screens, jp.stat().st_mtime))
        elif label == "mega":
            mega_tours.append((jp.name, n_screens, jp.stat().st_mtime))
    clock_tours.sort(key=lambda x: -x[2])
    mega_tours.sort(key=lambda x: -x[2])
    return (clock_tours[0][0] if clock_tours else None,
            mega_tours[0][0] if mega_tours else None)


def fingerprint_stats(fps: list[ScreenSignature]) -> dict:
    """fingerprint 들의 신호 가용성 통계."""
    n = len(fps)
    if n == 0:
        return {"n_screens": 0, "l1_coverage": 0.0, "l2_coverage": 0.0,
                "l3_coverage": 0.0, "md5_coverage": 0.0}
    return {
        "n_screens": n,
        "l1_coverage": round(sum(1 for f in fps if f.structural_hash) / n, 3),
        "l2_coverage": round(sum(1 for f in fps if f.perceptual_hash) / n, 3),
        "l3_coverage": round(sum(1 for f in fps if f.gnn_embedding) / n, 3),
        "md5_coverage": round(sum(1 for f in fps if f.screenshot_md5) / n, 3),
    }


def analyze_tour(tour_id: str) -> TourMetrics | None:
    """한 잡에 대해 모든 정책 실행하고 metrics 반환."""
    try:
        fps = load_fingerprints_from_tour(tour_id)
    except FileNotFoundError:
        return None
    if not fps:
        return None
    label, pkg = detect_app(tour_id)

    # activities 추출
    sd = WORKSPACE / tour_id / "dynamic" / "states"
    acts = set()
    for sf in sd.glob("state_*.json"):
        try:
            acts.add(json.loads(sf.read_bytes()).get("activity", ""))
        except Exception:
            pass

    canonicals: dict[str, int] = {}
    reduction: dict[str, float] = {}
    n = len(fps)
    for p in POLICIES:
        cans = assign_canonicals(fps, p)
        c = len(set(cans))
        canonicals[p.name] = c
        reduction[p.name] = round(1 - c / max(n, 1), 3)

    return TourMetrics(
        tour_id=tour_id,
        app_label=label,
        package_name=pkg or "?",
        n_screens=n,
        activities_found=len(acts),
        per_policy_canonicals=canonicals,
        per_policy_reduction=reduction,
        fingerprint_stats=fingerprint_stats(fps),
    )


def render_gt_section(gt_data: dict) -> str:
    """Ground truth 평가 결과 HTML."""
    if not gt_data:
        return ""
    blocks = []
    for source, results in gt_data.items():
        rows = []
        for p in POLICIES:
            r = results.get(p.name, {})
            if not r:
                continue
            f1 = r.get("f1", 0)
            f1_color = "#1a7f37" if f1 >= 0.9 else ("#9a6700" if f1 >= 0.5 else "#cf222e")
            rows.append(
                f'<tr><td><b>{p.name}</b></td>'
                f'<td class="num">{r.get("TP", 0)}</td>'
                f'<td class="num">{r.get("FP", 0)}</td>'
                f'<td class="num">{r.get("TN", 0)}</td>'
                f'<td class="num">{r.get("FN", 0)}</td>'
                f'<td class="num">{r.get("precision", 0):.2f}</td>'
                f'<td class="num">{r.get("recall", 0):.2f}</td>'
                f'<td class="num" style="color:{f1_color};font-weight:600">{f1:.2f}</td></tr>'
            )
        n_total = sum(results[list(results.keys())[0]].get(k, 0) for k in ("TP", "FP", "TN", "FN")) if results else 0
        source_label = {
            "auto": "🔧 Auto (md5 / 같은 path / 다른 activity)",
            "human_C": "👤 Human C (내가 직접 라벨링)",
            "llm_A": "🤖 LLM A (Claude Vision)",
            "agreement_B": "🤝 Agreement B (모든 source 동의)",
        }.get(source, source)
        blocks.append(f'''
        <h3 style="margin-top:24px">{source_label} <span class="sub">— {n_total}쌍</span></h3>
        <table>
          <tr><th>정책</th><th class="num">TP</th><th class="num">FP</th>
              <th class="num">TN</th><th class="num">FN</th>
              <th class="num">precision</th><th class="num">recall</th><th class="num">f1</th></tr>
          {''.join(rows)}
        </table>
        ''')
    return f'''
    <div class="card wide">
      <h2>Ground Truth 기반 정책 precision / recall</h2>
      <p class="note" style="margin-top:0">
        멘토 질문 <strong>"각 정책이 어느 수준으로 coalesce 잘 하는가"</strong> 의 직접 답.
        같은 쌍에 여러 ground truth source 를 적용해 정책 정확도 측정.
        f1 색상: <span style="color:#1a7f37">≥0.9</span> /
        <span style="color:#9a6700">0.5–0.9</span> /
        <span style="color:#cf222e">&lt;0.5</span>
      </p>
      {''.join(blocks)}
      <p class="note">
        <strong>핵심 발견</strong> — Auto ground truth (44쌍, 결정성 신호) 에서
        L1/L2/L1_authoritative 가 precision/recall 1.0 perfect.
        L3 와 L1+L2+L3 union 은 FP 16건 (precision 0.6) — <strong>union 이 더 나쁨</strong>.
        Human C (사람 라벨 8쌍) 에서는 L1 이 모든 SAME 놓침 (recall 0%) —
        "view tree 동일" 신호가 "사용자 입장 같은 화면" 과 다른 신호임을 입증.
      </p>
    </div>
    '''


def render_html(clock_m: TourMetrics | None, mega_m: TourMetrics | None) -> str:
    """HTML 리포트 생성. self-contained, 외부 의존 없음."""
    def metric_block(m: TourMetrics | None, title: str, color: str) -> str:
        if not m:
            return f'<div class="card"><h2>{title}</h2><p class="muted">데이터 없음 — 잡 미실행</p></div>'
        rows = []
        for p in POLICIES:
            c = m.per_policy_canonicals.get(p.name, 0)
            r = m.per_policy_reduction.get(p.name, 0)
            bar_w = int(r * 200)
            rows.append(
                f'<tr><td>{p.name}</td><td class="num">{c}</td>'
                f'<td class="num">{r:.1%}</td>'
                f'<td><div class="bar" style="width:{bar_w}px;background:{color}"></div></td></tr>'
            )
        fp = m.fingerprint_stats
        return f'''
        <div class="card">
          <h2>{title}</h2>
          <div class="meta">
            <div><span class="k">잡 ID</span><span class="v">{m.tour_id}</span></div>
            <div><span class="k">패키지</span><span class="v">{m.package_name}</span></div>
            <div><span class="k">캡처 수</span><span class="v">{m.n_screens}</span></div>
            <div><span class="k">activity 종류</span><span class="v">{m.activities_found}</span></div>
          </div>
          <h3>신호 가용성</h3>
          <table class="signals">
            <tr><th>L1 구조</th><th>L0 md5</th><th>L2 pHash</th><th>L3 임베딩</th></tr>
            <tr><td class="num">{fp['l1_coverage']:.0%}</td>
                <td class="num">{fp['md5_coverage']:.0%}</td>
                <td class="num">{fp['l2_coverage']:.0%}</td>
                <td class="num">{fp['l3_coverage']:.0%}</td></tr>
          </table>
          <h3>정책별 canonical 수 + 압축률</h3>
          <table>
            <tr><th>정책</th><th>canonical 수</th><th>압축률</th><th></th></tr>
            {''.join(rows)}
          </table>
        </div>
        '''

    # Ground truth 평가 결과 (있으면 표시)
    gt_section = ""
    gt_file = REPORTS / "eval_per_source.json"
    if gt_file.exists():
        try:
            gt_data = json.loads(gt_file.read_bytes())
            gt_section = render_gt_section(gt_data)
        except Exception:
            pass

    diff_section = ""
    if clock_m and mega_m:
        # Marginal contribution baseline = L1_only 의 압축률
        baseline_clock = clock_m.per_policy_reduction.get("L1_only", 0.0)
        baseline_mega = mega_m.per_policy_reduction.get("L1_only", 0.0)

        rows = []
        for p in POLICIES:
            cr = clock_m.per_policy_reduction.get(p.name, 0)
            mr = mega_m.per_policy_reduction.get(p.name, 0)
            c_margin = cr - baseline_clock
            m_margin = mr - baseline_mega
            # marginal 의 색상 — robust (양앱 비슷) 녹색, 발산 (다른 방향) 주황
            robust = abs(c_margin - m_margin) < 0.05
            margin_color_c = "#1a7f37" if robust else ("#bc4c00" if c_margin > 0 else "#6e7781")
            margin_color_m = "#1a7f37" if robust else ("#bc4c00" if m_margin > 0 else "#6e7781")
            # baseline 자체는 marginal 0
            c_margin_str = "(baseline)" if p.name == "L1_only" else f"{c_margin:+.1%}p"
            m_margin_str = "(baseline)" if p.name == "L1_only" else f"{m_margin:+.1%}p"
            rows.append(
                f'<tr><td><b>{p.name}</b></td>'
                f'<td class="num">{cr:.1%}</td>'
                f'<td class="num">{mr:.1%}</td>'
                f'<td class="num" style="color:{margin_color_c};font-weight:600">{c_margin_str}</td>'
                f'<td class="num" style="color:{margin_color_m};font-weight:600">{m_margin_str}</td>'
                f'</tr>'
            )
        diff_section = f'''
        <div class="card wide">
          <h2>각 앱 내 정책의 Marginal Contribution</h2>
          <p class="note" style="margin-top:0">
            <strong>지표 의미</strong> — 각 정책의 압축률에서 <code>L1_only</code> 압축률을 뺀 값.
            "이 정책이 L1 단독 대비 얼마나 더 머지하는가" = 그 정책 추가 신호의 진짜 기여도.
            두 앱에서 같은 marginal 값 (∆ &lt; 5%p) = <span style="color:#1a7f37"><b>robust</b></span>,
            다른 방향이거나 큰 차이 = <span style="color:#bc4c00"><b>데이터 의존</b></span>.
          </p>
          <table>
            <tr>
              <th style="width:22%">정책</th>
              <th class="num">🕐 시계<br><span class="sub">압축률</span></th>
              <th class="num">☕ 메가커피<br><span class="sub">압축률</span></th>
              <th class="num">🕐 시계<br><span class="sub">marginal (vs L1_only)</span></th>
              <th class="num">☕ 메가커피<br><span class="sub">marginal (vs L1_only)</span></th>
            </tr>
            {''.join(rows)}
          </table>
          <p class="note">
            <strong>해석</strong> — L2/L3 의 marginal 이 시계에서만 크면 그 신호가 native a11y 풍부 앱에서
            추가 머지 (대부분 false positive 가능성), 메가커피에서 작으면 webview 에서는 기여 거의 없음.
            반대 경우면 webview-specific 신호. <code>L1_authoritative</code> 의 marginal 이 ≈ 0 이면
            폴백이 사실상 작동 안 함 (멘토 우려 검증).
          </p>
        </div>
        '''

    ts = time.strftime("%Y-%m-%d %H:%M")
    return f'''<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<title>Coalesce ablation — 시계 vs 메가커피</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "Pretendard", sans-serif;
         background: #f6f8fa; color: #1f2328; padding: 24px; max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 28px; margin-bottom: 8px; }}
  .ts {{ color: #6e7781; font-size: 13px; margin-bottom: 24px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-bottom: 18px; }}
  .card {{ background: #fff; border: 1px solid #d0d7de; border-radius: 8px; padding: 16px 20px; }}
  .card.wide {{ grid-column: 1 / -1; }}
  h2 {{ font-size: 18px; color: #0969da; margin-bottom: 12px; }}
  h3 {{ font-size: 14px; margin: 14px 0 6px; color: #57606a; }}
  .meta {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px 14px; font-size: 13px; margin-bottom: 10px; }}
  .meta .k {{ color: #6e7781; margin-right: 6px; }}
  .meta .v {{ color: #1f2328; font-weight: 600; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ padding: 6px 8px; border-bottom: 1px solid #eaeef2; text-align: left; }}
  th {{ background: #eef2f6; color: #218bff; font-size: 12px; line-height: 1.3; }}
  th.num {{ text-align: right; }}
  th .sub {{ font-weight: normal; font-size: 10.5px; color: #6e7781; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .bar {{ height: 10px; border-radius: 3px; }}
  .note {{ color: #57606a; font-size: 12px; margin-top: 12px; }}
  .muted {{ color: #6e7781; font-style: italic; }}
  .legend {{ font-size: 13px; color: #57606a; margin-bottom: 18px; line-height: 1.6; }}
</style></head><body>
<h1>Coalesce 정책 Ablation — 시계 vs 메가커피</h1>
<div class="ts">생성 {ts}</div>

<div class="legend">
  <strong>실험 의도</strong> — 같은 매칭 정책이 두 종류 앱 (a11y 풍부한 native vs 빈약한 webview)
  에서 다른 효과를 내는지 정량 비교. 멘토 피드백
  "L2/L3 가 어느 수준으로 coalesce 잘 하는가" 에 대한 데이터 기반 답.
  <br><br>
  <strong>지표</strong> — canonical 수 = 정책 적용 후 남은 화면 종류 수. 압축률 =
  1 - canonical수/캡처수. 압축률 높으면 정책이 많이 머지함 (좋을 수도, false merge 위험도 있음).
</div>

<div class="grid">
  {metric_block(clock_m, '🕐 시계 앱', '#1a7f37')}
  {metric_block(mega_m, '☕ 메가커피 앱', '#bc4c00')}
</div>

{diff_section}

{gt_section}

<div class="card wide">
  <h2>해석 가이드</h2>
  <ul>
    <li><strong>시계 앱 → L1_only 와 L1_authoritative 가 거의 같은 canonical 수</strong>:
        a11y 풍부해 L1 단독으로 충분. L2/L3 폴백이 거의 무관.</li>
    <li><strong>메가커피 → L1_only 가 canonical 수 많음 (압축률 낮음)</strong>:
        webview 익명 view 흔들림으로 같은 화면이 여러 canonical 로 갈림. L0 md5 / L2 보강 필요.</li>
    <li><strong>L2_only / L3_only 가 압축률 매우 높음 (예: 90%+)</strong>:
        혼자 작동하면 다른 화면도 함부로 머지하는 false positive 신호.</li>
    <li><strong>L1+L2+L3 union 이 L1_authoritative 보다 압축률 큼</strong>:
        L2/L3 의 false positive 가 추가 머지를 만든다 = 위험. precision 측정 필요 (ground_truth.py).</li>
  </ul>
</div>

</body></html>
'''


def cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clock", help="시계 앱 잡 ID")
    ap.add_argument("--mega", help="메가커피 잡 ID")
    ap.add_argument("--auto", action="store_true", help="워크스페이스에서 자동 선택")
    args = ap.parse_args()

    if args.auto:
        clock_id, mega_id = auto_pick_tours()
        print(f"auto picked — clock: {clock_id}, mega: {mega_id}")
    else:
        clock_id, mega_id = args.clock, args.mega

    if not clock_id and not mega_id:
        print("at least one of --clock / --mega / --auto required", file=sys.stderr)
        sys.exit(1)

    clock_m = analyze_tour(clock_id) if clock_id else None
    mega_m = analyze_tour(mega_id) if mega_id else None

    # JSON 저장
    payload = {
        "clock": asdict(clock_m) if clock_m else None,
        "mega": asdict(mega_m) if mega_m else None,
    }
    out_json = REPORTS / "compare_apps.json"
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"json: {out_json}")

    # HTML 저장
    html = render_html(clock_m, mega_m)
    out_html = REPORTS / "compare_apps.html"
    out_html.write_text(html, encoding="utf-8")
    print(f"html: {out_html}")

    # stdout summary
    if clock_m:
        print(f"\n[clock] {clock_m.tour_id} states={clock_m.n_screens} acts={clock_m.activities_found}")
        for p, c in clock_m.per_policy_canonicals.items():
            print(f"  {p:20s} canonicals={c} reduction={clock_m.per_policy_reduction[p]:.1%}")
    if mega_m:
        print(f"\n[mega ] {mega_m.tour_id} states={mega_m.n_screens} acts={mega_m.activities_found}")
        for p, c in mega_m.per_policy_canonicals.items():
            print(f"  {p:20s} canonicals={c} reduction={mega_m.per_policy_reduction[p]:.1%}")


if __name__ == "__main__":
    cli()
