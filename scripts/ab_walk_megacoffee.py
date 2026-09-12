"""A/B 탐색 실험 러너 — baseline vs 논문 기법 (Frontier / TarpitEscaper).

같은 기기·같은 APK 로 Stage 1~3 을 변형별로 순차 실행하고 walk.json 지표를 표로 비교한다.
Stage 4~6 / LLM 은 돌리지 않는다 (탐색 엔진 자체를 비교하는 실험이므로 API 키 불필요.
TarpitEscaper 변형만 ANTHROPIC_API_KEY 필요 — 없으면 자동으로 건너뜀).

사용 예 (메가커피가 이미 설치된 실기기):
    PYTHONPATH=. python scripts/ab_walk_megacoffee.py --device R5CT20G1ZFL \
        --pull-from-device co.kr.waldlust.megacoffee --timeout 900 --events 400

    # APK 파일/디렉터리 직접 지정
    PYTHONPATH=. python scripts/ab_walk_megacoffee.py --device R5CT20G1ZFL --apk path/to/base.apk

    # 이미 끝난 tour 들만 다시 표로
    PYTHONPATH=. python scripts/ab_walk_megacoffee.py --summarize-only ab_baseline_xxx ab_frontier_xxx

    # 기기 없이 계획만 확인
    PYTHONPATH=. python scripts/ab_walk_megacoffee.py --dry-run --apk x.apk --device none

변형 (--variants 로 선택, 기본 baseline,frontier):
    baseline          : 모든 기법 off (baseline-v0 와 동일 동작)
    frontier          : WALK_FRONTIER=1
    frontier_v2       : WALK_FRONTIER=1 + WALK_FRONTIER_PREEMPT=3 (stall 3회면 inert 클릭 대신 이동)
    frontier_v3       : v2 + WALK_FRONTIER_REPOSITION=1 (목표 없으면 Back/재실행 후 재탐색)
    frontier_tarpit   : WALK_FRONTIER=1 + TARPIT_LLM_ESCAPE=1 (API 키 필요)
    tarpit            : TARPIT_LLM_ESCAPE=1 만
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("ab_walk")

VARIANTS: dict[str, dict[str, str]] = {
    "baseline": {},
    "frontier": {"WALK_FRONTIER": "1"},
    "frontier_v2": {"WALK_FRONTIER": "1", "WALK_FRONTIER_PREEMPT": "3"},
    "frontier_v3": {"WALK_FRONTIER": "1", "WALK_FRONTIER_PREEMPT": "3", "WALK_FRONTIER_REPOSITION": "1"},
    "tarpit": {"TARPIT_LLM_ESCAPE": "1"},
    "frontier_tarpit": {"WALK_FRONTIER": "1", "TARPIT_LLM_ESCAPE": "1"},
}
TECHNIQUE_FLAGS = ("WALK_FRONTIER", "WALK_FRONTIER_PREEMPT", "WALK_FRONTIER_REPOSITION",
                   "TARPIT_LLM_ESCAPE", "COALESCE_LEARNED")
WORKSPACE = ROOT / "workspace"


# ── APK 준비 ──────────────────────────────────────────────────

def pull_from_device(serial: str, package: str) -> Path:
    """기기에 설치된 앱의 base.apk (+ split) 를 workspace/_apk_cache/<pkg>/ 로 가져온다."""
    dest = WORKSPACE / "_apk_cache" / package
    dest.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["adb", "-s", serial, "shell", "pm", "path", package],
                       capture_output=True, text=True, timeout=30)
    paths = [ln.split(":", 1)[1].strip() for ln in r.stdout.splitlines() if ln.startswith("package:")]
    if not paths:
        raise SystemExit(f"'{package}' 가 기기에 설치돼 있지 않습니다 (pm path 결과 없음): {r.stderr.strip()}")
    for remote in paths:
        local = dest / Path(remote).name
        if local.exists() and local.stat().st_size > 0:
            continue
        logger.info("pull %s -> %s", remote, local)
        subprocess.run(["adb", "-s", serial, "pull", remote, str(local)], check=True, timeout=600)
    base = dest / "base.apk"
    if not base.exists():
        # 단일 APK 앱은 파일명이 base.apk 가 아닐 수 있음
        apks = sorted(dest.glob("*.apk"))
        if not apks:
            raise SystemExit("pull 된 APK 가 없습니다")
        base = apks[0]
    logger.info("APK ready: %s (%d file(s))", base, len(list(dest.glob('*.apk'))))
    return base


def resolve_apk(args) -> Path:
    if args.pull_from_device:
        return pull_from_device(args.device, args.pull_from_device)
    if not args.apk:
        raise SystemExit("--apk 또는 --pull-from-device 가 필요합니다")
    p = Path(args.apk)
    if p.is_dir():
        base = p / "base.apk"
        if not base.exists():
            apks = sorted(p.glob("*.apk"))
            if not apks:
                raise SystemExit(f"{p} 에 APK 없음")
            base = apks[0]
        return base
    if not p.exists():
        raise SystemExit(f"APK 없음: {p}")
    return p


# ── 변형 실행 ─────────────────────────────────────────────────

def _set_env(overrides: dict[str, str]) -> dict[str, str | None]:
    saved = {k: os.environ.get(k) for k in TECHNIQUE_FLAGS}
    for k in TECHNIQUE_FLAGS:
        os.environ.pop(k, None)
    os.environ.update(overrides)
    return saved


def _restore_env(saved: dict[str, str | None]) -> None:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def run_variant(name: str, apk: Path, device: str, timeout: int, events: int, ts: str) -> str:
    from config import PipelineConfig
    from stage1_install import run_stage1
    from stage2_manifest import run_stage2
    from stage3_walk import run_stage3
    from stage6_screenmap.wireframe_builder import write_wireframe

    tour_id = f"ab_{name}_{ts}"
    overrides = dict(VARIANTS[name])
    if "TARPIT_LLM_ESCAPE" in overrides:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key or "PLACEHOLDER" in key:
            logger.warning("[%s] ANTHROPIC_API_KEY 없음 — TarpitEscaper 는 자동 비활성 (frontier 만 적용)", name)
    saved = _set_env(overrides)
    try:
        # 실기기에서는 재설치하지 않도록 is_emulator=False (로그인 상태 보존)
        config = PipelineConfig(
            apk_path=str(apk), tour_id=tour_id, workspace_root=str(WORKSPACE),
            device_serial=device, is_emulator=device.startswith("emulator"),
        )
        config.droidbot_timeout = timeout
        config.ensure_dirs()
        logger.info("=== [%s] tour=%s env=%s timeout=%ds events=%d ===", name, tour_id, overrides, timeout, events)
        t0 = time.time()
        run_stage1(config)
        run_stage2(config)
        meta = json.loads((config.apk_dir / "metadata.json").read_text(encoding="utf-8"))
        static_info = json.loads((config.static_dir / "analysis.json").read_text(encoding="utf-8"))
        try:
            write_wireframe(config, static_info, meta)
        except Exception as e:  # noqa: BLE001
            logger.warning("wireframe build failed (walk 는 계속): %s", e)
        # run_stage3 → walker_dispatcher 의 event_count 기본값(800) 대신 인자 반영
        from stage3_walk import walker_dispatcher
        orig_run = walker_dispatcher.run_droidbot

        def _patched(**kw):
            kw["event_count"] = events
            return orig_run(**kw)

        import stage3_walk as s3
        s3.run_droidbot = _patched
        try:
            run_stage3(config)
        finally:
            s3.run_droidbot = orig_run
        logger.info("[%s] done in %.0fs", name, time.time() - t0)
    finally:
        _restore_env(saved)
    return tour_id


# ── 요약 ──────────────────────────────────────────────────────

METRICS = [
    ("events", "total_events"), ("screens", "unique_screens"), ("raw", "unique_screens_raw"),
    ("elapsed_s", "elapsed_seconds"), ("scr/min", "screens_per_minute"),
    ("term", "termination_reason"), ("auth_backoff", "auth_backoff_count"),
]


def summarize(tour_id: str) -> dict:
    d = WORKSPACE / tour_id
    walk_p = d / "dynamic" / "walk.json"
    if not walk_p.exists():
        return {"tour": tour_id, "error": "walk.json 없음"}
    walk = json.loads(walk_p.read_text(encoding="utf-8"))
    st = walk.get("stats", {})
    cov = walk.get("coverage", {})
    row = {"tour": tour_id}
    for label, key in METRICS:
        row[label] = st.get(key)
    row["activities"] = len(walk.get("activities_found", []))
    row["transitions"] = len(walk.get("transitions", []))
    row["coverage"] = round(cov.get("ratio", 0) or 0, 3)
    mr = st.get("must_reach") or {}
    row["must_reach"] = f"{len(mr.get('hit', []))}/{mr.get('total', 0)}" if mr.get("total") else "-"
    fr = st.get("frontier") or {}
    row["nav_ok/att"] = f"{fr.get('nav_success', 0)}/{fr.get('nav_attempts', 0)}" if fr else "-"
    row["repos"] = f"{fr.get('reposition_back', 0)}b/{fr.get('reposition_relaunch', 0)}r" if fr else "-"
    tp = st.get("tarpit") or {}
    row["tarpit"] = f"{tp.get('escapes', 0)} ({tp.get('calls_used', 0)} calls)" if tp else "-"
    return row


def print_table(rows: list[dict]) -> None:
    cols = ["tour", "events", "screens", "raw", "activities", "transitions", "coverage",
            "must_reach", "scr/min", "elapsed_s", "term", "auth_backoff", "nav_ok/att", "repos", "tarpit"]
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    line = " | ".join(c.ljust(widths[c]) for c in cols)
    print(line)
    print("-" * len(line))
    for r in rows:
        print(" | ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))


# ── main ──────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default="", help="ADB serial (예: R5CT20G1ZFL)")
    ap.add_argument("--apk", default="", help="APK 파일 또는 split APK 디렉터리")
    ap.add_argument("--pull-from-device", default="", metavar="PACKAGE",
                    help="기기에 설치된 앱의 APK 를 pull 해서 사용 (예: co.kr.waldlust.megacoffee)")
    ap.add_argument("--variants", default="baseline,frontier", help=f"쉼표 구분: {','.join(VARIANTS)}")
    ap.add_argument("--timeout", type=int, default=900, help="변형당 탐색 시간(초), 기본 900")
    ap.add_argument("--events", type=int, default=400, help="변형당 최대 이벤트, 기본 400")
    ap.add_argument("--summarize-only", nargs="*", metavar="TOUR_ID", help="이미 끝난 tour 만 표로")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        datefmt="%H:%M:%S")

    if args.summarize_only:
        rows = [summarize(t) for t in args.summarize_only]
        print_table(rows)
        return

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in variants if v not in VARIANTS]
    if unknown:
        raise SystemExit(f"알 수 없는 변형: {unknown} (가능: {list(VARIANTS)})")
    if not args.device:
        raise SystemExit("--device 필요")

    if args.dry_run:
        print("계획:")
        for v in variants:
            print(f"  {v:16s} env={VARIANTS[v] or '{}'}  timeout={args.timeout}s events={args.events}")
        print(f"  device={args.device} apk={args.apk or ('pull:' + args.pull_from_device)}")
        return

    apk = resolve_apk(args)
    ts = time.strftime("%m%d_%H%M")
    tours: list[str] = []
    for v in variants:
        try:
            tours.append(run_variant(v, apk, args.device, args.timeout, args.events, ts))
        except KeyboardInterrupt:
            logger.warning("중단됨 — 지금까지 결과만 요약")
            break
        except Exception as e:  # noqa: BLE001
            logger.exception("[%s] 실패: %s", v, e)
    rows = [summarize(t) for t in tours]
    print()
    print_table(rows)
    report = WORKSPACE / f"ab_report_{ts}.json"
    report.write_text(json.dumps({"variants": variants, "apk": str(apk), "device": args.device,
                                  "timeout": args.timeout, "events": args.events, "rows": rows},
                                 indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nreport: {report}")


if __name__ == "__main__":
    main()
