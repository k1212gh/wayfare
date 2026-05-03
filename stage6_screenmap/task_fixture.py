"""Phase B (2026-04-29) — task fixture loader + ScreenMap feasibility matcher.

원칙 (screenatlas_completeness_principle 메모리):
  completeness 는 task scope 안에서만 정의 — "이 ScreenMap 가 task N개의 step path
  를 만들 수 있는 노드를 갖고 있나" 가 binary, decidable. "모든 화면 발견" 은
  unbounded 라 평가 불가.

YAML 형식 (tests/fixtures/{app}.yaml):
    app: <name>
    tasks:
      - id: <id>
        goal: <설명>
        expected_screens:
          - description: <설명>
            match_any:
              activity_substr: [<substr>, ...]      # any-of 부분일치
              functional_category: [<cat>, ...]     # any-of 정확일치
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def load_fixture(app: str | None) -> dict | None:
    """tests/fixtures/{app}.yaml 로드. 없거나 PyYAML 없으면 None."""
    if not app:
        return None
    p = FIXTURE_DIR / f"{app}.yaml"
    if not p.exists():
        return None
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed — task fixture skipped")
        return None
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("fixture load failed for %s: %s", app, e)
        return None


def _node_matches(node: dict, match: dict) -> bool:
    """match_any 룰 — 시그널 하나라도 맞으면 True (OR 매칭)."""
    if not match:
        return False
    activity = (node.get("activity") or "").lower()
    category = (node.get("functional_category") or "").lower()

    for substr in match.get("activity_substr", []) or []:
        if substr and substr.lower() in activity:
            return True

    cats = [c.lower() for c in (match.get("functional_category", []) or [])]
    if category and category in cats:
        return True

    return False


def evaluate_task_coverage(
    nodes: list[dict],
    fixture: dict,
) -> dict:
    """각 task 의 expected_screens 가 ScreenMap 노드와 모두 매칭되는지 평가.

    Returns 형식:
      {
        "total": N, "feasible": K, "ratio": K/N,
        "tasks": [
          {"id": ..., "goal": ..., "feasible": bool,
           "missing": [<screen description>, ...]},
          ...
        ],
      }
    """
    tasks = (fixture or {}).get("tasks", []) or []
    if not tasks:
        return {"total": 0, "feasible": 0, "ratio": None, "tasks": []}

    results = []
    feasible_count = 0
    for task in tasks:
        expected = task.get("expected_screens", []) or []
        missing: list[str] = []
        for screen in expected:
            match_rule = screen.get("match_any", {}) or {}
            if not any(_node_matches(n, match_rule) for n in nodes):
                missing.append(screen.get("description", "?"))
        feasible = (not missing) and bool(expected)
        if feasible:
            feasible_count += 1
        results.append({
            "id": task.get("id"),
            "goal": task.get("goal"),
            "feasible": feasible,
            "missing": missing,
        })

    return {
        "total": len(tasks),
        "feasible": feasible_count,
        "ratio": round(feasible_count / len(tasks), 3),
        "tasks": results,
    }
