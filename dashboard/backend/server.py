"""FastAPI server for the ScreenAtlas dashboard."""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# uvicorn sets up its own loggers but doesn't configure the root logger, so
# `logger.info(...)` from our application code (stage3/5/6 etc) stays silent
# unless we add a handler. We install one that writes to stderr at INFO level,
# which uvicorn captures into its stdout pipe → our backend.log.
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=False,  # don't clobber uvicorn's access/error handlers if re-imported
    )
    # Silence noisy libraries
    for noisy in ("androguard", "androguard.core", "PIL", "httpx", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

app = FastAPI(title="ScreenAtlas", version="0.2.0")


@app.on_event("startup")
def _recover_stale_running_tours() -> None:
    """백엔드 재시작 시 stale 'running' 잡 자동 복구.

    이전 backend 가 죽으면 thread 도 함께 죽음. pipeline_state.json 은 그대로
    'running' 표시 — 사용자가 Stop 눌러도 read 할 thread 없어 무한 phantom.
    Startup 시점에 마지막 update 가 충분히 오래됐고 (5분+) 진짜 thread 가
    없는 잡들을 자동으로 CANCELLED 처리.
    """
    import json
    import time
    from pathlib import Path
    from dashboard.backend.paths import WORKSPACE_ROOT, _cancel_path, _pause_path

    STALE_THRESHOLD_SEC = 300  # 5분 이상 update 없으면 stale 로 본다
    RUNNING_STAGES = {
        "PREPROCESSING", "STATIC_ANALYZING", "WALKING", "WALK_DONE",
        "PREPROCESSING_DATA", "CARDS_READY", "BUILDING_SCREENMAP", "LLM_ANNOTATING",
        "LLM_ANALYZING",
    }
    if not WORKSPACE_ROOT.exists():
        return
    recovered = 0
    now = time.time()
    for tour_dir in WORKSPACE_ROOT.iterdir():
        if not tour_dir.is_dir():
            continue
        sp = tour_dir / "pipeline_state.json"
        if not sp.exists():
            continue
        try:
            d = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            continue
        stage = d.get("stage", "")
        if stage not in RUNNING_STAGES:
            continue
        updated = d.get("updated_at", 0)
        if updated and (now - updated) < STALE_THRESHOLD_SEC:
            continue  # 진짜 진행 중일 가능성 — 보수적 (5분 이상 update 없을 때만)
        # Stale → recover
        stages = d.setdefault("stages", {})
        for s in stages.values():
            if isinstance(s, dict) and s.get("status") == "running":
                s["status"] = "done"
                s.setdefault("duration_ms", 0)
        # Stage 5/6 까지 완료된 흔적 있으면 ANNOTATED, 아니면 CANCELLED
        screenmap_done = (tour_dir / "output" / "screen_map.json").exists()
        d["stage"] = "ANNOTATED" if screenmap_done else "CANCELLED"
        d["error"] = "Auto-recovered from stale running state on startup"
        d["updated_at"] = now
        try:
            sp.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            continue
        # cancel.flag / paused.json 제거 (다음 run 깨끗하게)
        for p in (_cancel_path(tour_dir.name), _pause_path(tour_dir.name)):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        recovered += 1
    if recovered:
        import logging
        logging.getLogger(__name__).warning(
            "Recovered %d stale running tour(s) on startup", recovered)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Path + filesystem helpers moved to paths.py (refactor Step 2). Re-exported
# here so existing imports `from dashboard.backend.server import _safe_tour_dir`
# etc. keep working without churn across the codebase/tests.
from dashboard.backend.paths import (  # noqa: E402  (re-export shim)
    WORKSPACE_ROOT,
    _SAFE_ID,
    _safe_tour_dir,
    _within,
    _safe_rmtree,
    _cancel_path,
    _pause_path,
    _read_pause_info,
)

# Concurrency + cancellation state lives in services/tour_store.py.
# Re-exported here for backward compat — code that still reaches for
# `server._tour_lock` / `server._is_cancelled` continues to work.
from dashboard.backend.services import tour_store as _tour_store  # noqa: E402
from dashboard.backend.services.tour_store import is_cancelled as _is_cancelled  # noqa: E402
_tour_lock = _tour_store.get_lock()
_cancel_events = _tour_store._cancel_events  # same dict — mutations visible


# ─── APK Upload ───────────────────────────────────────────
# Routes moved to dashboard/backend/api/upload.py — included on `app` below.

from dashboard.backend.api.upload import router as _upload_router  # noqa: E402
app.include_router(_upload_router)


# ─── Pipeline Execution ──────────────────────────────────
# Orchestration moved to dashboard/backend/services/pipeline_service.py
# (refactor Step 2.5). server-local names kept for backward compat.
from dashboard.backend.services.pipeline_service import (  # noqa: E402
    run_pipeline_sync as _run_pipeline_sync,
    build_static_screenmap as _build_static_screenmap,
)


# Tour lifecycle (list / run / pause / resume / stop / DELETE) moved to
# dashboard/backend/api/tours.py (refactor Step 2.5).
from dashboard.backend.api.tours import router as _tours_router  # noqa: E402
app.include_router(_tours_router)


# ─── Emulator Control ─────────────────────────────────────
# Moved to dashboard/backend/api/emulator.py (and services/adb_service.py) —
# refactor Step 2.4. The helper names are re-exported below for callers
# that still import from server (e.g. pipeline_service / tests).
from dashboard.backend.api.emulator import router as _emulator_router  # noqa: E402
from dashboard.backend.services.adb_service import (  # noqa: E402
    _adb_prefix,
    _boot_completed,
    _check_adb_device,
    _find_emulator_bin,
    _get_first_device,
    _list_running_emulators,
    _running_avd_name,
)
app.include_router(_emulator_router)

_EMU_LOG_DIR = WORKSPACE_ROOT / "_emulator_logs"  # still referenced in tests / logs


# Device routes (tap/key/text/swipe/screenshot + /api/device info) live in
# dashboard/backend/api/device.py (refactor Step 2.4).
from dashboard.backend.api.device import router as _device_router  # noqa: E402
app.include_router(_device_router)


# Read-only tour-scoped endpoints (graph / plan / path / state / report /
# traces / static / cache-stats / walk-live / screenshot) moved to
# dashboard/backend/api/graph.py (refactor Step 2.3).
from dashboard.backend.api.graph import router as _graph_router  # noqa: E402
app.include_router(_graph_router)

# 2026-09-12: LLM 공급자 설정 (Claude API / 로컬 OpenAI 호환 / off)
from dashboard.backend.api.settings import router as _settings_router  # noqa: E402
app.include_router(_settings_router)


# Serve frontend static files — only in production (not dev mode with Vite)
# To enable: set SERVE_STATIC=1
import os as _os
if _os.environ.get("SERVE_STATIC") == "1":
    FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"
    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
