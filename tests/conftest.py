"""테스트 공통 픽스처.

config.py 가 import 시 프로젝트 .env 를 os.environ 에 override 로 로드한다. 개발자가 대시보드에서
LLM_MODE=openai 로 저장해 두면 VisionTapper/TarpitEscaper 테스트가 로컬 경로를 타 버리므로(2026-09-12),
모든 테스트는 기본 'api' 모드에서 시작한다. 로컬 공급자 테스트는 monkeypatch 로 직접 설정한다.
"""

import pytest


@pytest.fixture(autouse=True)
def _default_llm_env(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "api")
    for k in ("LLM_BASE_URL", "LLM_MODEL_SCREEN", "LLM_MODEL_VISION", "LLM_API_KEY", "LLM_STAGE5_MODE",
              "LLM_STAGE5_VISION", "LLM_JSON_MODE", "LLM_PICK_ALLOW_FREE"):
        monkeypatch.delenv(k, raising=False)
    yield
