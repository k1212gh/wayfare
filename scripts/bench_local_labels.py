"""로컬 LLM 모델별 라벨링 벤치마크 — 같은 ScreenMap 기준점에서 텍스트 후보 선택 + 비전 라벨링 비교.

    PYTHONPATH=. python scripts/bench_local_labels.py --tour ab_frontier_v3_0912_1652 \
        --models "qwen2.5:7b-instruct|qwen2.5vl:7b" "qwen3.5:9b" "gemma4:12b"

모델 인자: "텍스트모델|비전모델" (비전 생략 시 같은 모델, "-" 면 비전 테스트 건너뜀). 각 모델마다
  workspace/bench_<모델>/ 에 기준점 ScreenMap 을 복사해 (1) grounded 라벨 선택, (2) 비전 라벨러를
  독립적으로 실행하고 시간·성공률·라벨을 기록한다. 결과 tour 는 대시보드에서 그래프로 열 수 있다.

정답(gold): workspace/ 또는 docs/ 의 bench_gold_<app>.json 이 하나면 자동 채점 (--gold 로 지정 가능).
  {"<screen_id>": {"best": [후보번호…, -1=제목 없음], "acceptable": [관대 채점 허용 번호]}}

출력: workspace/bench_labels_<ts>.json, workspace/bench_labels_<ts>.md
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WORKSPACE = ROOT / "workspace"
logger = logging.getLogger("bench")


def safe_name(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", model).strip("_")


def prepare_bench_tour(src_tour: str, name: str, base_map: Path) -> Path:
    """기준점 ScreenMap 으로 bench tour 생성 (대시보드 표시용 pipeline_state 포함)."""
    d = WORKSPACE / name
    if d.exists():
        shutil.rmtree(d)
    (d / "output").mkdir(parents=True)
    src = WORKSPACE / src_tour
    for sub in ("apk", "static"):
        if (src / sub).exists():
            shutil.copytree(src / sub, d / sub, ignore=shutil.ignore_patterns("*.apk"))
    # 스크린샷은 투어 폴더 안에 있어야 대시보드가 보여준다 (백엔드가 폴더 밖 경로는 거부) → 복사 + ref 재작성
    if (src / "analysis" / "screens").exists():
        shutil.copytree(src / "analysis" / "screens", d / "analysis" / "screens")
    sm = json.loads(base_map.read_text(encoding="utf-8"))
    for n in sm["screen_map"]["graph"]["nodes"]:
        ref = n.get("screenshot_ref") or ""
        if ref:
            n["screenshot_ref"] = str(d.resolve() / Path(ref).relative_to(src.resolve())) if _under(ref, src) else ref
    (d / "output" / "screen_map.json").write_text(json.dumps(sm, indent=2, ensure_ascii=False), encoding="utf-8")
    state = json.loads((src / "pipeline_state.json").read_text(encoding="utf-8"))
    state.update({"tour_id": name, "stage": "SCREENMAP_GENERATED", "error": None,
                  "started_at": time.time(), "updated_at": time.time()})
    (d / "pipeline_state.json").write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return d


def _under(path: str, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def set_model_env(base_url: str, text_model: str, vision_model: str) -> None:
    os.environ["LLM_MODE"] = "openai"
    os.environ["LLM_BASE_URL"] = base_url
    os.environ["LLM_MODEL_SCREEN"] = text_model
    os.environ["LLM_MODEL_VISION"] = vision_model
    os.environ.setdefault("LLM_TIMEOUT", "600")


def load_nodes(p: Path) -> dict[str, dict]:
    g = json.loads(p.read_text(encoding="utf-8"))["screen_map"]["graph"]
    return {n["screen_id"]: n for n in g["nodes"]}


def warm_up(model: str) -> float:
    """모델 적재 시간을 측정에서 분리 (첫 호출)."""
    from stage5_annotate.llm_client import create_client
    c = create_client(max_retries=1)
    t = time.time()
    try:
        c.query_text("Reply with one word.", "ping", model=model, max_tokens=8)
    except Exception as e:  # noqa: BLE001
        logger.warning("warm-up failed for %s: %s", model, e)
    return time.time() - t


class _Recording:
    """실제 클라이언트를 감싸 query_json 원시 응답(배치별 시간·항목 수·pick)을 기록."""

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[dict] = []

    def query_json(self, system_prompt, user_prompt, **kw):
        t = time.time()
        try:
            resp = self.inner.query_json(system_prompt, user_prompt, **kw)
        except Exception as e:  # noqa: BLE001
            self.calls.append({"elapsed_s": round(time.time() - t, 1), "error": str(e)[:200], "items": {}})
            raise
        items = resp if isinstance(resp, list) else (resp.get("nodes") if isinstance(resp, dict) else None)
        if items is None and isinstance(resp, dict):
            items = [dict(v, id=k) for k, v in resp.items() if isinstance(v, dict)]
        picks = {}
        for it in items or []:
            if isinstance(it, dict) and it.get("id") is not None:
                pk = it.get("pick")
                if isinstance(pk, str) and pk.strip().lstrip("-").isdigit():
                    pk = int(pk)
                picks[str(it["id"])] = {"pick": pk, "category": it.get("category")}
        self.calls.append({"elapsed_s": round(time.time() - t, 1), "items": picks,
                           "shape": type(resp).__name__})
        return resp

    def __getattr__(self, name):
        return getattr(self.inner, name)


def score_picks(picks: dict[str, dict], gold: dict, base_nodes: dict) -> dict:
    """gold 대비 pick 채점. 대상: gold 에 있는 노드."""
    strict = lenient = answered = 0
    wrong: list[dict] = []
    ids = [i for i in gold if not i.startswith("_")]
    for sid in ids:
        g = gold[sid]
        pk = (picks.get(sid) or {}).get("pick")
        if pk is None:
            wrong.append({"id": sid, "pick": None, "why": "no answer"})
            continue
        answered += 1
        ok_best = pk in g["best"]
        ok_len = ok_best or pk in g.get("acceptable", [])
        strict += int(ok_best)
        lenient += int(ok_len)
        if not ok_len:
            cands = base_nodes.get(sid, {}).get("label_candidates") or []
            picked_text = cands[pk] if isinstance(pk, int) and 0 <= pk < len(cands) else "(none)"
            wrong.append({"id": sid, "pick": pk, "picked": picked_text, "gold": g["best"], "title": g.get("title")})
    n = len(ids)
    return {"n": n, "answered": answered, "strict": strict, "lenient": lenient,
            "strict_pct": round(100 * strict / max(n, 1)), "lenient_pct": round(100 * lenient / max(n, 1)),
            "wrong": wrong}


def run_pick(config, base_nodes: dict, gold: dict | None = None) -> dict:
    from stage5_annotate.label_picker import pick_labels
    from stage5_annotate.llm_client import create_client
    rec = _Recording(create_client(max_retries=config.llm_max_retries, temperature=config.llm_temperature))
    t = time.time()
    stats = pick_labels(config, client=rec)
    elapsed = time.time() - t
    picks: dict[str, dict] = {}
    for c in rec.calls:
        picks.update(c.get("items") or {})
    after = load_nodes(config.output_dir / "screen_map.json")
    changed = []
    for sid, n in after.items():
        b = base_nodes.get(sid, {})
        if n.get("label") != b.get("label") or n.get("functional_category") != b.get("functional_category"):
            changed.append({"id": sid, "before": b.get("label"), "after": n.get("label"),
                            "source": n.get("label_source"), "category": n.get("functional_category"),
                            "candidates": (n.get("label_candidates") or [])[:5]})
    out = {"elapsed_s": round(elapsed, 1), "stats": stats,
            "per_node_s": round(elapsed / max(stats.get("targets", 1), 1), 2),
            "changed": changed, "calls": rec.calls, "picks": picks,
            "answered": len(picks), "pick_none": sum(1 for v in picks.values() if v.get("pick") == -1),
            "label_source": dict(Counter(n.get("label_source") for n in after.values())),
            "category": dict(Counter(n.get("functional_category") for n in after.values()))}
    if gold:
        out["score"] = score_picks(picks, gold, base_nodes)
    return out


def run_vision(config, base_nodes: dict, limit: int) -> dict:
    """비전 라벨러를 기준점에서 독립 실행 (limit 노드만 — 대상 노드를 잘라 시간 제한)."""
    from stage5_annotate import vision_labeler as vl
    p = config.output_dir / "screen_map.json"
    sm = json.loads(p.read_text(encoding="utf-8"))
    nodes = sm["screen_map"]["graph"]["nodes"]
    cands = [n for n in nodes if n.get("screenshot_ref") and not str(n["screen_id"]).startswith("system:")]
    keep = {n["screen_id"] for n in cands[:limit]}
    # 대상 밖 노드는 이미 라벨된 것처럼 표시해 라벨러가 건너뛰게 함
    for n in nodes:
        if n.get("screenshot_ref") and n["screen_id"] not in keep:
            n["_skip_vision"] = True
            n["functional_category"] = n.get("functional_category") or "list"
            if n.get("functional_category") == "other":
                n["functional_category"] = "list"
            n["screen_purpose"] = n.get("screen_purpose") or "(bench skip)"
    p.write_text(json.dumps(sm, ensure_ascii=False), encoding="utf-8")
    t = time.time()
    ok = True
    err = ""
    try:
        vl.label_screens_with_vision(config)
    except Exception as e:  # noqa: BLE001
        ok = False
        err = str(e)[:200]
    elapsed = time.time() - t
    after = load_nodes(p)
    results = []
    labeled = 0
    for sid in keep:
        n = after.get(sid, {})
        got = bool(n.get("screen_purpose")) and n.get("screen_purpose") != "(bench skip)"
        labeled += int(got)
        results.append({"id": sid, "label": n.get("label"), "category": n.get("functional_category"),
                        "purpose": (n.get("screen_purpose") or "")[:80],
                        "affordances": (n.get("primary_affordances") or [])[:4],
                        "confidence": n.get("confidence"), "shot": Path(n.get("screenshot_ref") or "").name})
    return {"elapsed_s": round(elapsed, 1), "ok": ok, "error": err, "targets": len(keep), "labeled": labeled,
            "per_node_s": round(elapsed / max(len(keep), 1), 2), "results": results}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tour", default="")
    ap.add_argument("--models", nargs="+", default=[], help='"text|vision" 형식, 비전 생략 가능')
    ap.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--vision-limit", type=int, default=12)
    ap.add_argument("--skip-vision", action="store_true")
    ap.add_argument("--gold", default="", help="정답 JSON (기본: workspace/ 또는 docs/ 에 bench_gold_*.json 이 하나면 자동)")
    ap.add_argument("--rescore", default="", help="이미 만든 결과 JSON 을 --gold 로 다시 채점해 md 재생성")
    args = ap.parse_args()
    if args.rescore:
        rescore(Path(args.rescore), Path(args.gold) if args.gold else None)
        return
    if not args.tour or not args.models:
        ap.error("--tour 와 --models 가 필요합니다 (--rescore 가 아니면)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")

    from config import PipelineConfig

    src = WORKSPACE / args.tour
    base_map = src / "output" / "screen_map.before_llm.bench.json"
    if not base_map.exists():
        # 라벨 없는 기준점: 현재 ScreenMap 에서 LLM 라벨 흔적 제거본 생성
        sm = json.loads((src / "output" / "screen_map.json").read_text(encoding="utf-8"))
        for n in sm["screen_map"]["graph"]["nodes"]:
            if n.get("label_source") in ("picked", "llm"):
                cands = n.get("label_candidates") or []
                n["label"] = cands[0] if cands else n.get("label")
                n["label_source"] = "fallback"
            n.pop("primary_affordances", None)
            n.pop("data_displayed", None)
            n.pop("entry_hint", None)
            if n.get("label_source") == "fallback":
                n["screen_purpose"] = ""
                n["functional_category"] = "other"
                n["description"] = ""
        base_map.write_text(json.dumps(sm, indent=2, ensure_ascii=False), encoding="utf-8")
    base_nodes = load_nodes(base_map)
    logger.info("baseline: %d nodes (%s)", len(base_nodes), base_map.name)
    gold: dict | None = None
    gold_path = Path(args.gold) if args.gold else None
    if gold_path is None:
        found = sorted(WORKSPACE.glob("bench_gold_*.json")) or sorted((ROOT / "docs").glob("bench_gold_*.json"))
        gold_path = found[0] if len(found) == 1 else None
    if gold_path and gold_path.exists():
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        logger.info("gold: %s (%d nodes)", gold_path.name, sum(1 for k in gold if not k.startswith("_")))

    ts = time.strftime("%m%d_%H%M")
    report: dict = {"tour": args.tour, "base_url": args.base_url, "ts": ts, "gold": gold, "models": []}
    for spec in args.models:
        text_model, _, vision_model = spec.partition("|")
        skip_vision = args.skip_vision or vision_model == "-"
        vision_model = text_model if vision_model in ("", "-") else vision_model
        name = f"bench_{safe_name(text_model)}"
        entry: dict = {"text_model": text_model, "vision_model": vision_model, "tour": name}
        logger.info("=== %s (text=%s, vision=%s) ===", name, text_model, vision_model)
        set_model_env(args.base_url, text_model, vision_model)
        # (1) 후보 선택
        prepare_bench_tour(args.tour, name, base_map)
        cfg = PipelineConfig(apk_path="", tour_id=name, workspace_root=str(WORKSPACE))
        entry["warmup_s"] = round(warm_up(text_model), 1)
        try:
            entry["pick"] = run_pick(cfg, base_nodes, gold)
        except Exception as e:  # noqa: BLE001
            entry["pick"] = {"error": str(e)[:300]}
        # (2) 비전 (별도 기준점 사본에서)
        if not skip_vision:
            vname = name + "_vision"
            prepare_bench_tour(args.tour, vname, base_map)
            vcfg = PipelineConfig(apk_path="", tour_id=vname, workspace_root=str(WORKSPACE))
            if vision_model != text_model:
                entry["vision_warmup_s"] = round(warm_up(vision_model), 1)
            entry["vision"] = run_vision(vcfg, base_nodes, args.vision_limit)
            _mark_done(vname)
        _mark_done(name)
        report["models"].append(entry)
        (WORKSPACE / f"bench_labels_{ts}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    md = render_md(report, base_nodes)
    (WORKSPACE / f"bench_labels_{ts}.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"\nreport: {WORKSPACE / f'bench_labels_{ts}.md'}")


def rescore(report_path: Path, gold_path: Path | None) -> None:
    """결과 JSON 의 저장된 pick 을 새 gold 로 다시 채점하고 md 를 다시 쓴다 (LLM 재실행 없음)."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if gold_path is None or not gold_path.exists():
        raise SystemExit("--gold 경로가 필요합니다")
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    report["gold"] = gold
    src = WORKSPACE / report["tour"]
    base_map = src / "output" / "screen_map.before_llm.bench.json"
    base_nodes = load_nodes(base_map)
    for m in report["models"]:
        pk = m.get("pick") or {}
        if pk.get("picks") is not None:
            pk["score"] = score_picks(pk["picks"], gold, base_nodes)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md = render_md(report, base_nodes)
    md_path = report_path.with_suffix(".md")
    md_path.write_text(md, encoding="utf-8")
    print(md)
    print("rescored:", md_path)


