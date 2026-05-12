"""Vision LLM (Claude Sonnet) 로 쌍 라벨링 (옵션 A).

`visual_merge_llm.py` 의 batched call 로직을 재사용. ground truth 라벨링
용도로 wrap. 100쌍 / 한 잡 단일 API call (~$0.05).

CLI:
  python -m experiments.coalesce_ablation.label_with_llm

입력: data/pairs_to_label.jsonl (의 needs_review 부분만 또는 전체)
출력: data/labels_llm_A.jsonl
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


DATA = Path(__file__).resolve().parent / "data"
INPUT = DATA / "pairs_needs_review_unique.jsonl"
OUT = DATA / "labels_llm_A.jsonl"


def load_pairs() -> list[dict]:
    pairs = []
    for line in INPUT.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pairs.append(json.loads(line))
    return pairs


def label_with_claude(pairs: list[dict]) -> list[dict]:
    """Claude Sonnet vision call 로 쌍 라벨링.

    visual_merge_llm.py 의 batched 로직을 mirror. 단, ground truth 라벨링은
    Claude 답이 SAME / DIFFERENT 둘 중 하나로 결정성 있도록 strict prompt.
    """
    try:
        import anthropic
    except ImportError:
        print("anthropic not installed. pip install anthropic", file=sys.stderr)
        sys.exit(2)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or "PLACEHOLDER" in api_key:
        print("ANTHROPIC_API_KEY missing or placeholder", file=sys.stderr)
        sys.exit(2)

    # visual_merge_llm 의 helper 재사용
    from stage6_screenmap.visual_merge_llm import _encode_image_for_llm

    client = anthropic.Anthropic(api_key=api_key)
    out: list[dict] = []

    # batch size 5 쌍 — Claude vision input 제약 안 넘기게
    BATCH = 5
    for bstart in range(0, len(pairs), BATCH):
        batch = pairs[bstart:bstart + BATCH]
        content = [{
            "type": "text",
            "text": (
                "Each pair below is two Android screen captures. For each pair, "
                "decide if they represent the SAME logical screen (user sees them "
                "as same) or DIFFERENT screens.\n\n"
                "Rules:\n"
                "  - SAME: ticking clock / search input data / scroll position / "
                "  list item data difference\n"
                "  - DIFFERENT: different tab, different settings page, "
                "  different category (e.g. Coffee menu vs Decaf menu)\n\n"
                "Output JSON array with each entry: "
                '{"pair_index": N, "same_screen": true|false, "reason": "..."}'
            ),
        }]

        for idx, pair in enumerate(batch):
            global_idx = bstart + idx
            for label, path in (("A", pair["screenshot_a"]), ("B", pair["screenshot_b"])):
                p = Path(path)
                if not p.exists():
                    continue
                try:
                    img_bytes, mt = _encode_image_for_llm(str(p))
                except Exception as e:
                    print(f"encode fail {p.name}: {e}", file=sys.stderr)
                    continue
                import base64
                content.append({
                    "type": "text",
                    "text": f"--- Pair {global_idx} side {label} ---",
                })
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mt,
                        "data": base64.standard_b64encode(img_bytes).decode(),
                    },
                })

        try:
            msg = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=2000,
                temperature=0,
                messages=[{"role": "user", "content": content}],
            )
            response_text = msg.content[0].text if msg.content else ""
        except Exception as e:
            print(f"API call failed for batch {bstart}: {e}", file=sys.stderr)
            continue

        # JSON 파싱
        import re
        m = re.search(r"\[.*\]", response_text, re.DOTALL)
        if not m:
            print(f"no JSON in response: {response_text[:200]}", file=sys.stderr)
            continue
        try:
            verdicts = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            print(f"JSON parse fail: {e}", file=sys.stderr)
            continue

        for v in verdicts:
            pi = v.get("pair_index")
            if pi is None or pi - bstart < 0 or pi - bstart >= len(batch):
                continue
            rec = dict(batch[pi - bstart])
            rec["same_screen"] = bool(v.get("same_screen", False))
            rec["rationale"] = v.get("reason", "")
            rec["label_source"] = "llm_A"
            out.append(rec)

        print(f"batch {bstart}~{bstart + len(batch) - 1}: {len(verdicts)} labeled",
              file=sys.stderr)

    return out


def main():
    pairs = load_pairs()
    print(f"labeling {len(pairs)} pairs via Claude Vision", file=sys.stderr)
    labeled = label_with_claude(pairs)
    OUT.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in labeled),
        encoding="utf-8",
    )
    print(f"\nsaved {len(labeled)} labels → {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
