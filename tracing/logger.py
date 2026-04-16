"""JSONL trace logger — writes structured TouchLog records to file."""

import json
import logging
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from .touch_log import TouchLog, TraceLayer

logger = logging.getLogger(__name__)

_active_logger: "TraceLogger | None" = None


def get_trace_logger() -> "TraceLogger | None":
    return _active_logger


class TraceLogger:
    """Appends TouchLog records as JSONL to a trace file."""

    def __init__(self, trace_file: Path, trace_id: str = ""):
        self.trace_file = trace_file
        self.trace_id = trace_id or uuid.uuid4().hex[:8]
        self.trace_file.parent.mkdir(parents=True, exist_ok=True)
        self._records: list[dict] = []

        global _active_logger
        _active_logger = self

    def log(self, record: TouchLog) -> None:
        """Write a single TouchLog record."""
        record.trace_id = self.trace_id
        if not record.step_id:
            record.step_id = uuid.uuid4().hex[:8]
        d = record.to_dict()
        self._records.append(d)
        self._append_jsonl(d)

    def log_operational(self, stage: str, type: str, intent: str, **kwargs) -> None:
        """Quick log for operational layer."""
        self.log(TouchLog(
            stage=stage, layer=TraceLayer.OPERATIONAL, type=type,
            intent=intent, **kwargs,
        ))

    def log_cognitive(self, stage: str, type: str, intent: str, **kwargs) -> None:
        """Quick log for cognitive layer (LLM reasoning)."""
        self.log(TouchLog(
            stage=stage, layer=TraceLayer.COGNITIVE, type=type,
            intent=intent, **kwargs,
        ))

    def log_contextual(self, stage: str, type: str, intent: str, **kwargs) -> None:
        """Quick log for contextual layer (external I/O)."""
        self.log(TouchLog(
            stage=stage, layer=TraceLayer.CONTEXTUAL, type=type,
            intent=intent, **kwargs,
        ))

    @contextmanager
    def span(self, stage: str, type: str, intent: str, layer: str = "operational") -> Generator[dict, None, None]:
        """Context manager that auto-measures duration and logs on exit.

        Usage:
            with tracer.span("stage2", "manifest_parse", "Extract activities") as s:
                result = parse_manifest(path)
                s["observation"] = f"Found {len(result)} activities"
                s["output_data"] = {"count": len(result)}
        """
        ctx: dict[str, Any] = {"_start": time.time()}
        try:
            yield ctx
        except Exception as e:
            ctx["error"] = str(e)[:200]
            raise
        finally:
            duration = (time.time() - ctx.pop("_start", 0)) * 1000
            self.log(TouchLog(
                stage=stage,
                layer=layer,
                type=type,
                intent=intent,
                observation=ctx.get("observation", ""),
                inference=ctx.get("inference", ""),
                duration_ms=round(duration, 1),
                output_data=ctx.get("output_data"),
                input_data=ctx.get("input_data"),
                token_count=ctx.get("token_count"),
                confidence=ctx.get("confidence"),
                error=ctx.get("error"),
            ))

    def get_records(self) -> list[dict]:
        """Get all records (for API serving)."""
        if self._records:
            return self._records
        # Read from file
        if self.trace_file.exists():
            records = []
            for line in self.trace_file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            return records
        return []

    def _append_jsonl(self, data: dict) -> None:
        with open(self.trace_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
