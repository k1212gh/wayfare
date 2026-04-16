"""Derive functional subflows by grouping related screens."""

import logging
from collections import defaultdict
from .llm_client import LLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """당신은 안드로이드 앱 화면 흐름 분석 전문가입니다.
주어진 연관된 화면들을 종합하여 기능 그룹의 화면 흐름을 그래프 구조로 정리합니다.

출력 규칙:
- 반드시 JSON 형식으로만 응답하세요.
- 노드는 화면, 엣지는 화면 전환입니다."""

GROUP_PROMPT_TEMPLATE = """## 앱 패키지: {package_name}

## 화면 그룹
{screens_description}

## 전환 관계
{transitions_description}

## 분석 요청
아래 JSON 형식으로 이 기능 그룹의 서브그래프를 정리하세요:
{{
    "subflow_name": "기능 그룹 이름",
    "description": "이 그룹이 담당하는 기능 설명",
    "nodes": [
        {{
            "screen_id": "페이지 ID",
            "label": "화면 이름 (한글)",
            "functional_role": "이 화면의 역할",
            "screen_params": {{
                "inputs": ["이전 화면에서 받는 데이터"],
                "outputs": ["다음 화면에 전달하는 데이터"],
                "displays": ["화면에 표시되는 정보"]
            }}
        }}
    ],
    "edges": [
        {{
            "from": "source screen_id",
            "to": "target screen_id",
            "trigger_action": "click widget_id",
            "condition": "전환 조건 (없으면 null)",
            "passed_params": ["전달 파라미터"]
        }}
    ]
}}"""


def derive_subflows(
    client: LLMClient,
    screen_analyses: list[dict],
    screen_cards: list[dict],
    metadata: dict,
) -> list[dict]:
    """Group related screens and derive functional subflows.

    Groups screens by:
    1. Common Activity prefix (same feature module)
    2. Navigation connectivity (reachable from each other)
    """
    package_name = metadata.get("package_name", "unknown")

    # Build lookup
    analysis_map = {a["screen_id"]: a for a in screen_analyses}
    unit_map = {u["screen_id"]: u for u in screen_cards}

    # Group by Activity prefix (first two parts after package)
    groups = _group_by_activity(screen_analyses)

    subflows = []
    for group_name, screen_ids in groups.items():
        if len(screen_ids) < 1:
            continue

        # Build description for LLM
        screens_desc = _build_screens_description(screen_ids, analysis_map)
        transitions_desc = _build_transitions_description(screen_ids, unit_map)

        prompt = GROUP_PROMPT_TEMPLATE.format(
            package_name=package_name,
            screens_description=screens_desc,
            transitions_description=transitions_desc,
        )

        try:
            result = client.query_json(SYSTEM_PROMPT, prompt)
            subflows.append(result)
        except Exception as e:
            logger.error("Failed to derive subflow for %s: %s", group_name, e)
            # Fallback: create basic subflow without LLM
            subflows.append(_fallback_subflow(group_name, screen_ids, analysis_map))

    logger.info("Derived %d subflows", len(subflows))
    return subflows


def _group_by_activity(analyses: list[dict]) -> dict[str, list[str]]:
    """Group screens by Activity name prefix."""
    groups: dict[str, list[str]] = defaultdict(list)
    for a in analyses:
        activity = a.get("activity_name", "")
        # Use functional_category as group key
        category = a.get("functional_category", "other")
        groups[category].append(a["screen_id"])
    return dict(groups)


def _build_screens_description(screen_ids: list[str], analysis_map: dict) -> str:
    lines = []
    for sid in screen_ids[:10]:  # Limit to avoid token overflow
        a = analysis_map.get(sid, {})
        lines.append(
            f"- {sid} ({a.get('activity_name', '?')}): "
            f"{a.get('screen_purpose', 'N/A')} "
            f"[category: {a.get('functional_category', '?')}]"
        )
    return "\n".join(lines)


def _build_transitions_description(screen_ids: list[str], unit_map: dict) -> str:
    lines = []
    id_set = set(screen_ids)
    for sid in screen_ids:
        unit = unit_map.get(sid, {})
        nav = unit.get("navigation_context", {})
        for target in nav.get("reachable_screens", []):
            if target in id_set:
                lines.append(f"- {sid} → {target}")
    return "\n".join(lines) if lines else "(no internal transitions)"


def _fallback_subflow(
    group_name: str,
    screen_ids: list[str],
    analysis_map: dict,
) -> dict:
    """Create a basic subflow without LLM when analysis fails."""
    nodes = []
    for sid in screen_ids:
        a = analysis_map.get(sid, {})
        nodes.append({
            "screen_id": sid,
            "label": a.get("screen_purpose", sid)[:50],
            "functional_role": a.get("screen_purpose", ""),
            "screen_params": {"inputs": [], "outputs": [], "displays": []},
        })
    return {
        "subflow_name": group_name,
        "description": f"Auto-grouped screens ({group_name})",
        "nodes": nodes,
        "edges": [],
    }
