"""ScreenMap 변경 후 ``screen_map.metadata`` 를 실제 nodes/edges 기준으로 재계산.

호출 시점 (Phase 1 P0.1):
  - ``semantic_merge.semantic_merge()`` 끝 (coalesce 으로 nodes/edges 줄어들 때)
  - ``stage5_annotate.run_stage5()`` 후 (status 가 enriched 로 바뀌어 actionable/plannable 변동)
  - ``pipeline_service`` 의 Stage 5.5 coalesce_file 호출 직후
  - ``coalesce_file()`` 자체에서도 (CLI 단독 사용 시 안전장치)

이 모듈은 ``screenmap_validator.validate_graph()`` 를 재실행하여 summary 를 만들고
그 결과 + 새 quality 필드 (actionable / plannable / activity_coverage / issue_severity)
를 ``screen_map.metadata`` 에 in-place 로 반영. 기존 schema 와 호환 — 추가 필드만.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# 고품질 노드의 의미적 정의 — 다른 모듈도 일치되도록 한 곳에서 정의.
# - actionable: 실제로 user agent 가 탭/스와이프 할 수 있는 view 가 있는 노드
# - plannable: actionable + 라벨링 완료 (LLM 이 의도 추론 가능)
def _is_actionable(node: dict) -> bool:
    if node.get("widgets"):
        return True
    if node.get("primary_affordances"):
        return True
    # chip_groups 가 있는 화면도 actionable (radio/stepper 등)
    if node.get("chip_groups"):
        return True
    # Phase 2: primitives 가 있으면 actionable (universal primitive 9종)
    prims = node.get("primitives") or {}
    interactive_keys = ("inputs", "toggles", "selectors", "steppers",
                        "sliders", "submits", "media")
    if any(prims.get(k) for k in interactive_keys):
        return True
    return False


def _is_plannable(node: dict) -> bool:
    """LLM 이 task path 의 step 으로 사용할 만한 노드."""
    if not _is_actionable(node):
        return False
    status = node.get("status", "")
    if status not in ("enriched", "probed"):
        return False
    # 의미 라벨이 없으면 plan step 으로 부적격
    if not node.get("label") and not node.get("screen_purpose"):
        return False
    return True


_OVERLAY_CLASS_PATTERNS = (
    "Dialog", "AlertDialog", "BottomSheet", "BottomSheetDialog",
    "PopupWindow", "Snackbar", "Toast",
)


def _is_overlay_node(node: dict) -> str | None:
    """Heuristic — 해당 노드가 overlay 인지, 어떤 종류인지.

    반환값: 'dialog' / 'bottom_sheet' / 'snackbar' / 'popup' / 'toast' / None
    """
    label = (node.get("label") or "").lower()
    activity = (node.get("activity") or "").lower()
    cls = node.get("root_class") or node.get("activity") or ""
    haystack = " ".join([label, activity, cls]).lower()
    if "bottomsheet" in haystack or "bottom_sheet" in haystack:
        return "bottom_sheet"
    if "snackbar" in haystack:
        return "snackbar"
    if "toast" in haystack:
        return "toast"
    if "popup" in haystack:
        return "popup"
    if "dialog" in haystack:
        return "dialog"
    return None


def _build_completeness(
    nodes: list[dict],
    static_info: dict | None,
    app_name: str | None = None,
) -> dict:
    """Phase A 1번 (2026-04-29) — completeness metric 분리.
    Phase B (2026-04-29) — task_coverage 채움 (app_name 있으면).

    이전: 단일 ``activity_coverage`` 가 launched/captured/enriched 를 다 섞어
    "발견율" 한 단어로 쓰던 결과 → 정의 충돌.

    이제: 3 개 분리.
      - manifest_reachability: declared activity 중 launched / captured / enriched
        세 단계 별도 분자. 분모는 manifest 의 declared 수.
      - overlay_count: dialog / bottom_sheet / snackbar / popup / toast 카운트.
        ground truth 가 없어 분모 없음 — lower bound.
      - task_coverage: tests/fixtures/{app}.yaml 의 task 가 ScreenMap 에서 풀리는지.
        app_name 없거나 fixture 없으면 None.

    원칙 (screenatlas_completeness_principle 메모리 참조):
    "모든 화면 발견" 은 unbounded 라 불가능 → task scope 안에서만 정의.
    """
    declared: set[str] = set()
    if static_info:
        declared = {
            a.get("name") for a in static_info.get("activities", []) if a.get("name")
        }

    launched = {
        n.get("activity") for n in nodes
        if n.get("status") in ("probed", "enriched", "resolved")
        and n.get("activity")
    }
    captured = {
        n.get("activity") for n in nodes
        if n.get("widgets") and n.get("activity")
    }
    enriched = {
        n.get("activity") for n in nodes
        if n.get("status") == "enriched" and n.get("activity")
    }

    overlay = {"dialog": 0, "bottom_sheet": 0, "snackbar": 0, "popup": 0, "toast": 0}
    for n in nodes:
        kind = _is_overlay_node(n)
        if kind:
            overlay[kind] += 1

    n_declared = len(declared) or None  # None → 분모 없음 (RN/Compose 등)
    return {
        "manifest_reachability": {
            "declared": n_declared,
            "launched": len(launched & declared) if declared else None,
            "captured": len(captured & declared) if declared else None,
            "enriched": len(enriched & declared) if declared else None,
            # ratio 는 declared 가 있을 때만 계산. 없으면 null.
            "launched_ratio": (
                round(len(launched & declared) / len(declared), 3)
                if declared else None
            ),
            "captured_ratio": (
                round(len(captured & declared) / len(declared), 3)
                if declared else None
            ),
            "enriched_ratio": (
                round(len(enriched & declared) / len(declared), 3)
                if declared else None
            ),
        },
        "overlay_count": {
            **overlay,
            "total": sum(overlay.values()),
        },
        "task_coverage": _evaluate_task_coverage(nodes, app_name),
    }


def _evaluate_task_coverage(
    nodes: list[dict],
    app_name: str | None,
) -> dict | None:
    """Phase B — fixture 로드 + 평가. 없으면 None (Phase A placeholder 와 동일)."""
    if not app_name:
        return None
    try:
        from .task_fixture import load_fixture, evaluate_task_coverage
    except Exception as e:
        logger.warning("task_fixture import failed: %s", e)
        return None
    fixture = load_fixture(app_name)
    if not fixture:
        return None
    return evaluate_task_coverage(nodes, fixture)


def _infer_app_name(md: dict, static_info: dict | None) -> str | None:
    """metadata.app 우선, 없으면 static_info.package 의 마지막 segment.
    com.android.deskclock → 'deskclock' (fixture 파일 이름과 매칭)."""
    if md.get("app"):
        return str(md["app"]).lower()
    if static_info:
        pkg = static_info.get("package_name") or static_info.get("package") or ""
        if pkg:
            return pkg.split(".")[-1].lower()
    return None


def refresh_metadata(
    screenmap: dict,
    static_info: dict | None = None,
    app_name: str | None = None,
) -> dict:
    """ScreenMap metadata 를 실제 nodes/edges 기준으로 재계산해서 in-place 반영.

    Args:
        screenmap: screen_map wrapper dict 또는 graph dict 직접. 양쪽 다 처리.
        static_info: stage 2 의 static analysis (manifest_reachability 계산용).
            없으면 declared 분모 null.
        app_name: task fixture 매칭용 slug (예: 'deskclock'). 없으면
            metadata.app / static_info.package 에서 추론.

    Returns:
        업데이트된 metadata dict (참조용 — 실제로는 screenmap in-place).
    """
    # screenmap 가 wrapper 인지, graph 직접인지 판별
    if "screen_map" in screenmap:
        afg = screenmap["screen_map"]
        graph = afg.get("graph", {})
        meta_host = afg
    else:
        graph = screenmap.get("graph") or screenmap
        meta_host = screenmap

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # 1. validator 재실행 — unreachable / dead_end / issue_count 최신화
    from .screenmap_validator import validate_graph
    report = validate_graph(graph)
    summary = report.get("summary", {}) or {}
    issues = report.get("issues", []) or []

    md = meta_host.setdefault("metadata", {})

    # 2. 기존 schema 필드 (additive 아님 — 덮어쓰기로 stale 정정)
    md["total_nodes"] = len(nodes)
    md["total_edges"] = len(edges)
    md["orphan_nodes"] = summary.get("unreachable_count", 0)
    md["dead_end_nodes"] = summary.get("dead_end_count", 0)
    md["validation_issues"] = summary.get("issue_count", 0)
    if "coverage_ratio" not in md:
        md["coverage_ratio"] = 0.0

    # 3. 새 quality 필드 (P1.1) — 기존 schema 에 additive
    md["actionable_nodes"] = sum(1 for n in nodes if _is_actionable(n))
    md["plannable_nodes"] = sum(1 for n in nodes if _is_plannable(n))
    md["reachable_count"] = len(nodes) - summary.get("unreachable_count", 0)

    sev_counts = {"high": 0, "medium": 0, "low": 0}
    for issue in issues:
        sev = issue.get("severity", "medium")
        if sev in sev_counts:
            sev_counts[sev] += 1
    md["issue_severity"] = sev_counts

    # 4. completeness — 분리된 metric (Phase A 1번, 2026-04-29).
    # 3 namespace: manifest_reachability / overlay_count / task_coverage.
    # task_coverage 는 Phase B (fixture 도입) 후 실제 값 — app_name 추론.
    # 정의는 screenatlas_completeness_principle 메모리 + _build_completeness() 참조.
    resolved_app = app_name or _infer_app_name(md, static_info)
    md["completeness"] = _build_completeness(nodes, static_info, resolved_app)

    # 5. backward-compat alias — 기존 frontend / API / docs 가 읽던 이름은
    # completeness 로 대체. activity_coverage 는 manifest_reachability 의
    # launched_ratio (loose) 를 가리키는 별칭. activity_coverage_captured 는
    # enriched_ratio (strict) 별칭. 다음 사이클에 frontend 마이그레이션 후 제거.
    mr = md["completeness"]["manifest_reachability"]
    if mr.get("launched_ratio") is not None:
        md["activity_coverage"] = mr["launched_ratio"]
        md["activity_coverage_captured"] = mr["enriched_ratio"]

    logger.info(
        "[metadata_refresh] nodes=%d edges=%d actionable=%d plannable=%d "
        "issues=%d (high=%d) overlay=%d",
        md["total_nodes"], md["total_edges"], md["actionable_nodes"],
        md["plannable_nodes"], md["validation_issues"], sev_counts["high"],
        md["completeness"]["overlay_count"]["total"],
    )
    return md
