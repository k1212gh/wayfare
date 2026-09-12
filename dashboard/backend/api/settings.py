"""LLM 공급자 설정 API — 대시보드에서 Claude API / 로컬 LLM / 사용 안 함을 고른다.

    GET  /api/settings/llm        현재 설정 (키는 마스킹)
    PUT  /api/settings/llm        설정 저장 → os.environ 즉시 반영 + .env 파일 갱신
    POST /api/settings/llm/test   현재(또는 body 의) 설정으로 연결 테스트

.env 는 config.py 가 프로세스 시작 시 os.environ 에 override 로 로드하므로,
여기서 두 곳을 함께 갱신하면 재시작 없이 다음 파이프라인부터 적용된다.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = PROJECT_ROOT / ".env"

# 대시보드가 다루는 키 (그 외 .env 줄은 그대로 보존)
LLM_KEYS = (
    "LLM_MODE", "LLM_BASE_URL", "LLM_MODEL_SCREEN", "LLM_MODEL_VISION", "LLM_MODEL_WIDGET",
    "LLM_API_KEY", "ANTHROPIC_API_KEY", "LLM_TIMEOUT", "LLM_STAGE5_MODE", "LLM_STAGE5_VISION",
    "LLM_JSON_MODE",
)
SECRET_KEYS = ("LLM_API_KEY", "ANTHROPIC_API_KEY")
MODES = ("api", "openai", "off", "cli")
_SAFE_URL = re.compile(r"^https?://[A-Za-z0-9.\-_:\[\]]+(?::\d{1,5})?(/[A-Za-z0-9._\-/]*)?$")
_SAFE_MODEL = re.compile(r"^[A-Za-z0-9._:\-/ ]{1,120}$")


class LLMSettings(BaseModel):
    mode: str = Field("api", description="api | openai | off | cli")
    base_url: str = ""
    model_screen: str = ""
    model_vision: str = ""
    api_key: str | None = Field(None, description="로컬 서버 키 (비우면 유지, '' 이면 삭제)")
    anthropic_api_key: str | None = Field(None, description="비우면 유지, '' 이면 삭제")
    timeout: int | None = None
    stage5_mode: str = ""          # '' = 자동 (로컬→vision_name, api→screenmap_annotate)
    stage5_vision: bool | None = None
    json_mode: bool | None = None


def _mask(v: str) -> str:
    if not v or "PLACEHOLDER" in v:
        return ""
    return ("*" * 6) + v[-4:] if len(v) > 4 else "****"


def _read_env_file() -> list[str]:
    if not ENV_PATH.exists():
        return []
    return ENV_PATH.read_text(encoding="utf-8").splitlines()


def _write_env_file(updates: dict[str, str | None]) -> None:
    """키 갱신(None 이면 삭제). 다른 줄/주석은 보존. 원자적 저장."""
    lines = _read_env_file()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=", line)
        key = m.group(1) if m else None
        if key in updates:
            seen.add(key)
            if updates[key] is None:
                continue
            out.append(f"{key}={updates[key]}")
        else:
            out.append(line)
    for key, val in updates.items():
        if key not in seen and val is not None:
            out.append(f"{key}={val}")
    tmp = ENV_PATH.with_suffix(".env.tmp")
    tmp.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    tmp.replace(ENV_PATH)


def _current() -> dict:
    from stage5_annotate.llm_client import is_llm_configured, llm_mode, OPENAI_COMPAT_MODES
    mode = llm_mode()
    ui_mode = "openai" if mode in OPENAI_COMPAT_MODES else mode
    ok, reason = is_llm_configured()
    return {
        "mode": ui_mode if ui_mode in MODES else "api",
        "base_url": os.environ.get("LLM_BASE_URL", ""),
        "model_screen": os.environ.get("LLM_MODEL_SCREEN", ""),
        "model_vision": os.environ.get("LLM_MODEL_VISION", ""),
        "api_key_masked": _mask(os.environ.get("LLM_API_KEY", "")),
        "anthropic_api_key_masked": _mask(os.environ.get("ANTHROPIC_API_KEY", "")),
        "timeout": int(float(os.environ.get("LLM_TIMEOUT", "0") or 0)) or None,
        "stage5_mode": os.environ.get("LLM_STAGE5_MODE", ""),
        "stage5_vision": os.environ.get("LLM_STAGE5_VISION", "").lower() in ("1", "true", "yes"),
        "json_mode": os.environ.get("LLM_JSON_MODE", "").lower() in ("1", "true", "yes"),
        "configured": ok,
        "reason": reason,
        "env_path": str(ENV_PATH),
    }


@router.get("/api/settings/llm")
async def get_llm_settings():
    return _current()


@router.put("/api/settings/llm")
async def put_llm_settings(body: LLMSettings):
    mode = (body.mode or "api").strip().lower()
    if mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    updates: dict[str, str | None] = {"LLM_MODE": mode}

    if mode == "openai":
        base = body.base_url.strip().rstrip("/")
        if not base or not _SAFE_URL.match(base):
            raise HTTPException(400, "base_url 형식이 올바르지 않습니다 (예: http://192.168.0.10:11434/v1)")
        if not body.model_screen.strip() or not _SAFE_MODEL.match(body.model_screen.strip()):
            raise HTTPException(400, "model_screen 이 필요합니다 (예: qwen2.5:7b-instruct)")
        if body.model_vision and not _SAFE_MODEL.match(body.model_vision.strip()):
            raise HTTPException(400, "model_vision 형식이 올바르지 않습니다")
        updates["LLM_BASE_URL"] = base
        updates["LLM_MODEL_SCREEN"] = body.model_screen.strip()
        updates["LLM_MODEL_VISION"] = body.model_vision.strip() or None
    elif mode == "api":
        if body.model_screen.strip():
            if not _SAFE_MODEL.match(body.model_screen.strip()):
                raise HTTPException(400, "model_screen 형식이 올바르지 않습니다")
            updates["LLM_MODEL_SCREEN"] = body.model_screen.strip()

    if body.api_key is not None:
        updates["LLM_API_KEY"] = body.api_key.strip() or None
    if body.anthropic_api_key is not None:
        updates["ANTHROPIC_API_KEY"] = body.anthropic_api_key.strip() or None
    if body.timeout is not None:
        if not (10 <= body.timeout <= 3600):
            raise HTTPException(400, "timeout 은 10~3600 초")
        updates["LLM_TIMEOUT"] = str(body.timeout)
    s5 = (body.stage5_mode or "").strip()
    if s5 and s5 not in ("vision_name", "screenmap_annotate", "grounded", "vision_only", "legacy"):
        raise HTTPException(400, "stage5_mode 값이 올바르지 않습니다")
    updates["LLM_STAGE5_MODE"] = s5 or None
    if body.stage5_vision is not None:
        updates["LLM_STAGE5_VISION"] = "1" if body.stage5_vision else None
    if body.json_mode is not None:
        updates["LLM_JSON_MODE"] = "1" if body.json_mode else None

    # 1) 프로세스 환경 즉시 반영 — 다음 파이프라인부터 적용
    for k, v in updates.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    # 2) .env 영속화
    try:
        _write_env_file(updates)
    except Exception as e:  # noqa: BLE001
        logger.warning("settings: .env write failed: %s", e)
        raise HTTPException(500, f".env 저장 실패: {e}")
    logger.info("LLM settings updated: mode=%s keys=%s", mode, [k for k in updates if k not in SECRET_KEYS])
    return _current()


class LLMTestBody(BaseModel):
    mode: str | None = None
    base_url: str | None = None
    model_screen: str | None = None
    model_vision: str | None = None
    api_key: str | None = None
    with_vision: bool = False


@router.post("/api/settings/llm/test")
async def test_llm_settings(body: LLMTestBody | None = None):
    """body 가 있으면 그 값으로(저장 없이), 없으면 현재 설정으로 테스트."""
    body = body or LLMTestBody()
    from stage5_annotate.llm_client import OPENAI_COMPAT_MODES, llm_mode
    mode = (body.mode or llm_mode()).lower()
    t0 = time.time()
    if mode in OPENAI_COMPAT_MODES or mode == "openai":
        from stage5_annotate.llm_client import OpenAICompatClient
        try:
            saved = os.environ.get("LLM_API_KEY")
            if body.api_key:
                os.environ["LLM_API_KEY"] = body.api_key
            client = OpenAICompatClient(
                base_url=body.base_url or "", model_screen=body.model_screen or "",
                model_vision=body.model_vision or "", timeout_s=60.0, max_retries=1,
            )
            result = client.test_connection(with_vision=body.with_vision)
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": str(e)[:300]}
        finally:
            if body.api_key:
                if saved is None:
                    os.environ.pop("LLM_API_KEY", None)
                else:
                    os.environ["LLM_API_KEY"] = saved
        result["mode"] = "openai"
    elif mode == "api":
        key = body.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key or "PLACEHOLDER" in key:
            result = {"ok": False, "mode": "api", "error": "ANTHROPIC_API_KEY 없음"}
        else:
            try:
                import anthropic
                model = body.model_screen or os.environ.get("LLM_MODEL_SCREEN", "claude-haiku-4-5-20251001")
                c = anthropic.Anthropic(api_key=key, timeout=30.0, max_retries=0)
                msg = c.messages.create(model=model, max_tokens=16,
                                        messages=[{"role": "user", "content": 'Reply exactly: {"ok": true}'}])
                txt = "".join(getattr(b, "text", "") for b in msg.content)
                result = {"ok": '"ok"' in txt, "mode": "api", "model": model, "sample": txt[:60]}
            except Exception as e:  # noqa: BLE001
                result = {"ok": False, "mode": "api", "error": str(e)[:300]}
    else:
        result = {"ok": False, "mode": mode, "error": "테스트할 공급자가 아닙니다 (off/cli)"}
    result["elapsed_ms"] = int((time.time() - t0) * 1000)
    return result


# ─── 자동 탐지: IP 하나로 Ollama / LM Studio / Open WebUI 찾기 ────────────────
# 셋 다 OpenAI 호환 chat/completions 를 제공하므로 클라이언트는 하나, 주소/인증만 다르다.
#   Ollama     : http://host:11434/v1   (키 불필요; 네이티브 /api/tags 도 있음)
#   LM Studio  : http://host:1234/v1    (키 불필요; Developer 탭 → Server, 로컬 네트워크 서빙 켜기)
#   Open WebUI : http://host:3000/api   (Settings → Account → API Keys 의 Bearer 키 필요; 8080 도 흔함)
PROVIDER_PROBES: list[dict] = [
    {"provider": "ollama", "port": 11434, "base": "/v1", "models_path": "/v1/models", "needs_key": False},
    {"provider": "lmstudio", "port": 1234, "base": "/v1", "models_path": "/v1/models", "needs_key": False},
    {"provider": "openwebui", "port": 3000, "base": "/api", "models_path": "/api/models", "needs_key": True},
    {"provider": "openwebui", "port": 8080, "base": "/api", "models_path": "/api/models", "needs_key": True},
]
# 2026-09-12 벤치(docs/local_llm_benchmark.md): Qwen3.5·Gemma 4 는 텍스트+이미지 네이티브 멀티모달
_VISION_HINTS = ("vl", "vision", "llava", "minicpm-v", "moondream", "gemma3", "gemma4", "pixtral",
                 "qwen2.5vl", "qwen3.5", "qwen3-vl", "bakllava")
# 탐지 시 자동 선택 우선순위 (앞일수록 권장). 2026-09-13 벤치 2회 합산: 텍스트(후보 선택)는 gemma4 가
# 어려운 화면(오버레이)에서 더 정확하고, 비전(스크린샷 라벨)은 qwen3.5 가 두 번 다 오답 0. 사용자 선택: 정확도 우선.
RECOMMENDED_MODELS = ("gemma4:12b", "qwen3.5:9b", "qwen3.5:4b", "qwen2.5:7b-instruct")
RECOMMENDED_VISION = ("qwen3.5:9b", "gemma4:12b", "qwen3.5:4b", "qwen2.5vl:7b")
_SAFE_HOST = re.compile(r"^[A-Za-z0-9.\-_\[\]:]{1,128}$")


def _looks_vision(model_id: str) -> bool:
    m = model_id.lower()
    return any(h in m for h in _VISION_HINTS)


class DiscoverBody(BaseModel):
    host: str = "127.0.0.1"
    api_key: str = ""          # Open WebUI 키 (있으면 모델 목록까지 조회)
    extra_ports: list[int] = Field(default_factory=list)


@router.post("/api/settings/llm/discover")
async def discover_llm_servers(body: DiscoverBody):
    """host 의 잘 알려진 포트를 동시에 찔러 떠 있는 로컬 LLM 서버와 모델 목록을 돌려준다."""
    import asyncio
    import httpx

    host = body.host.strip() or "127.0.0.1"
    if not _SAFE_HOST.match(host):
        raise HTTPException(400, "host 형식이 올바르지 않습니다")
    probes = list(PROVIDER_PROBES)
    for port in body.extra_ports[:8]:
        if 1 <= port <= 65535:
            probes.append({"provider": "custom", "port": port, "base": "/v1", "models_path": "/v1/models", "needs_key": False})

    async def probe(pr: dict) -> dict | None:
        url = f"http://{host}:{pr['port']}{pr['models_path']}"
        headers = {}
        if body.api_key:
            headers["Authorization"] = f"Bearer {body.api_key}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(2.5, connect=1.5)) as c:
                r = await c.get(url, headers=headers)
        except Exception:
            return None
        found = {"provider": pr["provider"], "port": pr["port"],
                 "base_url": f"http://{host}:{pr['port']}{pr['base']}", "needs_key": pr["needs_key"],
                 "status": r.status_code, "models": [], "vision_models": []}
        if r.status_code == 200:
            try:
                data = r.json()
                items = data.get("data") if isinstance(data, dict) else data
                ids = [m.get("id", "") for m in (items or []) if isinstance(m, dict) and m.get("id")]
                found["models"] = ids[:100]
                found["vision_models"] = [m for m in ids if _looks_vision(m)][:20]
                found["recommended"] = next((m for m in RECOMMENDED_MODELS if m in ids), "")
                found["recommended_vision"] = next((m for m in RECOMMENDED_VISION if m in ids), "")
            except Exception:
                pass
            # Ollama 는 /v1/models 가 'object: list' 로 오고 LM Studio 도 같으므로 포트로 구분한 provider 유지
            return found
        if r.status_code in (401, 403) and pr["needs_key"]:
            found["hint"] = "API 키가 필요합니다 (Open WebUI: Settings → Account → API Keys)"
            return found
        return None

    results = await asyncio.gather(*(probe(pr) for pr in probes))
    found = [r for r in results if r]
    # 같은 provider 가 두 포트에서 잡히면 모델이 있는 쪽 우선
    found.sort(key=lambda f: (f["provider"], -len(f["models"])))
    return {"host": host, "found": found, "probed_ports": sorted({pr["port"] for pr in probes})}
