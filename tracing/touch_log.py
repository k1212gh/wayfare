"""Touch Log (TouchLog) — structured trace schema.

Based on AgentTrace 3-layer architecture:
  - operational: method calls, durations, return values
  - cognitive:   LLM reasoning, CoT, confidence, decisions
  - contextual:  external I/O (ADB, file system, API calls)

Each record: R_k = (Intent, Observation, Inference, Plan_version)
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class TraceLayer(str, Enum):
    OPERATIONAL = "operational"
    COGNITIVE = "cognitive"
    CONTEXTUAL = "contextual"


@dataclass
class TouchLog:
    """Single Touch Log."""

    # Identity
    trace_id: str = ""          # Pipeline run ID
    step_id: str = ""           # Unique step identifier
    stage: str = ""             # Pipeline stage (stage1..stage6)
    layer: str = "operational"  # TraceLayer value

    # TouchLog tuple: R_k = (I_k, O_k, N_k, P_k)
    intent: str = ""            # WHY this action was taken
    observation: str = ""       # WHAT was observed/returned
    inference: str = ""         # HOW this affects next steps
    plan_version: str = ""      # Which plan version triggered this

    # Metadata
    type: str = ""              # e.g. "llm_call", "adb_command", "file_write"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_ms: float = 0.0

    # Payload (layer-specific)
    input_data: Optional[dict[str, Any]] = None
    output_data: Optional[dict[str, Any]] = None

    # Metrics
    token_count: Optional[int] = None
    confidence: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dict, omitting None values for compact JSONL."""
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None and v != "" and v != 0.0}
