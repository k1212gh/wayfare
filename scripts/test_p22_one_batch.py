"""P2.2 빠른 테스트 — 한 batch 만 LLM 호출해서 응답 형식 확인."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import config as _  # load .env

from config import PipelineConfig  # noqa: E402
from stage5_annotate.screenmap_annotator import (  # noqa: E402
    _system_prompt, _build_kg_context, _build_batch_prompt,
)
from stage5_annotate.llm_client import create_client  # noqa: E402


def main(tour_id: str):
    cfg = PipelineConfig(tour_id=tour_id)
    screenmap_path = cfg.output_dir / cfg.screenmap_output_filename
    screenmap = json.loads(screenmap_path.read_text(encoding="utf-8"))
    graph = screenmap["screen_map"]["graph"]
    nodes = graph["nodes"]
    edges = graph["edges"]

    # 첫번째 primitives 가 있는 노드 1개만
    target = next((n for n in nodes if n.get("primitives") and any(n["primitives"].values())), None)
    if not target:
        print("[err] no node has primitives")
        return 1

    print(f"[target] {target.get('screen_id','?')[:14]} label={target.get('label','')[:40]}")
    print(f"[primitives]")
    for k, v in target.get("primitives", {}).items():
        if v:
            print(f"  {k}: {len(v)} items, e.g. {v[0].get('id')}({v[0].get('label','')})")

    ctx = _build_kg_context(screenmap, graph)
    prompt = _build_batch_prompt(ctx, [target], all_nodes=nodes, all_edges=edges)

    print(f"\n[prompt size] {len(prompt)} chars")
    print(f"[prompt tail 500 chars]\n{prompt[-500:]}")

    client = create_client(
        api_key=cfg.anthropic_api_key,
        model_screen=cfg.llm_model_screen,
        model_widget=cfg.llm_model_widget,
        temperature=cfg.llm_temperature,
        max_retries=cfg.llm_max_retries,
    )
    sys_prompt = _system_prompt()
    print("\n[sys prompt last 600 chars]")
    print(sys_prompt[-600:])

    print("\n[calling LLM...]")
    resp = client.query_json(sys_prompt, prompt, max_tokens=2048)
    print(f"\n[response] keys={list(resp.keys()) if isinstance(resp, dict) else type(resp)}")
    print(json.dumps(resp, indent=2, ensure_ascii=False)[:3000])

    return 0


if __name__ == "__main__":
    tour = sys.argv[1] if len(sys.argv) > 1 else "b7b152fd"
    sys.exit(main(tour))
