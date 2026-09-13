"""에이전트 적합성 감사 — 지도가 에이전트에게 얼마나 쓸 만한지 숫자로 (docs/agent_readiness_plan.md 완료 기준).

    PYTHONPATH=. python scripts/audit_agent_map.py --tour 358002fe [--log workspace/_backend.err.log]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tour", required=True)
    ap.add_argument("--log", default="")
    args = ap.parse_args()
    tour = ROOT / "workspace" / args.tour
    sm = json.loads((tour / "output" / "screen_map.json").read_text(encoding="utf-8"))
    g = sm["screen_map"]["graph"]
    nodes, edges = g["nodes"], g["edges"]
    byid = {n["screen_id"]: n for n in nodes}
    pages = [n for n in nodes if n["screen_id"].startswith("page_")]
    lab = lambda i: byid.get(i, {}).get("label", i)

    print(f"# 지도 감사 — {args.tour}: 노드 {len(nodes)} (page {len(pages)}) · 엣지 {len(edges)}")
    # 1) 위젯 grounding
    w_nodes = sum(1 for n in pages if n.get("widgets"))
    w_all = [w for n in pages for w in (n.get("widgets") or [])]
    print(f"[#2 위젯] 위젯 보유 page {w_nodes}/{len(pages)} · 위젯 {len(w_all)} (인터랙티브 {sum(1 for w in w_all if w.get('action_types') or w.get('clickable'))}, 좌표 {sum(1 for w in w_all if w.get('bounds'))}, 입력 {sum(1 for w in w_all if w.get('editable'))})")
    walk = [e for e in edges if e.get("source") == "walk" and e.get("kind") != "back"]
    print(f"[#2 셀렉터] 관측 엣지 {len(walk)}: {dict(Counter((e.get('selector') or {}).get('by') for e in walk))}")
    # 4) 오버레이·중복
    pairs = Counter((e["from"], e["to"]) for e in walk)
    print(f"[#4 중복] 같은 from→to 중복 {sum(1 for v in pairs.values() if v > 1)} · frequency>1 {sum(1 for e in walk if (e.get('frequency') or 0) > 1)} · 오버레이 노드 {[lab(n['screen_id']) for n in pages if n.get('is_dialog')]}")
    # 1) 검색·데이터 화면
    ts = [e for e in edges if e.get("trigger_action") == "type_submit"]
    li = [e for e in edges if e.get("list_item")]
    dyn = [n for n in nodes if n.get("dynamic")]
    print(f"[#1 검색] type_submit 엣지 {len(ts)} (검색어 {sorted({e.get('input_value') for e in ts})}) · 행→상세 엣지 {len(li)} · 데이터 노드 {len(dyn)}")
    for n in dyn:
        d = n["dynamic"]
        ia = d.get("item_action") or {}
        print(f"   - {lab(n['screen_id'])!r}: {d.get('kind')} · 검색어 {d.get('queries')} · 검색창 {(d.get('query_field') or {}).get('text') or (d.get('query_field') or {}).get('resource_id') or '-'}"
              + (f" · 행 탭 → {lab(ia.get('to'))!r} (예: {ia.get('sample_items')})" if ia else ""))
    for e in ts:
        print(f"   · {lab(e['from'])!r} --입력 {e.get('input_value')!r}{' (빈 결과)' if e.get('outcome') == 'empty' or e.get('expect') == 'empty' else ''}--> {lab(e['to'])!r}")
    # 3) 전제조건
    req = sum(1 for n in nodes if n.get("requires"))
    cond = sum(1 for e in edges if e.get("condition"))
    print(f"[#3 전제조건] requires 노드 {req} · condition 엣지 {cond}")
    # 로그 기반: 자동 입력 횟수, 인증/결제 진입
    if args.log and Path(args.log).exists():
        log = Path(args.log).read_text(encoding="utf-8", errors="replace")
        last = log.rfind("[search] probe enabled")
        seg = log[last:] if last >= 0 else log
        probes = re.findall(r"\[search\] done on \S+: (\{.*?\})", seg)
        stats = [eval(p) for p in probes]  # noqa: S307 — 우리 로그
        print(f"[로그] 프로브 실행 {len(stats)}회 · 검색어 입력 {sum(s['queries'] for s in stats)} · 결과 {sum(s['results'] for s in stats)} · 상세 {sum(s['details'] for s in stats)} · 실패 {sum(s['failed'] for s in stats)} · 재시도 {sum(s.get('retries', 0) for s in stats)}")
        auth = len(re.findall(r"auth_backoff|\[auth\]", seg))
        pay = len(re.findall(r"payment|결제 화면|\[external\]", seg))
        print(f"[로그] 인증 화면 백오프 {auth} · 외부/결제 가드 {pay}")


if __name__ == "__main__":
    main()
