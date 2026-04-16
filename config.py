"""ScreenAtlas pipeline configuration."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import os
import uuid

# Load .env file if present
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


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
    droidbot_timeout: int = 600  # seconds
    droidbot_policy: str = "dfs_greedy"
    coverage_target: float = 0.8  # 80%

    # Stage 4: Preprocessing
    xml_max_depth: int = 10
    screenshot_size: tuple[int, int] = (720, 1280)
    screenshot_quality: int = 85

    # Stage 5: LLM
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )
    llm_model_screen: str = "claude-sonnet-4-20250514"
    llm_model_widget: str = "claude-haiku-4-5-20251001"
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
