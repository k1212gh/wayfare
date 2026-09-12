"""Wayfare pipeline configuration."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import os
import uuid

# Load .env file if present.
# **Override** rather than setdefault: previously a bad shell-env export
# (e.g. LLM_MODEL_SCREEN=claude-sonnet-4-20250514 left by an older setup
# script run) would shadow the corrected value in .env, and LLM calls
# silently 404'd for hours. .env is the edit-and-expect-to-take-effect
# surface, so treat it as authoritative here. Shell exports are
# still honored when the key is NOT present in .env.
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ[key.strip()] = value.strip()


@dataclass
class PipelineConfig:
    """Top-level configuration for a pipeline run."""

    # APK input
    apk_path: str = ""

    # Device
    device_serial: str = "emulator-5554"
    is_emulator: bool = True

    # Workspace
    workspace_root: str = "workspace"
    tour_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    # Stage 2: Static analysis
    apktool_path: str = "apktool"

    # Stage 3: DroidBot walk
    # 2026-05-02 (F3): 1200 → 1800 (30분). 메가커피 e8951fef 분석 결과 timeout
    # 도달 (1203/1200s) 로 종료 — walking 더 길게 가면 부수 영역 (Naver maps,
    # Settings 깊이) 도달 가능. env var WALK_TIMEOUT 으로 override.
    droidbot_timeout: int = 1800  # seconds (30 min — large apps like Spotify, megacoffee)
    droidbot_policy: str = "dfs_greedy"
    coverage_target: float = 0.8  # 80%

    # Stage 4: Preprocessing
    xml_max_depth: int = 10
    screenshot_size: tuple[int, int] = (720, 1280)
    screenshot_quality: int = 85

    # Stage 5: LLM. Model names change with each Claude major release, so
    # they're overridable via .env (LLM_MODEL_SCREEN, LLM_MODEL_WIDGET).
    # Hardcoding yields 404 "not_found_error" when models are deprecated.
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )
    llm_model_screen: str = field(
        default_factory=lambda: os.environ.get("LLM_MODEL_SCREEN", "claude-sonnet-4-5")
    )
    llm_model_widget: str = field(
        default_factory=lambda: os.environ.get("LLM_MODEL_WIDGET", "claude-haiku-4-5")
    )
    llm_temperature: float = 0.1
    llm_max_retries: int = 3

    # Stage 6: ScreenMap
    screenmap_output_filename: str = "screen_map.json"

    # Web dashboard
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8000

    # --- Derived paths ---

    @property
    def tour_dir(self) -> Path:
        return Path(self.workspace_root) / self.tour_id

    @property
    def apk_dir(self) -> Path:
        return self.tour_dir / "apk"

    @property
    def static_dir(self) -> Path:
        return self.tour_dir / "static"

    @property
    def dynamic_dir(self) -> Path:
        return self.tour_dir / "dynamic"

    @property
    def analysis_dir(self) -> Path:
        return self.tour_dir / "analysis"

    @property
    def output_dir(self) -> Path:
        return self.tour_dir / "output"

    def ensure_dirs(self) -> None:
        """Create all workspace sub-directories."""
        for d in [
            self.apk_dir,
            self.static_dir,
            self.dynamic_dir,
            self.analysis_dir,
            self.output_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)
