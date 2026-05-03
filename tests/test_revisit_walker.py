"""RevisitWalker 단위 테스트 — ScreenMap load + provisional list + path BFS."""

from __future__ import annotations

import json
from pathlib import Path

from stage3_walk.revisit_walker import RevisitWalker, ERROR_LABEL_PATTERNS


def _screenmap(nodes, edges, entry="entry"):
    return {
        "screen_map": {
            "graph": {
                "nodes": nodes,
                "edges": edges,
                "entry_node": entry,
            }
        }
    }


def _node(screen_id, activity="com.app.A", label="", category="other",
          is_provisional=False, structure_str=""):
    return {
        "screen_id": screen_id, "activity": activity, "label": label,
        "functional_category": category, "is_provisional": is_provisional,
        "structure_str": structure_str,
    }


def _edge(f, t, action="click", elem=""):
    return {"from": f, "to": t, "trigger_action": action,
            "trigger_widget": elem, "edge_id": f"e_{f}_{t}"}


def _write_screenmap(tmp_path, nodes, edges, entry="entry"):
    p = tmp_path / "screen_map.json"
    p.write_text(json.dumps(_screenmap(nodes, edges, entry)), encoding="utf-8")
    return p


# ─── load + provisional list ──────────────────


def test_load_kg_extracts_provisional(tmp_path):
    nodes = [
        _node("entry"),
        _node("a", is_provisional=False),
        _node("b", is_provisional=True, label="WebView page"),
        _node("c", is_provisional=True, label="MainActivity"),
    ]
    p = _write_screenmap(tmp_path, nodes, [])
    px = RevisitWalker(p)
    assert px.load() is True
    assert len(px.provisional_nodes) == 2
    assert {n["screen_id"] for n in px.provisional_nodes} == {"b", "c"}


def test_error_labels_skipped(tmp_path):
    nodes = [
        _node("entry"),
        _node("err1", is_provisional=True, label="웹페이지 오류"),
        _node("err2", is_provisional=True, label="웹페이지 로드 오류"),
        _node("ok", is_provisional=True, label="2026 메가콘서트 이벤트"),
    ]
    p = _write_screenmap(tmp_path, nodes, [])
    px = RevisitWalker(p)
    px.load()
    # Error 2개 skip + clean 1개
    assert len(px.skipped_error) == 2
    assert len(px.provisional_nodes) == 1
    assert px.provisional_nodes[0]["screen_id"] == "ok"


def test_load_missing_screenmap(tmp_path):
    px = RevisitWalker(tmp_path / "nonexistent.json")
    assert px.load() is False


def test_load_no_provisional_nodes(tmp_path):
    nodes = [_node("entry"), _node("a"), _node("b")]
    p = _write_screenmap(tmp_path, nodes, [])
    px = RevisitWalker(p)
    assert px.load()
    assert px.provisional_nodes == []


# ─── find_entry_path BFS ──────────────────────


def test_find_path_direct_edge(tmp_path):
    nodes = [_node("entry"), _node("a", is_provisional=True)]
    edges = [_edge("entry", "a")]
    p = _write_screenmap(tmp_path, nodes, edges, entry="entry")
    px = RevisitWalker(p)
    px.load()
    path = px.find_entry_path("a")
    assert len(path) == 1
    assert path[0]["from"] == "entry"
    assert path[0]["to"] == "a"


def test_find_path_multi_hop(tmp_path):
    nodes = [_node(s) for s in ["entry", "a", "b", "c"]]
    nodes[3]["is_provisional"] = True
    edges = [_edge("entry", "a"), _edge("a", "b"), _edge("b", "c")]
    p = _write_screenmap(tmp_path, nodes, edges)
    px = RevisitWalker(p)
    px.load()
    path = px.find_entry_path("c")
    assert len(path) == 3
    assert [e["to"] for e in path] == ["a", "b", "c"]


def test_find_path_unreachable_returns_empty(tmp_path):
    nodes = [_node("entry"), _node("isolated", is_provisional=True)]
    edges = []  # No edges
    p = _write_screenmap(tmp_path, nodes, edges)
    px = RevisitWalker(p)
    px.load()
    assert px.find_entry_path("isolated") == []


# ─── report ───────────────────────────────


def test_report_summarizes_reachability(tmp_path):
    nodes = [
        _node("entry"),
        _node("a"),
        _node("provisional_reach", is_provisional=True, label="reachable"),
        _node("provisional_isolated", is_provisional=True, label="isolated"),
    ]
    edges = [_edge("entry", "a"), _edge("a", "provisional_reach")]
    p = _write_screenmap(tmp_path, nodes, edges)
    px = RevisitWalker(p)
    px.load()
    r = px.report()
    assert r["stats"]["provisional_total"] == 2
    assert r["stats"]["reachable"] == 1
    assert r["stats"]["unreachable"] == 1
    items = r["items"]
    reach = next(i for i in items if i["screen_id"] == "provisional_reach")
    iso = next(i for i in items if i["screen_id"] == "provisional_isolated")
    assert reach["reachable"] is True
    assert reach["path_length"] == 2
    assert iso["reachable"] is False


def test_report_shows_skipped_error_labels(tmp_path):
    nodes = [
        _node("entry"),
        _node("err", is_provisional=True, label="웹페이지 오류"),
        _node("ok", is_provisional=True, label="이벤트 페이지"),
    ]
    p = _write_screenmap(tmp_path, nodes, [])
    px = RevisitWalker(p)
    px.load()
    r = px.report()
    assert "웹페이지 오류" in r["skipped_error_labels"]
    assert r["stats"]["skipped_error"] == 1
    assert r["stats"]["provisional_total"] == 2


# ─── write_report ─────────────────────────


def test_write_report_creates_file(tmp_path):
    nodes = [_node("entry"), _node("a", is_provisional=True)]
    edges = [_edge("entry", "a")]
    p = _write_screenmap(tmp_path, nodes, edges)
    px = RevisitWalker(p)
    px.load()
    out_path = tmp_path / "revisit_report.json"
    px.write_report(out_path)
    assert out_path.exists()
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert "stats" in saved
    assert "items" in saved
