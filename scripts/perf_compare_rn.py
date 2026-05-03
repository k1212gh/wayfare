"""ABCD 적용 전/후 RN 앱 성능 비교 리포트.

기존 (deleted) Mattermost run 의 baseline 메트릭은 코드에 하드코딩 — 그 시점 분석 결과.
새 run 메트릭은 workspace/<tour_id>/ 에서 읽는다.

Usage:
    python scripts/perf_compare_rn.py <tour_id>
    예) python scripts/perf_compare_rn.py 5ba3011d

Output: reports/screenatlas_abcd_perf.html
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from html import escape
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKSPACE = REPO / "workspace"


# ─── Baseline (deleted Mattermost run 0f795b2f) 메트릭 ──────────
# 2026-04-27 분석 시점, ABCD 적용 전. 사용자 분석 + 내 진단 결과 보존.
BASELINE = {
    "label": "ABCD 적용 전 (Mattermost 0f795b2f, deleted)",
    "framework": "react-native",
    "package": "com.mattermost.rn",
    "static_activities": 9,
    "wireframe_nodes": 10,  # 9 + entry
    "walk_screens": 94,
    "unique_struct_hashes": 13,
    "max_collapse_count": 75,  # 75/94 가 한 hash 로 collapse
    "screenshot_count_unique": 1,  # 모든 76 collapse 가 같은 png
    "screenmap_total_nodes": 10,  # walk → ScreenMap 합성 안 됨
    "screenmap_walk_transitions": 0,
    "screenmap_static_edges": 6,
    "stall_resets": 0,  # B 미적용
    "synthesized_nodes": 0,  # D 미적용
    "login_pauses": 0,  # C 약함
    "stage5_completed": False,
    "notes": [
        "75/94 state 가 채널 만들기 폼 한 화면에 갇힘 (76 collapse)",
        "walk → ScreenMap 노드 변환 0 (resolve 실패 → drop)",
        "stall detection 미적용 — 무한 반복",
        "Mattermost 의 server URL/workspace 입력 폼이 login guard 에 안 잡힘",
    ],
}


def collect_metrics(tour_id: str) -> dict:
    tour_dir = WORKSPACE / tour_id
    if not tour_dir.exists():
        return {"error": f"tour dir not found: {tour_dir}"}

    metrics: dict = {
        "tour_id": tour_id,
        "label": "ABCD 적용 후 (Mattermost)",
    }

    # 메타
    meta_path = tour_dir / "apk" / "metadata.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        metrics["framework"] = meta.get("framework", "?")
        metrics["package"] = meta.get("package_name", "?")

    # 정적 분석
    static_path = tour_dir / "static" / "analysis.json"
    if static_path.exists():
        static = json.loads(static_path.read_text(encoding="utf-8"))
        metrics["static_activities"] = len(static.get("activities", []))

    # Stage 3 walk
    walk_path = tour_dir / "dynamic" / "walk.json"
    states_dir = tour_dir / "dynamic" / "states"
    if walk_path.exists():
        walk = json.loads(walk_path.read_text(encoding="utf-8"))
        metrics["walk_screens"] = len(walk.get("states", []))
        metrics["walk_transitions"] = len(walk.get("transitions", []))
    elif states_dir.exists():
        # 진행 중 — state 파일 직접 카운트
        metrics["walk_screens"] = len(list(states_dir.glob("state_*.json")))

    # 상태 다양성 (states 파일들 직접 분석)
    if states_dir.exists():
        struct_counter: Counter = Counter()
        ss_set = set()
        for f in states_dir.glob("state_*.json"):
            try:
                s = json.loads(f.read_text(encoding="utf-8"))
                struct_counter[s.get("structure_str", "")] += 1
                if s.get("screenshot_path"):
                    ss_set.add(s["screenshot_path"])
            except Exception:
                continue
        if struct_counter:
            metrics["unique_struct_hashes"] = len(struct_counter)
            metrics["max_collapse_count"] = max(struct_counter.values())
            metrics["screenshot_count_unique"] = len(ss_set)

    # ScreenMap (Stage 6 + 5 결과)
    screenmap_path = tour_dir / "output" / "screen_map.json"
    if screenmap_path.exists():
        screenmap = json.loads(screenmap_path.read_text(encoding="utf-8"))
        graph = screenmap.get("screen_map", {}).get("graph", {})
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        metrics["screenmap_total_nodes"] = len(nodes)
        metrics["wireframe_nodes"] = sum(1 for n in nodes if n.get("status") == "declared")
        metrics["synthesized_nodes"] = sum(
            1 for n in nodes if n.get("source") == "walk_synthesized"
        )
        edge_src = Counter(e.get("source", "-") for e in edges)
        metrics["screenmap_walk_transitions"] = edge_src.get("walk", 0)
        metrics["screenmap_static_edges"] = edge_src.get("static", 0)

    # 백엔드 로그에서 STALL / PAUSED 카운트 — workspace/_backend.log 또는 task output
    log_paths = [
        REPO / "workspace" / "_backend.log",
    ]
    log_paths += list(Path("C:/Users/USER/AppData/Local/Temp/claude").rglob("tasks/*.output"))
    tour_log_lines = []
    for lp in log_paths:
        if not lp.exists():
            continue
        try:
            for ln in lp.read_text(encoding="utf-8", errors="replace").splitlines():
                if tour_id in ln or "STALL" in ln or "Stall" in ln or "PAUSED" in ln or "Vision labeler" in ln:
                    tour_log_lines.append(ln)
        except Exception:
            continue
    metrics["stall_resets"] = sum(1 for ln in tour_log_lines if "[STALL]" in ln)
    metrics["login_pauses"] = sum(1 for ln in tour_log_lines if "PAUSED" in ln)

    # Stage 5 완료 여부
    state_path = tour_dir / "pipeline_state.json"
    if state_path.exists():
        st = json.loads(state_path.read_text(encoding="utf-8"))
        s5 = (st.get("stages") or {}).get("stage5", {})
        metrics["stage5_completed"] = s5.get("status") == "done"
        metrics["pipeline_stage"] = st.get("stage", "?")
        metrics["stage5_detail"] = s5.get("detail", "")

    return metrics


# ─── HTML ────────────────────────────────────────────

CSS = """
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css');
@import url('https://cdn.jsdelivr.net/gh/JetBrains/JetBrainsMono/web/CSS/JetBrainsMono-Regular.css');

