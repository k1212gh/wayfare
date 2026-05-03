"""Vision-LLM Clicker — XML extractor 가 분석 못 하는 화면의 fallback.

원칙 (screenatlas_completeness_principle + phash_coalesce_caveats 메모리):
  - score 가중치 누적의 본질적 한계 — Cycle 0~3 evidence 로 확정.
  - score 만으로 outside view 누르는 stall (TimePicker OK 0회 등) 못 풂.
  - Vision LLM 이 화면 보고 actionable element 좌표 추정 → click 으로 우회.

비용 최적화:
  - Haiku 4.5 ($0.001/call vs Sonnet $0.003)
  - Image resize 1080×2400 → 540×1200 (token 75% 감소)
  - state_str cache — 같은 화면 재진입 시 vision 안 호출
  - Stall-only trigger — 한 잡당 5-10 호출 (~$0.01-0.05)

사용 (Phase 5 통합 전):
    clicker = VisionTapper(api_key, budget=10)
    actions = clicker.extract_actionable(screenshot_path, state_str)
    # actions: [{label, type, bounds, confidence, expected_outcome}, ...]
"""

from __future__ import annotations

import json
import logging
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 보수적 — 좌표 검증, confidence 임계
MIN_CONFIDENCE = 0.7
MAX_RESIZED_WIDTH = 540   # 원본 1080 의 1/2
MAX_RESIZED_HEIGHT = 1200  # 원본 2400 의 1/2


_SYSTEM_PROMPT = (
    "You analyze Android UI screenshots and extract actionable elements as JSON. "
    "An actionable element is one a user can tap, type, or swipe to make progress.\n\n"
    "Output strict JSON only:\n"
    '{\n'
    '  "actionable_widgets": [\n'
    '    {\n'
    '      "label": "<short ko/en label>",\n'
    '      "type": "<button|input|toggle|tab|link|picker|other>",\n'
    '      "bounds": [x1, y1, x2, y2],         // pixel coords on ORIGINAL image\n'
    '      "confidence": 0.0-1.0,\n'
    '      "expected_outcome": "<what tapping does, ko/en, ≤60 chars>"\n'
    '    }\n'
    '  ],\n'
    '  "stall_signal": "<what is currently shown, e.g. TimePicker, EmptyList, Loading>",\n'
    '  "recommended_next": "<which element to tap next + why>"\n'
    "}\n\n"
    "Important:\n"
    "- bounds must be in ORIGINAL image pixel space (the image you see is resized; "
    "scale up coordinates accordingly).\n"
    "- Only include elements you are >70% confident about.\n"
    "- Prefer elements that progress the apparent task (e.g. OK over Cancel)."
)


