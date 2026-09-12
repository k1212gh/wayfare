"""TarpitEscaper — 막힌 화면(tarpit)에서 텍스트 위젯 목록만으로 탈출 액션을 고르는 LLM 보조.

근거: "Improving Random Testing via LLM-powered UI Tarpit Escaping for Mobile
Apps" (Xu et al., arXiv 2604.06763, 2026). 무작위 탐색이 같은 UI 영역에 갇힌
것을 감지했을 때만 LLM 을 호출해 탈출 이벤트를 제안 → Monkey +54.8%,
DroidBot +44.8% 커버리지.

기존 VisionTapper 와의 차이:
  - 스크린샷(이미지 토큰) 대신 정제된 위젯 목록(텍스트) 을 보낸다 → 훨씬 싸다.
  - 좌표 추정이 아니라 실제 view 인덱스를 고르므로 좌표 오차가 없다.
  - 반환된 view 는 기존 _execute_action 경로로 그대로 탭한다.

호출 조건은 호출자(TapWalker) 책임: stall 감지 + 현재 화면 미시도 소진 +
Frontier 경로 없음. 활성화: TARPIT_LLM_ESCAPE=1 + ANTHROPIC_API_KEY.
예산: TARPIT_BUDGET (기본 20 회/잡). canonical 별 캐시로 재호출 방지.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 60
MAX_PICKS = 3

_SYSTEM_PROMPT = (
    "You help an automated Android app explorer that is STUCK on one screen. "
    "You receive the current activity, a numbered list of visible UI widgets, "
    "the actions already tried on this screen, and the most recent actions. "
    "Pick up to 3 widgets that most likely lead to a DIFFERENT, not-yet-seen screen "
    "of the same app (navigation entries, tabs, menu items, list rows, next/confirm "
    "buttons). Avoid: already-tried widgets, external links, logout, payment, "
    "destructive actions, and anything that leaves the app.\n\n"
    "Respond with strict JSON only:\n"
    '{"screen_kind": "<short description>", '
    '"picks": [{"index": <int>, "reason": "<up to 60 chars>"}]}'
)


def _short(s: Any, n: int = 40) -> str:
    s = str(s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "~"


class TarpitEscaper:
    """정제된 위젯 목록을 LLM 에 보내 탈출 후보 view 를 고른다."""

    def __init__(
        self,
        api_key: str = "",
        budget: int | None = None,
        model: str = "claude-haiku-4-5-20251001",
        timeout_s: float = 30.0,
        client: Any = None,
    ):
        self.model = model
        self.budget = budget if budget is not None else int(os.environ.get("TARPIT_BUDGET", "20"))
        self.calls_used = 0
        self.cache: dict[str, list[dict]] = {}
        self.llm = None
        if client is not None:
            self.client = client   # 테스트 주입
        else:
            from stage5_annotate.llm_client import OPENAI_COMPAT_MODES, llm_mode
            if llm_mode() in OPENAI_COMPAT_MODES:
                # 로컬 OpenAI 호환 공급자 — 텍스트 전용이라 7B 급으로 충분
                from stage5_annotate.llm_client import create_client
                self.llm = create_client(max_retries=1, timeout_s=max(timeout_s, 120.0))
                self.client = None
                return
            if not api_key or "PLACEHOLDER" in api_key:
                raise ValueError("TarpitEscaper requires real ANTHROPIC_API_KEY (or LLM_MODE=openai)")
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("anthropic SDK required for TarpitEscaper") from e
            self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=0)

    # ── 후보 구성 ───────────────────────────────────────────────

    @staticmethod
    def candidates(views: list[dict]) -> list[tuple[int, dict]]:
        """LLM 에 보여줄 후보: 라벨/desc/rid 중 하나라도 있고 visible 인 view.

        원본 views 의 인덱스를 함께 반환해 응답의 index 를 그대로 역참조한다.
        """
        out: list[tuple[int, dict]] = []
        for i, v in enumerate(views):
            if v.get("visible") is False:
                continue
            if not (v.get("text") or v.get("content_desc") or v.get("resource_id")):
                continue
            out.append((i, v))
            if len(out) >= MAX_CANDIDATES:
                break
        return out

    @staticmethod
    def _format_candidates(cands: list[tuple[int, dict]]) -> str:
        lines = []
        for i, v in cands:
            flags = []
            if v.get("clickable"):
                flags.append("clickable")
            if v.get("scrollable"):
                flags.append("scrollable")
            if v.get("editable"):
                flags.append("editable")
            cls = str(v.get("class", "") or "").rsplit(".", 1)[-1]
            rid = str(v.get("resource_id", "") or "").rsplit("/", 1)[-1]
            lines.append(
                "[{}] {} text={!r} desc={!r} id={!r} {}".format(
                    i, cls, _short(v.get("text")), _short(v.get("content_desc")),
                    _short(rid, 30), " ".join(flags),
                )
            )
        return "\n".join(lines)

    def build_prompt(
        self,
        views: list[dict],
        activity: str,
        tried_descs: list[str] | set[str],
        recent_descs: list[str],
    ) -> tuple[str, list[tuple[int, dict]]]:
        cands = self.candidates(views)
        tried = sorted(_short(t, 50) for t in tried_descs)[:40]
        recent = [_short(r, 50) for r in recent_descs[-6:]]
        prompt = (
            "Activity: {}\n\n"
            "Widgets:\n{}\n\n"
            "Already tried on this screen ({}): {}\n"
            "Recent actions: {}\n\n"
            "Which widgets should be tapped to escape to a new screen?"
        ).format(activity or "unknown", self._format_candidates(cands), len(tried), tried, recent)
        return prompt, cands

    # ── 호출 ────────────────────────────────────────────────────

    def suggest(
        self,
        views: list[dict],
        activity: str,
        tried_descs: list[str] | set[str],
        recent_descs: list[str],
        canonical_id: str = "",
    ) -> list[dict]:
        """탈출 후보를 우선순위 순으로 반환: [{"index", "view", "reason"}, ...].

        빈 리스트 = 예산 초과 / 후보 없음 / 호출 실패 / 파싱 실패.
        """
        prompt, cands = self.build_prompt(views, activity, tried_descs, recent_descs)
        if not cands:
            return []
        by_index = {i: v for i, v in cands}

        if canonical_id and canonical_id in self.cache:
            picks = self.cache[canonical_id]
            logger.debug("[tarpit] cache hit for %s", canonical_id[:12])
        else:
            if self.calls_used >= self.budget:
                logger.warning("[tarpit] budget exceeded (%d/%d) — skipping", self.calls_used, self.budget)
                return []
            self.calls_used += 1
            try:
                if self.llm is not None:
                    text = self.llm.query_text(_SYSTEM_PROMPT, prompt, max_tokens=400)
                else:
                    msg = self.client.messages.create(
                        model=self.model,
                        max_tokens=400,
                        system=_SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    text = "".join(getattr(b, "text", "") for b in getattr(msg, "content", []) or [])
            except Exception as e:  # 네트워크/인증/레이트리밋 — 탐색을 멈추지 않는다
                logger.warning("[tarpit] LLM call failed: %s", e)
                return []
            picks = self.parse_picks(text)
            if canonical_id:
                self.cache[canonical_id] = picks

        out = []
        for entry in picks:
            idx = entry.get("index")
            if idx in by_index:
                out.append({"index": idx, "view": by_index[idx], "reason": entry.get("reason", "")})
        return out[:MAX_PICKS]

    @staticmethod
    def parse_picks(text: str) -> list[dict]:
        """모델 응답에서 picks 추출. 코드펜스/앞뒤 잡음 허용."""
        if not text:
            return []
        cleaned = re.sub(r"```(?:json)?", "", text).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            m = re.search(r"\{[\s\S]*\}", cleaned)
            if not m:
                return []
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return []
        picks = data.get("picks") if isinstance(data, dict) else None
        if not isinstance(picks, list):
            return []
        out = []
        for p in picks:
            if isinstance(p, dict) and isinstance(p.get("index"), int):
                out.append({"index": p["index"], "reason": str(p.get("reason", ""))[:80]})
            elif isinstance(p, int):
                out.append({"index": p, "reason": ""})
        return out[:MAX_PICKS]
