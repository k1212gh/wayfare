"""A/B 러너 tour 를 대시보드에서 보이게 pipeline_state.json 을 유지하는 보조 루프.

ab_walk_megacoffee.py 는 Stage 1~3 만 직접 호출하므로 대시보드가 읽는
workspace/<tour>/pipeline_state.json 이 없다. 이 스크립트가 workspace/ab_* 폴더를
주기적으로 훑어 상태 파일을 만들고 갱신한다.

    WALKING    — dynamic/walk.json 이 아직 없음 (탐색 중)
    WALK_DONE  — walk.json 생성됨

    PYTHONPATH=. python scripts/ab_state_sync.py [--device SERIAL] [--interval 15] [--once]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / "workspace"


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def sync_one(tour_dir: Path, device: str) -> str:
    state_p = tour_dir / "pipeline_state.json"
    st = _load(state_p) if state_p.exists() else {}
    now = time.time()
    meta = _load(tour_dir / "apk" / "metadata.json")
    apks = sorted((tour_dir / "apk").glob("*.apk")) if (tour_dir / "apk").exists() else []
    walk_p = tour_dir / "dynamic" / "walk.json"
    states_dir = tour_dir / "dynamic" / "states"
    n_states = len(list(states_dir.glob("state_*.json"))) if states_dir.exists() else 0

    started = st.get("started_at") or tour_dir.stat().st_ctime
    stages = st.get("stages") or {}
    for i in range(1, 7):
        stages.setdefault(f"stage{i}", {"status": "pending"})
    if (tour_dir / "apk" / "metadata.json").exists():
        stages["stage1"] = {"status": "done", "detail": ""}
    if (tour_dir / "static" / "analysis.json").exists():
        n_act = len(_load(tour_dir / "static" / "analysis.json").get("activities", []))
        stages["stage2"] = {"status": "done", "detail": f"{n_act} activities"}

    if walk_p.exists():
        walk = _load(walk_p)
        stage = "WALK_DONE"
        stats = walk.get("stats", {})
        stages["stage3"] = {
            "status": "done",
            "detail": f"{stats.get('unique_screens', 0)} screens, {stats.get('total_events', 0)} events "
                      f"({stats.get('termination_reason', '?')})",
            "duration_ms": int((stats.get("elapsed_seconds") or 0) * 1000),
        }
        error = None
    else:
        stage = "WALKING"
        s3 = stages.get("stage3") or {}
        stages["stage3"] = {
            "status": "running",
            "detail": f"TapWalker running... ({n_states} captures)",
            "started_at": s3.get("started_at") or started,
        }
        error = None

    out = {
        "tour_id": tour_dir.name,
        "stage": stage,
        "apk_path": str(apks[0]) if apks else "",
        "package_name": meta.get("package_name", "") or st.get("package_name", ""),
        "apk_filename": apks[0].name if apks else "",
        "apk_size_mb": round(sum(a.stat().st_size for a in apks) / 1e6, 1) if apks else 0,
        "started_at": started,
        "updated_at": now,
        "error": error,
        "stages": stages,
        "device_serial": device or st.get("device_serial", ""),
        "ab_variant": tour_dir.name.split("_")[1] if "_" in tour_dir.name else "",
    }
    state_p.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return stage


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="")
    ap.add_argument("--interval", type=int, default=15)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    while True:
        for d in sorted(WORKSPACE.glob("ab_*")):
            if d.is_dir():
                try:
                    stage = sync_one(d, args.device)
                    print(f"{time.strftime('%H:%M:%S')} {d.name}: {stage}", flush=True)
                except Exception as e:  # noqa: BLE001
                    print(f"{d.name}: sync failed: {e}", flush=True)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
