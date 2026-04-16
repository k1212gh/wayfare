"""Per-screen LLM analysis — screen purpose, functional category, key elements."""

import logging
from .llm_client import LLMClient
from .grounding_checker import check_grounding

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """당신은 안드로이드 앱 UI 분석 전문가입니다.
주어진 화면의 UI Hierarchy XML을 분석하여,
해당 화면의 기능적 의도와 사용 가능한 액션을 정확하게 파악합니다.

출력 규칙:
- 반드시 아래 JSON 형식으로만 응답하세요.
- 추측이 불확실한 경우 confidence를 "low"로 설정하세요.
- XML에 있는 요소만 참조하세요. 없는 widget_id를 만들지 마세요."""

USER_PROMPT_TEMPLATE = """## 앱 정보
- 패키지: {package_name}
- 현재 Activity: {activity_name}

## UI Hierarchy XML
{cleaned_xml}

## 분석 요청
아래 JSON 형식으로 응답하세요:
{{
    "screen_purpose": "이 화면의 주요 목적 (1~2문장)",
    "functional_category": "login | home | settings | content_detail | search | list | form | profile | navigation | other",
    "key_widgets": [
        {{
            "widget_id": "resource-id 또는 고유 식별자",
            "role": "이 요소의 기능적 역할",
            "action_type": "click | input | scroll | toggle",
            "expected_result": "이 요소를 조작하면 예상되는 결과"
        }}
    ],
    "user_flow_position": "이 화면이 사용자 여정에서 차지하는 위치",
    "confidence": "high | medium | low"
}}"""


def analyze_screens(
    client: LLMClient,
    screen_cards: list[dict],
    metadata: dict,
) -> list[dict]:
    """Analyze each screen with LLM and return enriched analyses.

    Uses cross-app pattern cache: cached elements skip LLM call.
    """
    package_name = metadata.get("package_name", "unknown")
    analyses = []
    analyzed_ids: set[str] = set()

    # Initialize pattern cache
    cache = None
    try:
        from cache import WidgetCache
        cache = WidgetCache()
        if cache.enabled:
            logger.info("Pattern cache enabled: %s", cache.stats())
    except Exception:
        pass

    for i, unit in enumerate(screen_cards):
        sid = unit.get("screen_id", "")
        if sid in analyzed_ids:
            logger.debug("Skipping already-analyzed screen: %s", sid)
            continue
        analyzed_ids.add(sid)
        logger.info(
            "Analyzing screen %d/%d: %s",
            i + 1, len(screen_cards), unit.get("activity_name", "?"),
        )

        user_prompt = USER_PROMPT_TEMPLATE.format(
            package_name=package_name,
            activity_name=unit.get("activity_name", ""),
            cleaned_xml=unit.get("cleaned_xml", "<hierarchy/>"),
        )

        try:
            result = client.query_json(SYSTEM_PROMPT, user_prompt)
        except Exception as e:
            logger.error("Failed to analyze screen %s: %s", unit["screen_id"], e)
            result = {
                "screen_purpose": "Analysis failed",
                "functional_category": "other",
                "key_widgets": [],
                "user_flow_position": "unknown",
                "confidence": "low",
            }

        # Grounding check
        valid_ids = {a["widget_id"] for a in unit.get("available_actions", [])}
        result = check_grounding(result, valid_ids)

        # Store element analyses in cache for cross-app reuse
        if cache and cache.enabled:
            for elem in result.get("key_widgets", []):
                try:
                    # Find matching action info
                    action_info = next(
                        (a for a in unit.get("available_actions", []) if a.get("widget_id") == elem.get("widget_id")),
                        {}
                    )
                    cache.store(
                        class_name=action_info.get("description", "").split(",")[0] if action_info else "",
                        content_desc=elem.get("widget_id", ""),
                        text="",
                        action_types=[elem.get("action_type", "click")],
                        role=elem.get("role", ""),
                        expected_result=elem.get("expected_result", ""),
                        confidence=result.get("confidence", "medium"),
                        package_name=package_name,
                    )
                except Exception:
                    pass

        analysis = {
            "screen_id": unit["screen_id"],
            "activity_name": unit.get("activity_name", ""),
            **result,
        }
        analyses.append(analysis)

    return analyses
