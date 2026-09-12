"""라벨링 방식 비교 — 같은 화면 집합에 4가지 방식을 적용해 최종 라벨 정확도를 잰다.

    PYTHONPATH=. python scripts/bench_label_methods.py --tour 358002fe \
        --gold docs/bench_gold_megacoffee_358002fe.json --models gemma4:12b qwen3.5:9b

방식:
  pick_text   : 화면에 보이는 텍스트 후보(≤8) 중 번호 하나를 고른다 (현재 기본, label_picker 와 같은 프롬프트)
  free_text   : 같은 텍스트 후보를 보고 라벨을 자유롭게 짓는다 (≤12자)
  free_vision : 스크린샷만 보고 라벨을 자유롭게 짓는다
  pick_vision : 스크린샷 + 후보 목록 → 번호를 고른다 (없으면 -1)

채점 (정답표 label_gold, 공백 제거·소문자 후 포함 관계):
  최종 라벨 정확도 = 노드가 최종적으로 갖게 되는 라벨이 정답 제목과 맞는 비율.
    pick 계열은 pick=-1/무응답이면 휴리스틱 라벨(후보 0)이 남는 것까지 그대로 채점 → 실제 그래프에 보이는 결과 기준.
  pick 계열은 gold best/acceptable 로 번호 정확도도 같이 낸다.

출력: workspace/bench_methods_<ts>.json / .md
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
WORKSPACE = ROOT / "workspace"
logger = logging.getLogger("bench_methods")

from stage5_annotate.label_picker import _SYSTEM_PROMPT as PICK_SYSTEM, _build_batch_prompt, MAX_CANDIDATES, _GENERIC, _STORE_NAME  # noqa: E402
from stage5_annotate.screenmap_annotator import CATEGORY_ENUM  # noqa: E402

BATCH = 20
FREE_MAX = 12

FREE_TEXT_SYSTEM = (
    "You name screens of an Android app for a screen-flow map.\n"
    "For each screen you get: id, activity, and the texts actually visible on it, ordered top-to-bottom.\n"
    "Write the screen's title as a user would call it — the page header if there is one, otherwise a short "
    "descriptive name (Korean, at most 12 characters). Never use store/branch names, prices, dates or generic "
    "buttons (이전/닫기/확인). Do not append words like 화면/페이지.\n"
    "Also classify functional_category as one of: " + ", ".join(CATEGORY_ENUM) + ".\n\n"
    'Respond with strict JSON only: {"nodes": [{"id": "<screen id>", "label": "<title>", "category": "<enum>"}]}'
)
FREE_VISION_SYSTEM = (
    "You look at one Android app screenshot and name the screen for a screen-flow map.\n"
    "Answer with the screen's title as a user would call it — the page header if visible, or the dialog/sheet "
    "title if one is open on top, otherwise a short descriptive name. Korean, at most 12 characters. "
    "Do not append 화면/페이지. Never use store/branch names, prices or dates.\n"
    "Also classify functional_category as one of: " + ", ".join(CATEGORY_ENUM) + ".\n"
    'Respond with strict JSON only: {"label": "<title>", "category": "<enum>"}'
)
PICK_VISION_SYSTEM = (
    "You look at one Android app screenshot and a numbered list of texts extracted from that screen.\n"
    "Pick the ONE candidate that is the screen's title — the page header, or the title of a dialog/sheet "
    "open on top. Never pick store/branch names, prices, dates, list rows or generic buttons "
    "(이전/닫기/취소/확인). If no candidate is the title, answer pick=-1.\n"
    "Also classify functional_category as one of: " + ", ".join(CATEGORY_ENUM) + ".\n"
    'Respond with strict JSON only: {"pick": <int>, "category": "<enum>"}'
)


def norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).lower()


def label_matches(label: str, golds: list[str]) -> bool:
    l = norm(label)
    if len(l) < 2:
        return False
    for g in golds:
        gn = norm(g)
        if not gn:
            continue
        if l == gn or gn in l or (len(l) >= 2 and l in gn):
            return True
    return False


def load_base(tour: str) -> tuple[dict, list[dict]]:
    p = WORKSPACE / tour / "output" / "screen_map.before_llm.bench.json"
    if not p.exists():
        raise SystemExit(f"기준점이 없습니다: {p} (bench_local_labels.py 를 먼저 한 번 돌리면 생성됨)")
    sm = json.loads(p.read_text(encoding="utf-8"))
    nodes = [n for n in sm["screen_map"]["graph"]["nodes"] if str(n["screen_id"]).startswith("page_")]
    return sm, nodes


def cands_of(n: dict) -> list[str]:
    return [str(c) for c in (n.get("label_candidates") or [])][:MAX_CANDIDATES]


def apply_pick(n: dict, pick) -> tuple[str, str]:
    """pick → (최종 라벨, 출처). 피커와 같은 거부 규칙."""
    cands = cands_of(n)
    if isinstance(pick, str) and pick.strip().lstrip("-").isdigit():
        pick = int(pick)
    if isinstance(pick, int) and 0 <= pick < len(cands):
        chosen = cands[pick].strip()
        if chosen.lower() not in _GENERIC and len(chosen) <= 24 and not _STORE_NAME.match(chosen):
            return chosen, "picked"
    return n.get("label") or "", "fallback"


def apply_free(n: dict, label: str) -> tuple[str, str]:
    free = (label or "").strip()
    free = re.sub(r"\s*(화면|페이지)$", "", free).strip()
    if free and free.lower() not in _GENERIC:
        return free[:24], "llm"
    return n.get("label") or "", "fallback"


def screenshot_bytes(n: dict, tour: str) -> bytes | None:
    ref = n.get("screenshot_ref") or ""
    p = Path(ref)
    if not p.exists():
        for base in ("analysis/screens", "dynamic"):
            root = WORKSPACE / tour / base
            if root.exists():
                hit = next(root.rglob(p.name), None) if p.name else None
                if hit:
                    p = hit
                    break
    return p.read_bytes() if p.exists() else None


def run_text_method(client, nodes: list[dict], system: str, mode: str) -> tuple[dict, list[dict]]:
    """배치 텍스트 방식. 반환: {id: {"pick"|"label", "category"}}, 호출 기록."""
    out: dict[str, dict] = {}
    calls = []
    for s in range(0, len(nodes), BATCH):
        batch = nodes[s:s + BATCH]
        t = time.time()
        try:
            resp = client.query_json(system, _build_batch_prompt(batch), max_tokens=2048)
            items = resp if isinstance(resp, list) else (resp.get("nodes") if isinstance(resp, dict) else [])
            if items is None and isinstance(resp, dict):
                items = [dict(v, id=k) for k, v in resp.items() if isinstance(v, dict)]
            for it in items or []:
                if isinstance(it, dict) and it.get("id"):
                    out[str(it["id"])] = {"pick": it.get("pick"), "label": it.get("label"), "category": it.get("category")}
            calls.append({"elapsed_s": round(time.time() - t, 1), "n": len(items or [])})
        except Exception as e:  # noqa: BLE001
            calls.append({"elapsed_s": round(time.time() - t, 1), "error": str(e)[:160]})
            logger.warning("%s batch %d failed: %s", mode, s // BATCH, str(e)[:160])
    return out, calls


def run_vision_method(client, nodes: list[dict], tour: str, system: str, with_cands: bool, model: str) -> tuple[dict, list[dict]]:
    out: dict[str, dict] = {}
    calls = []
    for n in nodes:
        img = screenshot_bytes(n, tour)
        if not img:
            continue
        cands = cands_of(n)
        if with_cands:
            user = f"screen id: {n['screen_id']}\nCandidates:\n" + "\n".join(f"  [{i}] {c}" for i, c in enumerate(cands))
        else:
            user = f"screen id: {n['screen_id']}. Name this screen."
        t = time.time()
        try:
            txt = client.query_with_image(system, user, img, image_media_type="image/jpeg", model=model, max_tokens=200)
            from stage5_annotate.llm_client import parse_json_response
            try:
                d = parse_json_response(txt)
            except Exception:  # noqa: BLE001
                d = {"label": txt.strip()[:24]}
            out[n["screen_id"]] = {"pick": d.get("pick"), "label": d.get("label"), "category": d.get("category")}
            calls.append({"elapsed_s": round(time.time() - t, 1)})
        except Exception as e:  # noqa: BLE001
            calls.append({"elapsed_s": round(time.time() - t, 1), "error": str(e)[:160]})
    return out, calls


def score(method: str, nodes: list[dict], answers: dict, gold: dict) -> dict:
    rows = []
    n_total = final_ok = final_len = idx_strict = idx_len = answered = 0
    for n in nodes:
        sid = n["screen_id"]
        g = gold.get(sid)
        if not g:
            continue
        n_total += 1
        a = answers.get(sid) or {}
        if a:
            answered += 1
        if method.startswith("pick"):
            pk = a.get("pick")
            label, src = apply_pick(n, pk if pk is not None else -1)
            if isinstance(pk, str) and pk.strip().lstrip("-").isdigit():
                pk = int(pk)
            ok_s = pk in g["best"] if pk is not None else False
            ok_l = ok_s or (pk in g.get("acceptable", []) if pk is not None else False)
            idx_strict += int(ok_s); idx_len += int(ok_l)
        else:
            pk = None
            label, src = apply_free(n, a.get("label") or "")
        ok = label_matches(label, g.get("label_gold") or [])
        ok_len = ok or label_matches(label, g.get("label_gold_lenient") or g.get("label_gold") or [])
        final_ok += int(ok)
        final_len += int(ok_len)
        rows.append({"id": sid, "pick": pk, "label": label, "src": src, "ok": ok, "ok_lenient": ok_len,
                     "gold": (g.get("label_gold") or [""])[0], "category": a.get("category"), "raw_label": a.get("label")})
    return {"n": n_total, "answered": answered, "final_ok": final_ok, "final_pct": round(100 * final_ok / max(n_total, 1)),
            "final_lenient": final_len, "final_lenient_pct": round(100 * final_len / max(n_total, 1)),
            "idx_strict": idx_strict, "idx_lenient": idx_len,
            "idx_strict_pct": round(100 * idx_strict / max(n_total, 1)), "idx_lenient_pct": round(100 * idx_len / max(n_total, 1)),
            "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tour", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--models", nargs="+", required=True, help='"텍스트모델" 또는 "텍스트|비전"')
    ap.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--methods", nargs="+", default=["pick_text", "free_text", "free_vision", "pick_vision"])
    ap.add_argument("--rescore", default="", help="기존 결과 JSON 을 --gold 로 다시 채점해 md 재생성 (LLM 호출 없음)")
    args = ap.parse_args()
    if args.rescore:
        gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
        _, nodes = load_base(args.tour)
        targets = [n for n in nodes if n["screen_id"] in gold]
        rp = Path(args.rescore)
        report = json.loads(rp.read_text(encoding="utf-8"))
        for r in report["results"]:
            if "answers" not in r:
                # 구버전 결과: rows 의 raw 값으로 복원
                r["answers"] = {row["id"]: {"pick": row.get("pick"), "label": row.get("raw_label") or row.get("label"), "category": row.get("category")} for row in r["rows"]}
            sc = score(r["method"], targets, r["answers"], gold)
            r.update(sc)
        report["n"] = len(targets)
        rp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        md = render(report, targets, gold)
        rp.with_suffix(".md").write_text(md, encoding="utf-8")
        print(md)
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")

    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    _, nodes = load_base(args.tour)
    targets = [n for n in nodes if n["screen_id"] in gold]
    logger.info("targets: %d page nodes with gold", len(targets))

    os.environ["LLM_MODE"] = "openai"
    os.environ["LLM_BASE_URL"] = args.base_url
    os.environ.setdefault("LLM_TIMEOUT", "600")
    from stage5_annotate.llm_client import create_client

    ts = time.strftime("%m%d_%H%M")
    report: dict = {"tour": args.tour, "ts": ts, "gold": str(args.gold), "n": len(targets), "results": []}
    for spec in args.models:
        text_model, _, vision_model = spec.partition("|")
        vision_model = vision_model or text_model
        os.environ["LLM_MODEL_SCREEN"] = text_model
        os.environ["LLM_MODEL_VISION"] = vision_model
        client = create_client(max_retries=2)
        for method in args.methods:
            model = vision_model if method.endswith("vision") else text_model
            logger.info("=== %s / %s ===", model, method)
            t0 = time.time()
            if method == "pick_text":
                answers, calls = run_text_method(client, targets, PICK_SYSTEM, method)
            elif method == "free_text":
                answers, calls = run_text_method(client, targets, FREE_TEXT_SYSTEM, method)
            elif method == "free_vision":
                answers, calls = run_vision_method(client, targets, args.tour, FREE_VISION_SYSTEM, False, model)
            elif method == "pick_vision":
                answers, calls = run_vision_method(client, targets, args.tour, PICK_VISION_SYSTEM, True, model)
            else:
                raise SystemExit(f"unknown method {method}")
            elapsed = round(time.time() - t0, 1)
            sc = score(method, targets, answers, gold)
            sc.update({"model": model, "method": method, "elapsed_s": elapsed, "per_node_s": round(elapsed / max(len(targets), 1), 2),
                       "errors": sum(1 for c in calls if c.get("error")), "answers": answers})
            report["results"].append(sc)
            logger.info("%s/%s: final %d/%d (%d%%) idx %d/%d in %.1fs", model, method, sc["final_ok"], sc["n"], sc["final_pct"], sc["idx_lenient"], sc["n"], elapsed)
            (WORKSPACE / f"bench_methods_{ts}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    md = render(report, targets, gold)
    (WORKSPACE / f"bench_methods_{ts}.md").write_text(md, encoding="utf-8")
    print(md)
    print("report:", WORKSPACE / f"bench_methods_{ts}.md")


METHOD_LABEL = {"pick_text": "후보 선택 (텍스트)", "free_text": "자유 생성 (텍스트)",
                "free_vision": "자유 생성 (스크린샷)", "pick_vision": "후보 선택 (스크린샷+후보)"}


def render(report: dict, targets: list[dict], gold: dict) -> str:
    L = [f"# 라벨링 방식 비교 — {report['tour']} ({report['ts']}), page 노드 {report['n']}개", ""]
    L.append("| 모델 | 방식 | 최종 라벨 정확도 strict / lenient | 번호 정확도 strict / lenient | 응답 | 오류 | 시간 (노드당) |")
    L.append("|---|---|---|---|---|---|---|")
    for r in report["results"]:
        idx = f"{r['idx_strict']}/{r['n']} ({r['idx_strict_pct']}%) / {r['idx_lenient']}/{r['n']} ({r['idx_lenient_pct']}%)" if r["method"].startswith("pick") else "—"
        L.append(f"| {r['model']} | {METHOD_LABEL[r['method']]} | **{r['final_ok']}/{r['n']} ({r['final_pct']}%)** / {r['final_lenient']}/{r['n']} ({r['final_lenient_pct']}%) | {idx} | {r['answered']} | {r['errors']} | {r['elapsed_s']}s ({r['per_node_s']}s) |")
    L += ["", "- 최종 라벨 정확도: 노드가 최종적으로 갖는 라벨(pick=-1/무응답이면 휴리스틱 라벨 유지)이 정답 제목과 맞는 비율. 공백 제거·소문자 후 포함 관계. strict = 실제 제목(+best 후보), lenient = acceptable 후보까지.",
          "- 번호 정확도: 후보 선택 방식에서 고른 번호가 gold best(strict)/acceptable(lenient)과 맞는 비율."]
    # per-node table
    L += ["", "## 노드별 최종 라벨 (✓ strict 일치, ~ lenient 일치, ✗ 오답)", ""]
    keys = [(r["model"], r["method"]) for r in report["results"]]
    L.append("| 화면 | 정답 | " + " | ".join(f"{m}<br>{METHOD_LABEL[k]}" for m, k in keys) + " |")
    L.append("|" + "---|" * (len(keys) + 2))
    by = {(r["model"], r["method"]): {row["id"]: row for row in r["rows"]} for r in report["results"]}
    for n in targets:
        sid = n["screen_id"]
        cells = [sid[5:11], (gold[sid].get("label_gold") or [""])[0]]
        for k in keys:
            row = by[k].get(sid)
            if not row:
                cells.append("?")
                continue
            lab = (row["label"] or "")[:14]
            mark = "✓" if row["ok"] else ("~" if row.get("ok_lenient") else "✗")
            pk = f"#{row['pick']} " if row.get("pick") is not None else ""
            cells.append(f"{mark} {pk}{lab}")
        L.append("| " + " | ".join(cells) + " |")
    return "\n".join(L)


if __name__ == "__main__":
    main()