def _mark_done(name: str) -> None:
    p = WORKSPACE / name / "pipeline_state.json"
    st = json.loads(p.read_text(encoding="utf-8"))
    st["stage"] = "ANNOTATED"
    st.setdefault("stages", {})["stage5"] = {"status": "done", "detail": "bench"}
    p.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")


def render_md(report: dict, base_nodes: dict) -> str:
    L = [f"# 로컬 LLM 라벨링 벤치마크 — {report['tour']} ({report['ts']})", ""]
    L.append("| 모델 (텍스트 / 비전) | 적재 | 후보 선택: 대상 / 시간 (노드당) | 응답 노드 | 정확도 strict / lenient | 실패 배치 | 비전: 노드 / 시간 (노드당) | 비전 성공 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for m in report["models"]:
        pk = m.get("pick", {}); vs = m.get("vision", {})
        st = pk.get("stats", {}); sc = pk.get("score", {})
        acc = f"{sc.get('strict','-')}/{sc.get('n','-')} ({sc.get('strict_pct','-')}%) / {sc.get('lenient','-')}/{sc.get('n','-')} ({sc.get('lenient_pct','-')}%)" if sc else "-"
        L.append(f"| {m['text_model']} / {m['vision_model'] if vs else '—'} | {m.get('warmup_s','-')}s | "
                 f"{st.get('targets','-')} / {pk.get('elapsed_s','-')}s ({pk.get('per_node_s','-')}s) | "
                 f"{pk.get('answered','-')} | {acc} | {st.get('failed_batches','-')} | "
                 + (f"{vs.get('targets','-')} / {vs.get('elapsed_s','-')}s ({vs.get('per_node_s','-')}s) | "
                    f"{vs.get('labeled','-')}/{vs.get('targets','-')}{' ERR' if not vs.get('ok', True) else ''} |" if vs else "— | — |"))
    L += ["", "- strict: gold `best` 와 일치, lenient: `acceptable` 까지 허용. 응답 노드: 모델이 JSON 에 답한 노드 수 (대상 중 후보가 없는 activity 노드 포함).",
          "- 후보 선택은 텍스트만(스크린샷 없음), 비전은 스크린샷 1장씩 (label/category/purpose 생성)."]
    # 후보 선택 라벨 비교 (page 노드)
    L += ["", "## 후보 선택 결과 (page 노드) — 모델별 pick → 적용 라벨 (category)", "",
          "표기: `#n` 모델이 고른 후보 번호, `✓` gold 일치, `~` lenient 일치, `✗` 오답, `?` 무응답. 라벨 뒤 `*` 는 휴리스틱 기준 라벨과 달라진 것.", ""]
    ids = [sid for sid in base_nodes if sid.startswith("page_") and base_nodes[sid].get("label_candidates")]
    gold = report.get("gold") or {}
    header = "| 화면 | 기준(휴리스틱) | " + " | ".join(m["text_model"] for m in report["models"]) + " |"
    L.append(header); L.append("|" + "---|" * (len(report["models"]) + 2))
    per_model = []
    for m in report["models"]:
        d = WORKSPACE / m["tour"] / "output" / "screen_map.json"
        per_model.append((load_nodes(d) if d.exists() else {}, (m.get("pick") or {}).get("picks") or {}))
    for sid in ids:
        base_lab = base_nodes[sid].get("label", "")[:16]
        g = gold.get(sid)
        row = [sid.replace("page_", "")[:6], base_lab]
        for nodes, picks in per_model:
            n = nodes.get(sid, {})
            lab = (n.get("label") or "")[:16]
            pk = (picks.get(sid) or {}).get("pick")
            if pk is None:
                mark = "?"
            elif g is None:
                mark = f"#{pk}"
            elif pk in g["best"]:
                mark = f"#{pk}✓"
            elif pk in g.get("acceptable", []):
                mark = f"#{pk}~"
            else:
                mark = f"#{pk}✗"
            star = "*" if lab != base_lab else ""
            row.append(f"{mark} {lab}{star} ({(n.get('functional_category') or '-')[:6]})")
        L.append("| " + " | ".join(row) + " |")
    # 오답 상세
    for m in report["models"]:
        sc = (m.get("pick") or {}).get("score")
        if not sc or not sc.get("wrong"):
            continue
        L += ["", f"### 오답/무응답 — {m['text_model']}", ""]
        for w in sc["wrong"]:
            if w.get("pick") is None:
                L.append(f"- {w['id']}: 무응답")
            else:
                L.append(f"- {w['id']}: pick={w['pick']} → \"{w.get('picked')}\" (정답 {w.get('gold')}: {w.get('title')})")
    # 배치별 시간
    L += ["", "## 배치별 응답 시간 (후보 선택, 20노드/배치)", ""]
    for m in report["models"]:
        calls = (m.get("pick") or {}).get("calls") or []
        L.append(f"- {m['text_model']}: " + ", ".join(
            (f"{c['elapsed_s']}s/{len(c.get('items') or {})}개" + (" ERR" if c.get("error") else "")) for c in calls))
    # 비전 샘플
    for m in report["models"]:
        vs = m.get("vision")
        if not vs:
            continue
        L += ["", f"## 비전 라벨 샘플 — {m['vision_model']}", ""]
        L.append("| 스크린샷 | label | category | purpose |"); L.append("|---|---|---|---|")
        for r in vs["results"][:12]:
            L.append(f"| {r['shot']} | {r['label']} | {r['category']} | {r['purpose']} |")
    return "\n".join(L)


if __name__ == "__main__":
    main()
