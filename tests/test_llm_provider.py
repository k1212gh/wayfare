"""LLM 공급자 선택 — OpenAI 호환 클라이언트(mock 서버), 팩토리 분기, 설정 API, 후보 선택형 라벨링."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from stage5_annotate import llm_client as lc


# ─── mock OpenAI 호환 서버 ─────────────────────────────────────────

def _mock_transport(reply: str | Exception = '{"ok": true}', status: int = 200, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content or b"{}") if request.content else {"path": request.url.path})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen2.5:7b"}, {"id": "qwen2.5vl:7b"}]})
        if isinstance(reply, Exception):
            raise reply
        if status != 200:
            return httpx.Response(status, text="err")
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
    return httpx.MockTransport(handler)


def _client(monkeypatch, reply='{"ok": true}', status=200, seen=None, **env):
    monkeypatch.setenv("LLM_BASE_URL", "http://gpu-box:11434/v1")
    monkeypatch.setenv("LLM_MODEL_SCREEN", "qwen2.5:7b")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    c = lc.OpenAICompatClient(max_retries=2, timeout_s=5)
    c.client = httpx.Client(base_url=c.base_url, transport=_mock_transport(reply, status, seen))
    return c


def test_factory_routes_by_mode(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "http://gpu-box:11434/v1")
    monkeypatch.setenv("LLM_MODEL_SCREEN", "qwen2.5:7b")
    assert isinstance(lc.create_client(), lc.OpenAICompatClient)
    assert lc.is_llm_configured() == (True, "openai:qwen2.5:7b@http://gpu-box:11434/v1")
    monkeypatch.setenv("LLM_MODE", "off")
    assert lc.is_llm_configured()[0] is False
    monkeypatch.setenv("LLM_MODE", "api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-PLACEHOLDER")
    assert lc.is_llm_configured() == (False, "ANTHROPIC_API_KEY missing")
    monkeypatch.setenv("LLM_MODE", "local")
    monkeypatch.delenv("LLM_BASE_URL")
    assert lc.is_llm_configured() == (False, "LLM_BASE_URL not set")


def test_openai_query_json_and_request_shape(monkeypatch):
    seen: list = []
    c = _client(monkeypatch, reply='```json\n{"nodes": [{"id": "a", "pick": 0}]}\n```', seen=seen)
    out = c.query_json("SYS", "USER", max_tokens=99)
    assert out == {"nodes": [{"id": "a", "pick": 0}]}
    body = seen[-1]
    assert body["model"] == "qwen2.5:7b" and body["max_tokens"] == 99 and body["stream"] is False
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    assert body["messages"][1]["content"] == "USER"
    assert "response_format" not in body          # LLM_JSON_MODE 미설정


def test_openai_json_mode_flag(monkeypatch):
    seen: list = []
    c = _client(monkeypatch, seen=seen, LLM_JSON_MODE="1")
    c.query_json("s", "u")
    assert seen[-1]["response_format"] == {"type": "json_object"}


def test_openai_vision_payload_uses_vision_model(monkeypatch):
    seen: list = []
    c = _client(monkeypatch, reply="orange", seen=seen, LLM_MODEL_VISION="qwen2.5vl:7b")
    txt = c.query_with_image("s", "what color", b"\xff\xd8jpegbytes", image_media_type="image/jpeg")
    assert txt == "orange"
    body = seen[-1]
    assert body["model"] == "qwen2.5vl:7b"
    parts = body["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": "what color"}
    assert parts[1]["type"] == "image_url" and parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_openai_retries_then_raises(monkeypatch):
    c = _client(monkeypatch, reply="not json at all")
    monkeypatch.setattr(lc.time, "sleep", lambda *_: None)
    with pytest.raises(RuntimeError, match="failed after 2 attempts"):
        c.query_json("s", "u")


def test_openai_auth_error_not_retried(monkeypatch):
    c = _client(monkeypatch, status=401)
    with pytest.raises(RuntimeError, match="authentication"):
        c.query_text("s", "u")


def test_openai_test_connection(monkeypatch):
    c = _client(monkeypatch, reply='{"ok": true, "model_says": "qwen"}')
    r = c.test_connection()
    assert r["ok"] is True and r["json_ok"] is True
    assert "qwen2.5vl:7b" in r["models"]


def test_requires_base_url_and_model(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="LLM_BASE_URL"):
        lc.OpenAICompatClient()
    monkeypatch.setenv("LLM_BASE_URL", "http://x:1/v1")
    monkeypatch.delenv("LLM_MODEL_SCREEN", raising=False)
    with pytest.raises(RuntimeError, match="LLM_MODEL_SCREEN"):
        lc.OpenAICompatClient()


# ─── Stage 4 후보 추출 ─────────────────────────────────────────────

def _v(text="", desc="", y1=0, y2=None, cls="android.widget.TextView"):
    y2 = y2 if y2 is not None else y1 + 40
    return {"text": text, "content_desc": desc, "class": cls, "bounds": f"[0,{y1}][1440,{y2}]", "visible": True}


def test_label_candidates_skip_status_bar_and_noise():
    from stage4_screens.screen_clusterer import _extract_title, extract_label_candidates
    state = {"views": [
        _v(desc="11번가 알림:", y1=25), _v(text="5:13", y1=25), _v(desc="Android 시스템", y1=25),
        _v(text="매장 정보", y1=287), _v(desc="리스트", y1=322, cls="android.view.View"),
        _v(text="/3", y1=400), _v(text="화성정남시장점", y1=900),
        _v(text="", y1=3000, y2=3088),   # 화면 높이 힌트
    ]}
    assert _extract_title(state) == "매장 정보"                 # 250px 고정이었으면 잘렸을 위치
    cands = extract_label_candidates(state)
    assert cands[0] == "매장 정보"
    assert "/3" not in cands and "11번가 알림:" not in cands and "5:13" not in cands
    assert "화성정남시장점" in cands


def test_fallback_label_chain():
    from stage6_screenmap.screenmap_builder import fallback_label, short_activity_name
    unit = {"title_text": "", "label_candidates": ["추천메뉴", "확인"]}
    assert fallback_label("LLM 라벨", unit, "a.b.MainActivity", "page_1") == "LLM 라벨"
    assert fallback_label("", dict(unit, title_text="결제"), "a.b.MainActivity", "page_1") == "결제"
    assert fallback_label("", unit, "a.b.MainActivity", "page_1") == "추천메뉴"
    assert fallback_label("", {"label_candidates": []}, "co.kr.app.ui.webkit.WebActivity", "page_1") == "Web"
    assert short_activity_name("com.android.deskclock.DeskClock", "CLOCKS") == "DeskClock · CLOCKS"


# ─── 후보 선택형 라벨링 ────────────────────────────────────────────

class _PickClient:
    def __init__(self, reply):
        self.reply = reply
        self.prompts: list[str] = []

    def query_json(self, system_prompt, user_prompt, **kw):
        self.prompts.append(user_prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _screenmap(tmp_path: Path, nodes: list[dict]) -> Path:
    out = tmp_path / "output"; out.mkdir()
    p = out / "screen_map.json"
    p.write_text(json.dumps({"screen_map": {"graph": {"nodes": nodes, "edges": []}, "metadata": {}}}), encoding="utf-8")
    return p


def _config(tmp_path: Path):
    from config import PipelineConfig
    return PipelineConfig(apk_path="x.apk", tour_id="t1", workspace_root=str(tmp_path.parent), )


def test_label_picker_applies_pick_and_free_label(tmp_path, monkeypatch):
    from stage5_annotate import label_picker as lp
    cfg = _config(tmp_path)
    monkeypatch.setattr(cfg.__class__, "tour_dir", property(lambda self: tmp_path))
    monkeypatch.setattr(cfg.__class__, "output_dir", property(lambda self: tmp_path / "output"))
    nodes = [
        {"screen_id": "page_a", "label": "page_a", "label_source": "fallback", "activity": "x.MainActivity",
         "label_candidates": ["매장 정보", "리스트", "퀵오더"], "functional_category": "other"},
        {"screen_id": "page_b", "label": "page_b", "label_source": "fallback", "activity": "x.WebActivity",
         "label_candidates": ["/3", "스탬프"], "functional_category": "other"},
        {"screen_id": "page_c", "label": "결제", "label_source": "llm", "activity": "x.WebActivity",
         "label_candidates": ["결제"], "functional_category": "form"},
        {"screen_id": "system:external_entry", "label": "system:external_entry", "label_candidates": []},
    ]
    path = _screenmap(tmp_path, nodes)
    client = _PickClient({"nodes": [
        {"id": "page_a", "pick": 0, "category": "list"},
        {"id": "page_b", "pick": -1, "label": "스탬프 적립 현황 페이지입니다 정말로", "category": "detail"},
        {"id": "page_c", "pick": 0, "category": "home"},          # 대상 아님 → 무시
        {"id": "nope", "pick": 0},
    ]})
    monkeypatch.setattr(lp, "_push_progress", lambda *_: None)
    monkeypatch.setattr(lp, "_raise_if_cancelled", lambda *_: None)
    stats = lp.pick_labels(cfg, client=client)
    assert stats["targets"] == 2 and stats["applied"] == 2 and stats["batches"] == 1
    saved = {n["screen_id"]: n for n in json.loads(path.read_text(encoding="utf-8"))["screen_map"]["graph"]["nodes"]}
    assert saved["page_a"]["label"] == "매장 정보" and saved["page_a"]["label_source"] == "picked"
    assert saved["page_a"]["functional_category"] == "list"
    assert saved["page_b"]["label"] == "스탬프 적립 현황 페이지입니다"[:14] and saved["page_b"]["label_source"] == "llm"
    assert saved["page_c"]["label"] == "결제" and saved["page_c"]["functional_category"] == "form"
    # 프롬프트에 후보가 번호로 들어감, 대상 아닌 노드는 없음
    assert "[0] 매장 정보" in client.prompts[0] and "page_c" not in client.prompts[0]


def test_label_picker_batch_failure_is_nonfatal(tmp_path, monkeypatch):
    from stage5_annotate import label_picker as lp
    cfg = _config(tmp_path)
    monkeypatch.setattr(cfg.__class__, "tour_dir", property(lambda self: tmp_path))
    monkeypatch.setattr(cfg.__class__, "output_dir", property(lambda self: tmp_path / "output"))
    _screenmap(tmp_path, [{"screen_id": "page_a", "label": "page_a", "label_candidates": ["x"]}])
    monkeypatch.setattr(lp, "_push_progress", lambda *_: None)
    monkeypatch.setattr(lp, "_raise_if_cancelled", lambda *_: None)
    stats = lp.pick_labels(cfg, client=_PickClient(RuntimeError("boom")))
    assert stats["failed_batches"] == 1 and stats["applied"] == 0


# ─── 설정 API ──────────────────────────────────────────────────────

def test_settings_api_roundtrip(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import dashboard.backend.api.settings as st
    import dashboard.backend.server as srv
    env = tmp_path / ".env"
    env.write_text("# keep me\nWALK_MODE=tap\nANTHROPIC_API_KEY=sk-ant-PLACEHOLDER\n", encoding="utf-8")
    monkeypatch.setattr(st, "ENV_PATH", env)
    for k in st.LLM_KEYS:
        monkeypatch.delenv(k, raising=False)
    c = TestClient(srv.app)

    r = c.put("/api/settings/llm", json={"mode": "openai", "base_url": "http://192.168.0.10:11434/v1/",
                                         "model_screen": "qwen2.5:7b-instruct", "model_vision": "qwen2.5vl:7b",
                                         "timeout": 600, "json_mode": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["mode"] == "openai" and d["configured"] is True and d["base_url"] == "http://192.168.0.10:11434/v1"
    import os
    assert os.environ["LLM_MODE"] == "openai" and os.environ["LLM_MODEL_VISION"] == "qwen2.5vl:7b"
    text = env.read_text(encoding="utf-8")
    assert "# keep me" in text and "WALK_MODE=tap" in text and "LLM_BASE_URL=http://192.168.0.10:11434/v1" in text
    assert "LLM_JSON_MODE=1" in text

    # 잘못된 URL 거부
    r = c.put("/api/settings/llm", json={"mode": "openai", "base_url": "ftp://x;rm -rf", "model_screen": "m"})
    assert r.status_code == 400

    # off 로 전환 + 키 삭제
    r = c.put("/api/settings/llm", json={"mode": "off", "anthropic_api_key": ""})
    assert r.status_code == 200 and r.json()["configured"] is False
    assert "ANTHROPIC_API_KEY" not in env.read_text(encoding="utf-8")

    r = c.get("/api/settings/llm")
    assert r.status_code == 200 and r.json()["mode"] == "off"


def test_settings_test_endpoint_openai(monkeypatch):
    from fastapi.testclient import TestClient
    import dashboard.backend.server as srv
    monkeypatch.setattr(lc.OpenAICompatClient, "test_connection", lambda self, with_vision=False: {"ok": True, "json_ok": True})
    c = TestClient(srv.app)
    r = c.post("/api/settings/llm/test", json={"mode": "openai", "base_url": "http://gpu:11434/v1", "model_screen": "m"})
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["mode"] == "openai"
    r = c.post("/api/settings/llm/test", json={"mode": "off"})
    assert r.json()["ok"] is False


def test_vision_tapper_and_tarpit_use_local_provider(monkeypatch, tmp_path):
    """LLM_MODE=openai 면 VisionTapper/TarpitEscaper 가 anthropic 없이 공용 클라이언트를 쓴다."""
    monkeypatch.setenv("LLM_MODE", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "http://gpu-box:11434/v1")
    monkeypatch.setenv("LLM_MODEL_SCREEN", "qwen2.5:7b")
    monkeypatch.setenv("LLM_MODEL_VISION", "qwen2.5vl:7b")
    calls: list = []

    class _Fake:
        def query_with_image(self, s, u, img, image_media_type="image/jpeg", max_tokens=0, **kw):
            calls.append(("vision", image_media_type))
            return '{"actionable_widgets": [], "stall_signal": "x"}'

        def query_text(self, s, u, max_tokens=0, **kw):
            calls.append(("text", u[:20]))
            return '{"picks": [{"index": 0}]}'

    monkeypatch.setattr(lc, "create_client", lambda **kw: _Fake())

    from stage3_walk.vision_tapper import VisionTapper
    from stage3_walk.tarpit_escaper import TarpitEscaper
    vt = VisionTapper(api_key="", budget=3)
    assert vt.llm is not None and vt.client is None
    pytest.importorskip("PIL")
    from PIL import Image
    shot = tmp_path / "s.png"
    Image.new("RGB", (100, 200), "white").save(shot)
    assert vt.extract_actionable(shot, state_str="c1") == []
    assert calls[-1][0] == "vision"

    te = TarpitEscaper(api_key="", budget=3)
    assert te.llm is not None
    out = te.suggest([{"text": "메뉴", "content_desc": "", "resource_id": "", "class": "V",
                       "clickable": True, "visible": True}], "Main", set(), [], canonical_id="c1")
    assert out and out[0]["index"] == 0 and calls[-1][0] == "text"


def test_generic_button_texts_demoted():
    from stage4_screens.screen_clusterer import _extract_title, extract_label_candidates
    state = {"views": [_v(text="이전", y1=200), _v(text="닫기", y1=210), _v(text="매장 상세", y1=300),
                       _v(text="", y1=3000, y2=3088)]}
    assert _extract_title(state) == "매장 상세"
    assert extract_label_candidates(state)[:3] == ["매장 상세", "이전", "닫기"]
