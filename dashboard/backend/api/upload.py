"""APK upload endpoints — `/api/upload` (single) and `/api/upload-multi` (split).

Extracted from server.py (refactor Step 2). No subprocess, no device state,
just file streaming + validation + seed pipeline_state.json.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from dashboard.backend.paths import WORKSPACE_ROOT

router = APIRouter()


@router.post("/api/upload")
async def upload_apk(file: UploadFile = File(...)):
    """Upload an APK and create a new tour.

    Streams in 1 MB chunks so memory stays flat for large APKs.
    Filename must be a plain basename (`foo.apk`); any path component
    (`../foo.apk`, `dir/foo.apk`) is rejected to prevent workspace
    traversal on write.
    """
    if not file.filename:
        raise HTTPException(400, "No file provided")
    # Reject path components in filename — prevents traversal outside workspace
    safe_name = Path(file.filename).name
    if not safe_name or safe_name != file.filename:
        raise HTTPException(400, "Invalid filename — provide a plain .apk basename")
    if not safe_name.endswith(".apk"):
        raise HTTPException(400, "Only .apk files accepted. For split APKs, upload the base.apk")

    tour_id = uuid.uuid4().hex[:8]
    tour_dir = WORKSPACE_ROOT / tour_id
    for d in ["apk", "static", "dynamic", "analysis", "output"]:
        (tour_dir / d).mkdir(parents=True, exist_ok=True)

    apk_path = tour_dir / "apk" / safe_name
    total_bytes = 0
    try:
        with open(apk_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                total_bytes += len(chunk)
    except Exception as e:
        # Cleanup partial file AND the just-created tour_dir to avoid orphans
        shutil.rmtree(tour_dir, ignore_errors=True)
        raise HTTPException(500, f"Failed to save APK: {e}")
    finally:
        await file.close()

    state = {
        "tour_id": tour_id,
        "stage": "UPLOADED",
        "apk_path": str(apk_path.resolve()),
        "package_name": "",
        "apk_filename": safe_name,
        "apk_size_mb": round(total_bytes / 1024 / 1024, 1),
        "started_at": time.time(),
        "updated_at": time.time(),
        "error": None,
    }
    (tour_dir / "pipeline_state.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    return {"tour_id": tour_id, "status": "uploaded", "filename": safe_name}


@router.post("/api/upload-multi")
async def upload_split_apks(files: list[UploadFile] = File(...)):
    """Upload split APKs (base.apk + split_config.*.apk). Analyzes base.apk.

    Every filename is validated up-front; if any entry has path components
    or a non-.apk extension, the entire request is rejected (no partial
    writes). `base.apk` must be present.
    """
    if not files:
        raise HTTPException(400, "No files provided")

    # Validate every filename up-front — reject the whole request if any
    # entry has path components or a non-.apk name. Keep (UploadFile, safe_name)
    # pairs so the save loop below doesn't need to redo validation.
    validated: list[tuple[UploadFile, str]] = []
    for f in files:
        if not f.filename:
            continue
        safe = Path(f.filename).name
        if safe != f.filename:
            raise HTTPException(400, f"Invalid filename (path components not allowed): {f.filename}")
        if not safe.endswith(".apk"):
            raise HTTPException(400, f"Only .apk files accepted: {f.filename}")
        validated.append((f, safe))

    if not validated:
        raise HTTPException(400, "No valid .apk files in upload")

    if not any(s == "base.apk" for _, s in validated):
        raise HTTPException(400, "No base.apk found in uploaded files")

    tour_id = uuid.uuid4().hex[:8]
    tour_dir = WORKSPACE_ROOT / tour_id
    for d in ["apk", "static", "dynamic", "analysis", "output"]:
        (tour_dir / d).mkdir(parents=True, exist_ok=True)

    # Save all APKs — any failure wipes the whole tour_dir to avoid orphans
    total_bytes = 0
    try:
        for f, safe in validated:
            dest = tour_dir / "apk" / safe
            with open(dest, "wb") as out:
                while True:
                    chunk = await f.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    total_bytes += len(chunk)
            await f.close()
    except Exception as e:
        shutil.rmtree(tour_dir, ignore_errors=True)
        raise HTTPException(500, f"Failed to save split APKs: {e}")

    base_path = tour_dir / "apk" / "base.apk"

    state = {
        "tour_id": tour_id,
        "stage": "UPLOADED",
        "apk_path": str(base_path.resolve()),
        "package_name": "",
        "apk_filename": f"{len(validated)} split APKs",
        "apk_size_mb": round(total_bytes / 1024 / 1024, 1),
        "started_at": time.time(),
        "updated_at": time.time(),
        "error": None,
    }
    (tour_dir / "pipeline_state.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    return {
        "tour_id": tour_id,
        "status": "uploaded",
        "filename": f"base.apk (+{len(validated)-1} splits)",
    }