class VisionTapper:
    """Stall 시점에 vision LLM 으로 actionable element 좌표 추정.

    내부 cache 로 같은 state_str 의 결과 재사용 (잡당 호출 횟수 최소화).
    Budget cap 으로 한 잡당 호출 횟수 제한 — env: VISION_BUDGET (default 10).
    """

    def __init__(
        self,
        api_key: str,
        budget: int | None = None,
        model: str = "claude-haiku-4-5-20251001",
        timeout_s: float = 30.0,
        original_size: tuple[int, int] = (1080, 2400),
    ):
        if not api_key or "PLACEHOLDER" in api_key:
            raise ValueError("VisionTapper requires real ANTHROPIC_API_KEY")
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError("anthropic SDK required for VisionTapper") from e
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(
            api_key=api_key, timeout=timeout_s, max_retries=0,
        )
        self.model = model
        self.budget = budget if budget is not None else int(
            os.environ.get("VISION_BUDGET", "10"),
        )
        self.calls_used = 0
        self.cache: dict[str, list[dict]] = {}
        self.original_size = original_size

    def extract_actionable(
        self,
        screenshot_path: str | Path,
        state_str: str = "",
    ) -> list[dict]:
        """Screenshot 에서 actionable elements 추출. 빈 리스트 = 호출 실패/budget 초과/저신뢰."""
        if state_str and state_str in self.cache:
            logger.debug("[vision] cache hit for state %s", state_str[:12])
            return self.cache[state_str]

        if self.calls_used >= self.budget:
            logger.warning(
                "[vision] budget exceeded (%d/%d) — skipping",
                self.calls_used, self.budget,
            )
            return []

        sp = Path(screenshot_path)
        if not sp.exists():
            logger.warning("[vision] screenshot not found: %s", sp)
            return []

        try:
            img_bytes, original_dim, resized_dim = self._load_resize(sp)
        except Exception as e:
            logger.warning("[vision] image load/resize failed: %s", e)
            return []

        # 호출
        try:
            self.calls_used += 1
            resp_text = self._call_vision(img_bytes)
        except Exception as e:
            logger.warning("[vision] API call failed: %s", e)
            return []

        # 파싱 + 좌표 검증
        try:
            parsed = self._parse(resp_text, original_dim)
        except Exception as e:
            logger.warning("[vision] parse failed: %s — first 200 chars: %r",
                           e, resp_text[:200])
            return []

        actions = parsed.get("actionable_widgets", []) or []
        actions = [a for a in actions if self._validate(a, original_dim)]
        # confidence 정렬 — 호출자가 top 부터 시도
        actions.sort(key=lambda a: -a.get("confidence", 0))

        if state_str:
            self.cache[state_str] = actions
        logger.info(
            "[vision] %s — %d actions (budget %d/%d)",
            parsed.get("stall_signal", "?")[:30],
            len(actions), self.calls_used, self.budget,
        )
        return actions

    # ─── private ─────────────────────────────────────────

    def _load_resize(
        self,
        path: Path,
    ) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
        """원본 size 와 resized image bytes 반환.
        호출자가 좌표 unscale 시 original_dim 사용."""
        try:
            from PIL import Image
        except ImportError as e:
            raise RuntimeError("Pillow required for image resize") from e

        with Image.open(path) as img:
            original = img.size  # (w, h)
            # 비율 유지 thumbnail
            img2 = img.copy()
            img2.thumbnail((MAX_RESIZED_WIDTH, MAX_RESIZED_HEIGHT), Image.LANCZOS)
            resized = img2.size
            buf = BytesIO()
            # JPEG 으로 압축 — token 더 적음
            img2.convert("RGB").save(buf, format="JPEG", quality=85)
            return buf.getvalue(), original, resized

    def _call_vision(self, img_bytes: bytes) -> str:
        import base64
        b64 = base64.standard_b64encode(img_bytes).decode("ascii")
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            temperature=0.1,
            system=_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/jpeg",
                                "data": b64}},
                    {"type": "text",
                     "text": "Extract actionable elements from this screen."},
                ],
            }],
        )
        return msg.content[0].text

    def _parse(self, text: str, original_dim: tuple[int, int]) -> dict:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # regex fallback — 가장 outermost {...}
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            return json.loads(m.group(0))
        raise ValueError(f"no valid JSON ({len(text)} chars)")

    def _validate(self, action: dict, original_dim: tuple[int, int]) -> bool:
        """좌표가 원본 이미지 안 + confidence 임계 통과."""
        if action.get("confidence", 0) < MIN_CONFIDENCE:
            return False
        bounds = action.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4:
            return False
        x1, y1, x2, y2 = bounds
        if not all(isinstance(c, (int, float)) for c in bounds):
            return False
        w, h = original_dim
        # 범위 + 너비/높이 sanity
        if not (0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h):
            return False
        if (x2 - x1) < 10 or (y2 - y1) < 10:
            return False  # 너무 작음 — 의미 없는 후보
        return True

    @staticmethod
    def click_point(action: dict) -> tuple[int, int]:
        """action 의 bounds 중심 좌표 — uiautomator click 에 사용."""
        x1, y1, x2, y2 = action["bounds"]
        return ((x1 + x2) // 2, (y1 + y2) // 2)
