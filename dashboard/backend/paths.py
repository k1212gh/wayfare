"""Path + filesystem helpers for the backend.

Extracted from `server.py` (Step 2 of the refactor roadmap). Pure file
operations only — no subprocess, no FastAPI state, no concurrency primitives.
Those live in `services/tour_store.py` (to be added) and `services/adb_service.py`.

Exports:
  WORKSPACE_ROOT   — absolute path of the workspace dir (created if missing)
  _SAFE_ID         — regex for validating tour_id / screen_id query-path params
  _safe_tour_dir    — resolve + existence check + traversal guard (→ HTTP 400/404)
  _within          — "is path inside base" check, used for screenshot serving
  _safe_rmtree     — defensive rmtree that won't follow symlinks or leave the root
  _cancel_path     — path of `<tour>/cancel.flag` sentinel
  _pause_path      — path of `<tour>/paused.json` sentinel
  _read_pause_info — safely read paused.json or return None
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Resolved once at import — project_root/workspace/. Callers should use this
# constant rather than re-deriving via __file__ so every module agrees.
WORKSPACE_ROOT = (Path(__file__).parent.parent.parent / "workspace").resolve()
WORKSPACE_ROOT.mkdir(exist_ok=True)


# Tour/state IDs are used as path components; the regex rejects anything that
# could smuggle in a traversal (slashes, backslashes) or shell metachars.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _safe_tour_dir(tour_id: str) -> Path:
    """Resolve tour_id to a workspace subdir, rejecting path-traversal attempts.

    Raises 400 on malformed / traversal-style IDs, 404 if the dir doesn't exist.
    All tour-scoped endpoints should call this at entry.
    """
    if not _SAFE_ID.match(tour_id):
        raise HTTPException(400, "Invalid tour_id")
    tour_dir = (WORKSPACE_ROOT / tour_id).resolve()
    ws_root = WORKSPACE_ROOT.resolve()
    if ws_root not in tour_dir.parents and tour_dir != ws_root:
        raise HTTPException(400, "Invalid tour_id")
    if not tour_dir.exists():
        raise HTTPException(404, "Tour not found")
    return tour_dir


def _within(path: Path, base: Path) -> bool:
    """True if `path` resolves inside `base` (used for screenshot path safety)."""
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def _safe_rmtree(target: Path, must_be_under: Path | None = None) -> None:
    """Remove a directory tree with defensive checks.

    - No-op if `target` does not exist.
    - If `must_be_under` is given, refuse to operate unless `target` resolves
      inside that root (prevents callers from accidentally nuking anything
      outside the workspace).
    - If `target` itself is a symlink/junction, unlink the link only — never
      follow it into whatever it points to.
    - Symlinked directories *inside* the tree are not followed; Python's
      `shutil.rmtree` already behaves this way, but we pass `ignore_errors=True`
      so a malformed junction can't leave the caller in a half-cleaned state.
    """
    if not target.exists() and not target.is_symlink():
        return
    try:
        resolved = target.resolve(strict=False)
    except OSError:
        return

    if must_be_under is not None:
        try:
            resolved.relative_to(must_be_under.resolve(strict=False))
        except ValueError:
            logger.warning(
                "Refusing to _safe_rmtree %s: not under %s",
                resolved, must_be_under,
            )
            return

    # Follow-symlink-to-dir guard: if the entry point is itself a link, just
    # unlink the link and stop (so the link's target tree is preserved).
    if target.is_symlink():
        try:
            target.unlink()
        except OSError as e:
            logger.warning("Failed to unlink symlink %s: %s", target, e)
        return

    shutil.rmtree(target, ignore_errors=True)


def _cancel_path(tour_id: str) -> Path:
    """Sentinel file a cancel-requester writes. Walker polls for its existence."""
    return WORKSPACE_ROOT / tour_id / "cancel.flag"


def _pause_path(tour_id: str) -> Path:
    """Sentinel file for pause/resume state (written by /pause, deleted by /resume)."""
    return WORKSPACE_ROOT / tour_id / "paused.json"


def _read_pause_info(tour_id: str) -> dict | None:
    """Return paused.json contents, or None if not paused. Corrupt files fall
    back to a minimal synthetic payload so /api/tours never 500s on stale state."""
    p = _pause_path(tour_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"reason": "paused", "auto": False, "since": p.stat().st_mtime}
