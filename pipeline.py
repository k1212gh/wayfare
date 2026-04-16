"""Pipeline orchestrator with stage-level state management."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

from config import PipelineConfig

logger = logging.getLogger(__name__)


class Stage(str, Enum):
    UPLOADED = "UPLOADED"
    STATIC_DONE = "STATIC_DONE"
    WALK_DONE = "WALK_DONE"
    CARDS_READY = "CARDS_READY"
    ANALYSIS_DONE = "ANALYSIS_DONE"
    SCREENMAP_GENERATED = "SCREENMAP_GENERATED"
    VISUALIZED = "VISUALIZED"

    # Failure states
    INSTALL_FAILED = "INSTALL_FAILED"
    STATIC_FAILED = "STATIC_FAILED"
    WALK_FAILED = "WALK_FAILED"
    CARDS_FAILED = "CARDS_FAILED"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"
    SCREENMAP_FAILED = "SCREENMAP_FAILED"


@dataclass
class PipelineState:
    """Persisted state for a pipeline run."""

    tour_id: str
    stage: Stage = Stage.UPLOADED
    apk_path: str = ""
    package_name: str = ""
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: Optional[str] = None

    def save(self, path: Path) -> None:
        self.updated_at = time.time()
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "PipelineState":
        data = json.loads(path.read_text(encoding="utf-8"))
        data["stage"] = Stage(data["stage"])
        return cls(**data)


class Pipeline:
    """Runs the 6-stage pipeline with checkpoint/resume support."""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.state_file = config.tour_dir / "pipeline_state.json"
        self.state: PipelineState | None = None

    def _load_or_create_state(self) -> PipelineState:
        if self.state_file.exists():
            return PipelineState.load(self.state_file)
        return PipelineState(tour_id=self.config.tour_id, apk_path=self.config.apk_path)

    def _save_state(self) -> None:
        if self.state:
            self.state.save(self.state_file)

    def _set_stage(self, stage: Stage, error: str | None = None) -> None:
        assert self.state is not None
        self.state.stage = stage
        self.state.error = error
        self._save_state()
        logger.info("Pipeline stage → %s", stage.value)

    # ------------------------------------------------------------------
    # Individual stage runners (import lazily to avoid circular deps)
    # ------------------------------------------------------------------

    def _run_stage1(self) -> None:
        from stage1_install import run_stage1
        run_stage1(self.config)

    def _run_stage2(self) -> None:
        from stage2_manifest import run_stage2
        run_stage2(self.config)

    def _run_stage3(self) -> None:
        from stage3_walk import run_stage3
        run_stage3(self.config)

    def _run_stage4(self) -> None:
        from stage4_screens import run_stage4
        run_stage4(self.config)

    def _run_stage5(self) -> None:
        from stage5_annotate import run_stage5
        run_stage5(self.config)

    def _run_stage6(self) -> None:
        from stage6_screenmap import run_stage6
        run_stage6(self.config)

    # ------------------------------------------------------------------

    STAGE_ORDER = [
        (Stage.UPLOADED, "_run_stage1", Stage.UPLOADED, Stage.INSTALL_FAILED),
        (Stage.UPLOADED, "_run_stage2", Stage.STATIC_DONE, Stage.STATIC_FAILED),
        (Stage.STATIC_DONE, "_run_stage3", Stage.WALK_DONE, Stage.WALK_FAILED),
        (Stage.WALK_DONE, "_run_stage4", Stage.CARDS_READY, Stage.CARDS_FAILED),
        (Stage.CARDS_READY, "_run_stage5", Stage.ANALYSIS_DONE, Stage.ANALYSIS_FAILED),
        (Stage.ANALYSIS_DONE, "_run_stage6", Stage.SCREENMAP_GENERATED, Stage.SCREENMAP_FAILED),
    ]

    def run(self, from_stage: str | None = None) -> None:
        """Execute the full pipeline (or resume from a given stage)."""
        self.config.ensure_dirs()
        self.state = self._load_or_create_state()
        self._save_state()

        stages = [
            ("stage1", self._run_stage1, Stage.UPLOADED, Stage.INSTALL_FAILED),
            ("stage2", self._run_stage2, Stage.STATIC_DONE, Stage.STATIC_FAILED),
            ("stage3", self._run_stage3, Stage.WALK_DONE, Stage.WALK_FAILED),
            ("stage4", self._run_stage4, Stage.CARDS_READY, Stage.CARDS_FAILED),
            ("stage5", self._run_stage5, Stage.ANALYSIS_DONE, Stage.ANALYSIS_FAILED),
            ("stage6", self._run_stage6, Stage.SCREENMAP_GENERATED, Stage.SCREENMAP_FAILED),
        ]

        skip = from_stage is not None
        for name, runner, success_stage, fail_stage in stages:
            if skip:
                if name == from_stage:
                    skip = False
                else:
                    continue

            logger.info("▶ Running %s …", name)
            try:
                runner()
                self._set_stage(success_stage)
            except Exception as exc:
                logger.exception("Stage %s failed", name)
                self._set_stage(fail_stage, error=str(exc))
                raise

        logger.info("Pipeline complete → %s", self.state.stage.value)