* { box-sizing: border-box; }
body {
  font-family: 'Pretendard Variable', sans-serif;
  background: #f7f7f5; color: #1a1a1a;
  line-height: 1.7; font-size: 14.5px;
  margin: 0 auto; padding: 36px 52px; max-width: 1180px;
}
code { font-family: 'JetBrains Mono', monospace; font-size: 0.88em;
       background: #eee4d4; padding: 1px 6px; border-radius: 3px; color: #5c4a1c; }

.cover { border-bottom: 2px solid #1a1a1a; padding-bottom: 18px; margin-bottom: 24px; }
.cover h1 { font-size: 2em; margin: 0 0 4px 0; letter-spacing: -0.02em; }
.cover .sub { color: #666; font-size: 1.02em; }
.cover .meta { color: #888; font-size: 0.85em; margin-top: 8px; }

h2 { font-size: 1.25em; margin: 32px 0 12px 0; padding-bottom: 6px; border-bottom: 2px solid #1a1a1a; }
h3 { font-size: 1.05em; margin: 18px 0 8px 0; color: #2a2a2a; }

.cmp { width: 100%; border-collapse: collapse; margin: 14px 0; background: #fff;
       border: 1px solid #e0e0d8; border-radius: 10px; overflow: hidden; }
.cmp th { background: #1a1a1a; color: #fff; padding: 10px 14px; text-align: left;
          font-weight: 600; font-size: 0.92em; }
.cmp td { padding: 9px 14px; border-bottom: 1px solid #eee; vertical-align: top; }
.cmp tr:last-child td { border-bottom: none; }
.cmp tr:nth-child(even) td { background: #fafaf5; }

.metric-name { font-weight: 600; color: #2a2a2a; width: 30%; }
.metric-cell { font-family: 'JetBrains Mono', monospace; font-size: 0.95em; }
.delta-up   { color: #059669; font-weight: 600; }
.delta-down { color: #dc2626; font-weight: 600; }
.delta-eq   { color: #888; }

.banner {
  padding: 14px 20px; border-radius: 8px; margin: 14px 0;
  background: #fff; border-left: 4px solid #0a5c9c;
}
.banner.warn { border-left-color: #d97706; background: #fef3c7; }
.banner.ok   { border-left-color: #059669; background: #d1fae5; }

.notes { background: #fafaf5; padding: 12px 18px; border-radius: 8px;
         border-left: 3px solid #aaa; margin: 10px 0; font-size: 0.94em; }
.notes ul { margin: 4px 0; padding-left: 22px; }

.in-progress { color: #d97706; font-weight: 600; }

@page { size: A4 portrait; margin: 15mm 12mm; }
@media print { body { padding: 0; max-width: none; } a { color: inherit; } }
"""


def fmt_delta(before, after):
    if before is None or after is None:
        return f'<span class="delta-eq">—</span>'
    if isinstance(before, bool) or isinstance(after, bool):
        if before == after:
            return f'<span class="delta-eq">동일 ({after})</span>'
        return f'<span class="delta-up">{before} → {after}</span>'
    try:
        b, a = float(before), float(after)
        if a == b:
            return f'<span class="delta-eq">변화 없음</span>'
        diff = a - b
        ratio = "" if b == 0 else f" ({diff / b * 100:+.0f}%)"
        cls = "delta-up" if diff > 0 else "delta-down"
        sign = "+" if diff > 0 else ""
        return f'<span class="{cls}">{sign}{diff:.0f}{ratio}</span>'
    except (ValueError, TypeError):
        return f'<span class="delta-eq">{before} → {after}</span>'


def row(name, before, after, hint=""):
    b = "—" if before is None else str(before)
    a = "—" if after is None else str(after)
    delta = fmt_delta(before, after)
    hint_html = f'<div style="font-size:0.82em;color:#888;margin-top:2px">{hint}</div>' if hint else ""
    return (
        f"<tr><td class='metric-name'>{name}{hint_html}</td>"
        f"<td class='metric-cell'>{b}</td>"
        f"<td class='metric-cell'>{a}</td>"
        f"<td>{delta}</td></tr>"
    )


def render_html(after: dict) -> str:
    in_progress = not after.get("stage5_completed", False)
    progress_banner = ""
    if in_progress:
        progress_banner = (
            '<div class="banner warn">⏳ <b>아직 진행 중</b> — '
            f'현재 stage: <code>{after.get("pipeline_stage", "?")}</code> · '
            f'detail: {escape(after.get("stage5_detail", "") or "")}</div>'
        )
    elif "error" in after:
        progress_banner = f'<div class="banner warn">❌ {escape(after["error"])}</div>'
    else:
        progress_banner = '<div class="banner ok">✅ 파이프라인 완료</div>'

    notes_baseline = (
        '<div class="notes"><b>Baseline 분석 메모</b><ul>'
        + "".join(f"<li>{escape(n)}</li>" for n in BASELINE["notes"])
        + "</ul></div>"
    )

    rows_html = "".join([
        row("Framework", BASELINE["framework"], after.get("framework")),
        row("Package", BASELINE["package"], after.get("package")),
        row("정적 activity 수 (manifest)", BASELINE["static_activities"], after.get("static_activities"),
            "Stage 2 결과 — 매니페스트의 activity"),
        row("Wireframe ScreenMap 노드 (Stage 2.5)", BASELINE["wireframe_nodes"], after.get("wireframe_nodes")),
        row("탐색 state 캡처 수", BASELINE["walk_screens"], after.get("walk_screens")),
        row("unique structure_str 개수 ← 다양성", BASELINE["unique_struct_hashes"], after.get("unique_struct_hashes"),
            "Stage 3 의 화면 다양성. ABCD 의 B (stall) 와 D (synth) 양쪽에 영향"),
        row("최대 collapse 수 (1 hash 가 점유)", BASELINE["max_collapse_count"], after.get("max_collapse_count"),
            "이게 작아질수록 stall 탈출 잘 된 것"),
        row("unique screenshot 개수", BASELINE["screenshot_count_unique"], after.get("screenshot_count_unique")),
        row("최종 ScreenMap 총 노드", BASELINE["screenmap_total_nodes"], after.get("screenmap_total_nodes"),
            "사용자 핵심 관심 — '9 → 더 많이'"),
        row("ScreenMap 의 walk source 엣지", BASELINE["screenmap_walk_transitions"], after.get("screenmap_walk_transitions"),
            "0이면 dynamic 결과가 ScreenMap 에 안 들어간 것"),
        row("ScreenMap 의 static source 엣지", BASELINE["screenmap_static_edges"], after.get("screenmap_static_edges")),
        row("Synthesized 노드 (D 효과)", BASELINE["synthesized_nodes"], after.get("synthesized_nodes"),
            "walk_synthesized 표시된 노드 — D 옵션 직접 측정"),
        row("Stall reset 발생 (B 효과)", BASELINE["stall_resets"], after.get("stall_resets"),
            "[STALL] 로그 카운트 — 0이면 stall 자체가 없었거나 미적용"),
        row("Login pause 발생 (C 효과)", BASELINE["login_pauses"], after.get("login_pauses"),
            "PAUSED 로그 카운트"),
        row("Stage 5 (LLM) 완료", BASELINE["stage5_completed"], after.get("stage5_completed")),
    ])

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<title>ABCD 패치 RN 성능 비교</title>
<style>{CSS}</style></head><body>

<div class="cover">
  <h1>🧬 ABCD 패치 RN 성능 비교</h1>
  <div class="sub">Mattermost (com.mattermost.rn) — Before / After ABCD 적용</div>
  <div class="meta">2026-04-27 · feature/ScreenMap-POC · 실기기 R5CT20G1ZFL</div>
</div>

{progress_banner}

<h2>📊 핵심 메트릭 비교</h2>
<table class="cmp">
  <thead><tr><th>메트릭</th><th>Before (baseline)</th><th>After (ABCD)</th><th>Delta</th></tr></thead>
  <tbody>{rows_html}</tbody>
</table>

<h2>🛠 ABCD 옵션 정리</h2>
<table class="cmp">
  <thead><tr><th>옵션</th><th>적용 위치</th><th>예상 효과</th><th>측정 메트릭</th></tr></thead>
  <tbody>
  <tr><td><b>A</b> RN 번들 정적 추출</td><td><code>stage2_manifest/rn_bundle_extractor.py</code></td>
      <td>JSC 텍스트 번들 앱: routes 다수 추가 / Hermes: 효과 제한적</td>
      <td>정적 activity + routes</td></tr>
  <tr><td><b>B</b> Stall detection</td><td><code>stage3_walk/tap_walker.py</code></td>
      <td>같은 hash 6회 연속 → hard reset (한 run 최대 3회)</td>
      <td>max_collapse_count ↓ · stall_resets ≥ 1</td></tr>
  <tr><td><b>C</b> Login guard 강화</td><td><code>stage3_walk/mixins/guards.py</code></td>
      <td>한국어 키워드 + EditText ≥ 3 dense form → pause</td>
      <td>login_pauses</td></tr>
  <tr><td><b>D</b> Walk 노드 합성</td><td><code>stage6_screenmap/walk_transitions.py</code></td>
      <td>RN/Flutter framework auto-on. 미해결 transition endpoint → 신규 노드</td>
      <td>synthesized_nodes ≥ 1 · screenmap_total_nodes ↑</td></tr>
  </tbody>
</table>

<h2>🔍 해석 가이드</h2>
{notes_baseline}
<div class="notes">
  <b>읽는 법</b>:
  <ul>
    <li>screenmap_total_nodes 가 9 → 그 이상으로 증가하면 D 동작 (RN auto-on)</li>
    <li>max_collapse_count 가 75 → 작아지면 B 동작 (stall 풀림)</li>
    <li>stall_resets ≥ 1 이면 실제 reset 발생</li>
    <li>login_pauses ≥ 1 이면 C 가 Mattermost 의 server-URL 폼 잡음</li>
    <li>screenmap_walk_transitions 가 0 → 그 이상이면 dynamic 결과가 ScreenMap 에 합쳐짐 (D + 기존 _inject 합작)</li>
  </ul>
</div>

</body></html>
"""


def main():
    tour_id = sys.argv[1] if len(sys.argv) > 1 else ""
    if not tour_id:
        print("Usage: python scripts/perf_compare_rn.py <tour_id>")
        sys.exit(1)
    after = collect_metrics(tour_id)
    html = render_html(after)
    out = REPO / "reports" / "screenatlas_abcd_perf.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out} ({len(html):,} bytes)")
    # 콘솔에 핵심 메트릭 요약
    print()
    print(f"=== {tour_id} 메트릭 요약 ===")
    for k in ("framework", "package", "pipeline_stage", "static_activities",
              "walk_screens", "unique_struct_hashes", "max_collapse_count",
              "screenmap_total_nodes", "synthesized_nodes", "stall_resets", "login_pauses",
              "screenmap_walk_transitions", "screenmap_static_edges", "stage5_completed"):
        v = after.get(k)
        if v is not None:
            print(f"  {k:30} = {v}")


if __name__ == "__main__":
    main()
