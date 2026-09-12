"""vision_namer — 스크린샷 자유 생성 + 후보 스냅 라벨링."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from stage5_annotate import vision_namer as vn


def test_snap_rules():
    cands = ["주문내역 2개", "주문내역", "메가오더", "사전예약 내역이 없습니다."]
    assert vn.snap_to_candidates("주문 내역", cands) == "주문내역"          # 공백 무시 완전 일치
    assert vn.snap_to_candidates("사전예약", cands) is None               # 라벨보다 3자 넘게 긴 후보엔 안 붙음
    assert vn.snap_to_candidates("주문내역 2", cands) == "주문내역 2개"     # 살짝 긴 후보는 허용
    assert vn.snap_to_candidates("영수증", cands) is None
    assert vn.snap_to_candidates("영수증", ["A"]) is None                 # 너무 짧은 후보는 무시
    # 라벨이 후보를 포함: 절반 이상 덮는 가장 긴 후보 ("등록" 같은 조각엔 안 붙음)
    assert vn.snap_to_candidates("메가퀵결제 등록", ["메가퀵결제", "등록"]) == "메가퀵결제"
    assert vn.snap_to_candidates("바코드 번호로 등록", ["바코드 번호로 등록하기", "메가쿠폰"]) == "바코드 번호로 등록하기"
    assert vn.snap_to_candidates("CJ ONE", ["CJ ONE 등록하기 등록 아이콘", "CJ ONE 유의사항"]) is None


def test_clean_label_rejects_generic_store_and_suffix():
    assert vn.clean_label("퀵오더 결제 화면") == "퀵오더 결제"
    assert vn.clean_label("닫기") == ""
    assert vn.clean_label("화성마도산업단지점") == ""
    assert vn.clean_label("page_04aefaddbfa9") == ""
    assert vn.clean_label('"영수증"') == "영수증"


def test_apply_answer_sets_source_and_category():
    n = {"screen_id": "page_a", "label": "1개월", "label_source": "fallback", "label_candidates": ["1개월", "픽업완료"]}
    assert vn.apply_answer(n, {"label": "영수증", "category": "dialog"})
    assert n["label"] == "영수증" and n["label_source"] == "vision" and n["functional_category"] == "dialog"
    m = {"screen_id": "page_b", "label": "메가오더", "label_source": "fallback", "label_candidates": ["메가오더", "사전예약"]}
    assert vn.apply_answer(m, {"label": "사전 예약", "category": "nope"})
    assert m["label"] == "사전예약" and m["label_source"] == "vision_snapped" and "functional_category" not in m
    k = {"screen_id": "page_c", "label": "스탬프", "label_source": "fallback"}
    assert not vn.apply_answer(k, {"label": "닫기"})
    assert k["label"] == "스탬프"


class _FakeClient:
    def __init__(self, answers, fail_ids=()):
        self.answers = answers
        self.fail_ids = set(fail_ids)
        self.calls = []

    def query_with_image(self, system, user, image_bytes, image_media_type="image/jpeg", model=None, max_tokens=200):
        sid = user.split("screen id: ")[1].split(".")[0]
        self.calls.append(sid)
        if sid in self.fail_ids:
            raise RuntimeError("no vision")
        return json.dumps(self.answers[sid])


def _config(tmp_path: Path, monkeypatch):
    from config import PipelineConfig
    cfg = PipelineConfig(apk_path="", tour_id="t", workspace_root=str(tmp_path))
    monkeypatch.setattr(cfg.__class__, "tour_dir", property(lambda self: tmp_path))
    monkeypatch.setattr(cfg.__class__, "output_dir", property(lambda self: tmp_path / "output"))
    (tmp_path / "output").mkdir(exist_ok=True)
    return cfg


def _screenmap(tmp_path: Path, nodes):
    shots = tmp_path / "analysis" / "screens"
    shots.mkdir(parents=True, exist_ok=True)
    for n in nodes:
        if n.get("screenshot_ref"):
            (shots / n["screenshot_ref"]).write_bytes(b"\xff\xd8fake")
            n["screenshot_ref"] = str(shots / n["screenshot_ref"])
    p = tmp_path / "output" / "screen_map.json"
    p.write_text(json.dumps({"screen_map": {"graph": {"nodes": nodes, "edges": []}}}), encoding="utf-8")
    return p


def test_name_screens_with_vision_names_and_skips(tmp_path, monkeypatch):
    cfg = _config(tmp_path, monkeypatch)
    monkeypatch.setattr(vn, "_push_progress", lambda *_: None)
    monkeypatch.setattr(vn, "_raise_if_cancelled", lambda *_: None)
    path = _screenmap(tmp_path, [
        {"screen_id": "page_a", "label": "1개월", "label_source": "fallback", "label_candidates": ["1개월"], "screenshot_ref": "a.jpg"},
        {"screen_id": "page_b", "label": "매장 정보", "label_source": "fallback", "label_candidates": ["매장 정보"], "screenshot_ref": "b.jpg"},
        {"screen_id": "page_c", "label": "이미 됨", "label_source": "vision", "screenshot_ref": "c.jpg"},
        {"screen_id": "act_d", "label": "LoginActivity", "label_source": None},
    ])
    client = _FakeClient({"page_a": {"label": "영수증 화면", "category": "dialog"}, "page_b": {"label": "매장정보", "category": "list"}})
    stats = vn.name_screens_with_vision(cfg, client=client)
    saved = {n["screen_id"]: n for n in json.loads(path.read_text(encoding="utf-8"))["screen_map"]["graph"]["nodes"]}
    assert client.calls == ["page_a", "page_b"]
    assert saved["page_a"]["label"] == "영수증" and saved["page_a"]["label_source"] == "vision"
    assert saved["page_b"]["label"] == "매장 정보" and saved["page_b"]["label_source"] == "vision_snapped"
    assert saved["page_c"]["label"] == "이미 됨" and saved["act_d"]["label"] == "LoginActivity"
    assert stats["named"] == 2 and stats["snapped"] == 1 and stats["failed"] == 0


def test_name_screens_stops_early_when_vision_unavailable(tmp_path, monkeypatch):
    cfg = _config(tmp_path, monkeypatch)
    monkeypatch.setattr(vn, "_push_progress", lambda *_: None)
    monkeypatch.setattr(vn, "_raise_if_cancelled", lambda *_: None)
    nodes = [{"screen_id": f"page_{i}", "label": f"L{i}", "label_source": "fallback", "screenshot_ref": f"{i}.jpg"} for i in range(6)]
    _screenmap(tmp_path, nodes)
    client = _FakeClient({}, fail_ids={f"page_{i}" for i in range(6)})
    stats = vn.name_screens_with_vision(cfg, client=client)
    assert stats["failed"] == 2 and stats["named"] == 0 and len(client.calls) == 2


def test_run_stage5_vision_name_dispatch(monkeypatch):
    import stage5_annotate as s5
    calls = []
    monkeypatch.setattr("stage5_annotate.vision_namer.name_screens_with_vision", lambda cfg: calls.append("namer"))
    monkeypatch.setattr("stage5_annotate.label_picker.pick_labels", lambda cfg: calls.append("picker"))
    monkeypatch.setattr("stage5_annotate.vision_labeler.label_screens_with_vision", lambda cfg: calls.append("labeler"))
    monkeypatch.delenv("LLM_STAGE5_VISION", raising=False)
    s5.run_stage5(object(), mode="vision_name")
    assert calls == ["namer", "picker"]
    monkeypatch.setenv("LLM_STAGE5_VISION", "1")
    calls.clear()
    s5.run_stage5(object(), mode="vision_name")
    assert calls == ["namer", "picker", "labeler"]


def test_vision_labeler_keeps_namer_label():
    from stage5_annotate.vision_labeler import _apply_vision_annotation
    n = {"screen_id": "page_a", "label": "영수증", "label_source": "vision"}
    _apply_vision_annotation(n, {"label": "주문 내역 화면", "screen_purpose": "영수증을 확인한다", "functional_category": "dialog"})
    assert n["label"] == "영수증" and n["screen_purpose"] == "영수증을 확인한다" and n["functional_category"] == "dialog"
    m = {"screen_id": "page_b", "label": "1개월", "label_source": "fallback"}
    _apply_vision_annotation(m, {"label": "영수증 화면"})
    assert m["label"] == "영수증" and m["label_source"] == "llm"
