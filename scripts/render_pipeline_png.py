"""파이프라인 SVG → PNG 변환. Notion 업로드용.

Usage:
    python scripts/render_pipeline_png.py

Output: reports/pipeline_flow.png (2x 해상도)
"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gen_master_report import svg_pipeline_flow  # noqa: E402
from svglib.svglib import svg2rlg  # noqa: E402
from reportlab.graphics import renderPM  # noqa: E402


def main() -> None:
    svg = svg_pipeline_flow()
    drawing = svg2rlg(BytesIO(svg.encode("utf-8")))
    # 2x 해상도 — Notion에서 확대해도 깨끗하게 보이도록
    drawing.scale(2, 2)
    drawing.width *= 2
    drawing.height *= 2

    out_path = ROOT / "reports" / "pipeline_flow.png"
    out_path.parent.mkdir(exist_ok=True)
    renderPM.drawToFile(drawing, str(out_path), fmt="PNG")
    print(f"Wrote {out_path} ({out_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
