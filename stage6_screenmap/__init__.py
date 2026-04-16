"""Stage 6: Screen Map generation — merge, validate, serialize."""

import json
import logging
from config import PipelineConfig
from .screenmap_builder import build_graph
from .screenmap_validator import validate_graph
from .screenmap_enricher import enrich_graph
from .screenmap_serializer import serialize_screenmap

logger = logging.getLogger(__name__)


def run_stage6(config: PipelineConfig) -> None:
    """Build the final screen_map.json."""
    # Load inputs
    sa_path = config.analysis_dir / "screen_analyses.json"
    sg_path = config.analysis_dir / "subflows.json"
    cu_path = config.analysis_dir / "screen_cards.json"
    meta_path = config.apk_dir / "metadata.json"
    static_path = config.static_dir / "analysis.json"

    screen_analyses = json.loads(sa_path.read_text(encoding="utf-8")) if sa_path.exists() else []
    subflows = json.loads(sg_path.read_text(encoding="utf-8")) if sg_path.exists() else []
    screen_cards = json.loads(cu_path.read_text(encoding="utf-8")) if cu_path.exists() else []
    metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    static_info = json.loads(static_path.read_text(encoding="utf-8")) if static_path.exists() else {}

    # 1. Build graph from subflows + screen analyses
    graph = build_graph(subflows, screen_analyses, screen_cards)

    # 2. Enrich with static analysis (restore missing edges)
    graph = enrich_graph(graph, static_info)

    # 3. Validate
    report = validate_graph(graph)
    logger.info("Validation: %s", json.dumps(report["summary"], indent=2))

    # 4. Serialize to schema
    screenmap = serialize_screenmap(graph, metadata, report)

    # Write output
    output_path = config.output_dir / config.screenmap_output_filename
    output_path.write_text(json.dumps(screenmap, indent=2, ensure_ascii=False, encoding="utf-8"))

    # Write validation report
    report_path = config.output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, encoding="utf-8"))

    logger.info(
        "ScreenMap generated: %d nodes, %d edges → %s",
        len(graph.get("nodes", [])),
        len(graph.get("edges", [])),
        output_path,
    )
