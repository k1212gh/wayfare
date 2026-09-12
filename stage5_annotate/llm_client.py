"""LLM client — supports both Claude API and Claude Code CLI modes."""

import json
import os
import re
import subprocess
import time
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# LLM_MODE 값 → 공급자. "openai" 계열은 OpenAI 호환 chat/completions 를 쓰는 로컬 서버
# (Ollama / LM Studio / llama.cpp server / vLLM) 이며 다른 PC 의 GPU 로 라벨링을 돌릴 때 쓴다.
OPENAI_COMPAT_MODES = ("openai", "local", "ollama", "lmstudio", "vllm", "llamacpp")


def llm_mode() -> str:
    return (os.environ.get("LLM_MODE", "api") or "api").strip().lower()


def is_llm_configured() -> tuple[bool, str]:
    """(사용 가능 여부, 사유). pipeline_service 가 Stage 5 실행 여부를 정할 때 사용."""
    mode = llm_mode()
    if mode in ("off", "none", "disabled", "0"):
        return False, "LLM_MODE=off"
    if mode == "cli":
        import shutil
        return (True, "cli") if shutil.which("claude") else (False, "claude CLI not found")
    if mode in OPENAI_COMPAT_MODES:
        base = os.environ.get("LLM_BASE_URL", "").strip()
        model = os.environ.get("LLM_MODEL_SCREEN", "").strip()
        if not base:
            return False, "LLM_BASE_URL not set"
        if not model:
            return False, "LLM_MODEL_SCREEN not set"
        return True, f"{mode}:{model}@{base}"
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key or "PLACEHOLDER" in key:
        return False, "ANTHROPIC_API_KEY missing"
    return True, "anthropic"


def create_client(**kwargs) -> "LLMClient":
    """Factory: create the right client based on LLM_MODE env var.

    api (default)  — Anthropic API
    openai/local/… — OpenAI 호환 서버 (LLM_BASE_URL, LLM_MODEL_SCREEN, LLM_MODEL_VISION)
    cli            — Claude Code CLI (deprecated)
    """
    mode = llm_mode()
    if mode == "cli":
        logger.info("Using CLI mode (deprecated — switch to API when key available)")
        return CLIClient(**kwargs)
    if mode in OPENAI_COMPAT_MODES:
        return OpenAICompatClient(**kwargs)
    return APIClient(**kwargs)


def parse_json_response(text: str) -> dict:
    """모델 응답에서 JSON 객체 추출 (코드펜스 / 앞뒤 잡음 허용). 실패 시 JSONDecodeError."""
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise json.JSONDecodeError(f"No valid JSON ({len(text)} chars)", text[:200], 0)


class LLMClient:
    """Base interface for LLM calls."""

    def query_json(self, system_prompt: str, user_prompt: str, **kw) -> dict[str, Any]:
        raise NotImplementedError

    def query_text(self, system_prompt: str, user_prompt: str, **kw) -> str:
        raise NotImplementedError


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI Mode — calls `claude` subprocess
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CLIClient(LLMClient):
    """Use local Claude Code CLI for LLM calls. No API key needed."""

    def __init__(self, max_retries: int = 3, **_kwargs):
        self.max_retries = max_retries
        self.model = os.environ.get("LLM_CLI_MODEL", "sonnet")

        # Verify CLI is available
        if not self._find_claude():
            raise RuntimeError("claude CLI not found. Install Claude Code first.")
        logger.info("LLM mode: CLI (model=%s)", self.model)

    def _find_claude(self) -> str | None:
        """Find claude executable."""
        import shutil
        return shutil.which("claude")

    def query_json(self, system_prompt: str, user_prompt: str, **_kw) -> dict[str, Any]:
        original_prompt = user_prompt
        for attempt in range(1, self.max_retries + 1):
            prompt = original_prompt
            if attempt > 1:
                prompt = original_prompt + "\n\n[IMPORTANT: Respond ONLY with valid JSON. No markdown, no explanations.]"

            full_prompt = f"{system_prompt}\n\n---\n\n{prompt}"
            text = self._call_cli(full_prompt)

            try:
                return self._parse_json(text)
            except json.JSONDecodeError as e:
                logger.warning("JSON parse failed (attempt %d): %s", attempt, e)
                if attempt == self.max_retries:
                    raise

        raise RuntimeError("query_json failed after retries")

    def query_text(self, system_prompt: str, user_prompt: str, **_kw) -> str:
        full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"
        return self._call_cli(full_prompt)

    def _call_cli(self, prompt: str) -> str:
        """Call claude CLI with --print flag (non-interactive, stdout only)."""
        cmd = [
            "claude",
            "--print",              # non-interactive, print response only
            "--model", self.model,
        ]

        logger.debug("Calling claude CLI (prompt length: %d chars)", len(prompt))

        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=120,
                encoding="utf-8",
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("Claude CLI timed out after 120s")
        except FileNotFoundError:
            raise RuntimeError("claude CLI not found in PATH")

        if result.returncode != 0:
            stderr = result.stderr[:500] if result.stderr else ""
            raise RuntimeError(f"Claude CLI error (code {result.returncode}): {stderr}")

        response = result.stdout.strip()
        if not response:
            raise RuntimeError("Claude CLI returned empty response")

        logger.debug("Claude CLI response: %d chars", len(response))
        return response

    def _parse_json(self, text: str) -> dict:
        text = text.strip()

        # Strip markdown code blocks
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Regex fallback: find outermost JSON object
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        raise json.JSONDecodeError(f"No valid JSON in response ({len(text)} chars)", text[:200], 0)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API Mode — calls Anthropic API directly
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class APIClient(LLMClient):
    """Use Anthropic API with prompt caching."""

    def __init__(
        self,
        api_key: str = "",
        model_screen: str = "",
        model_widget: str = "",
        temperature: float = 0.1,
        max_retries: int = 3,
        **_kwargs,
    ):
        import anthropic

        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set. Use LLM_MODE=cli or set the key.")

        # 2026-04-29: timeout=120 + sdk max_retries=0.
        # 이전: SDK default (600s, 내부 retry 2회). vision API + 큰 image 에서
        # SSE/connection drop 시 무한 hang — 사용자 보고된 'Vision labeling
        # 25/38 에서 멈춤' 의 root cause. 한 노드 호출이 hang 하면 전체 정지.
        # 우리 코드에 이미 max_retries 루프 있으니 SDK 내부 retry 는 끔 (중복).
        timeout_s = float(os.environ.get("LLM_TIMEOUT", "120"))
        self.client = anthropic.Anthropic(
            api_key=api_key,
            timeout=timeout_s,
            max_retries=0,
        )
        self.model_screen = model_screen or os.environ.get("LLM_MODEL_SCREEN", "claude-sonnet-4-6")
        self.model_widget = model_widget or os.environ.get("LLM_MODEL_WIDGET", "claude-haiku-4-5-20251001")
        self.temperature = temperature
        self.max_retries = max_retries
        self._anthropic = anthropic
        logger.info("LLM mode: API (model=%s)", self.model_screen)

    def query_json(self, system_prompt: str, user_prompt: str, model: str | None = None, max_tokens: int = 4096) -> dict[str, Any]:
        model = model or self.model_screen
        original_prompt = user_prompt

        for attempt in range(1, self.max_retries + 1):
            prompt = original_prompt
            if attempt > 1:
                prompt = original_prompt + "\n\n[IMPORTANT: Respond ONLY with valid JSON. No explanations.]"

            try:
                response = self._call_api(system_prompt, prompt, model, max_tokens)
                return self._parse_json(response)
            except json.JSONDecodeError as e:
                logger.warning("JSON parse failed (attempt %d): %s", attempt, e)
            except self._anthropic.AuthenticationError:
                # Mask SDK internals so the key/headers don't surface in tracebacks
                # or downstream logs. `from None` severs the cause chain.
                raise RuntimeError(
                    "LLM authentication failed (check ANTHROPIC_API_KEY)"
                ) from None
            except self._anthropic.RateLimitError as e:
                retry_after = getattr(e, "retry_after", None)
                wait = retry_after if retry_after else min(2 ** attempt, 30)
                logger.warning("Rate limited, waiting %ds", wait)
                time.sleep(wait)
            except Exception as e:
                logger.warning("API error (attempt %d): %s", attempt, e)
                time.sleep(2 ** attempt)

        raise RuntimeError(f"API query failed after {self.max_retries} attempts")

    def query_text(self, system_prompt: str, user_prompt: str, model: str | None = None, max_tokens: int = 4096) -> str:
        return self._call_api(system_prompt, user_prompt, model or self.model_screen, max_tokens)

    def query_with_image(
        self,
        system_prompt: str,
        user_prompt: str,
        image_bytes: bytes,
        image_media_type: str = "image/jpeg",
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> str:
        """Send a vision request (screenshot + text). Returns raw text response.

        System prompt is cacheable (repeated across many nodes) but the image
        bytes are NOT cached — each call pays full image input tokens.
        """
        import base64
        model = model or self.model_screen
        img_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        for attempt in range(1, self.max_retries + 1):
            try:
                message = self.client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=self.temperature,
                    system=[{"type": "text", "text": system_prompt,
                             "cache_control": {"type": "ephemeral"}}],
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image",
                             "source": {"type": "base64",
                                        "media_type": image_media_type,
                                        "data": img_b64}},
                            {"type": "text", "text": user_prompt},
                        ],
                    }],
                )
                return message.content[0].text
            except self._anthropic.AuthenticationError:
                # See H3 in query_json — same masking rationale.
                raise RuntimeError(
                    "LLM authentication failed (check ANTHROPIC_API_KEY)"
                ) from None
            except self._anthropic.RateLimitError as e:
                wait = getattr(e, "retry_after", None) or min(2 ** attempt, 30)
                logger.warning("Rate limited, waiting %ds", wait)
                time.sleep(wait)
            except Exception as e:
                logger.warning("Vision API error (attempt %d): %s", attempt, e)
                time.sleep(2 ** attempt)
        raise RuntimeError("query_with_image failed after retries")

    def _call_api(self, system: str, user: str, model: str, max_tokens: int = 4096) -> str:
        message = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=self.temperature,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        return message.content[0].text

    def _parse_json(self, text: str) -> dict:
        return parse_json_response(text)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# OpenAI-compatible Mode — 로컬 LLM 서버 (Ollama / LM Studio / llama.cpp / vLLM)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class OpenAICompatClient(LLMClient):
    """OpenAI `chat/completions` 호환 서버용 클라이언트.

    env:
      LLM_BASE_URL      예) http://192.168.0.10:11434/v1 (Ollama), http://host:1234/v1 (LM Studio)
      LLM_MODEL_SCREEN  텍스트 모델 (예: qwen2.5:7b-instruct)
      LLM_MODEL_VISION  비전 모델 (예: qwen2.5vl:7b). 없으면 LLM_MODEL_SCREEN 사용
      LLM_API_KEY       서버가 요구할 때만 (기본 "local")
      LLM_TIMEOUT       초 (로컬 모델은 느리므로 기본 600)
      LLM_JSON_MODE=1   response_format=json_object 전송 (지원 서버에서 JSON 안정성 ↑)

    APIClient 와 같은 인터페이스: query_json / query_text / query_with_image.
    """

    def __init__(
        self,
        api_key: str = "",
        model_screen: str = "",
        model_widget: str = "",
        temperature: float = 0.1,
        max_retries: int = 3,
        base_url: str = "",
        model_vision: str = "",
        timeout_s: float | None = None,
        **_kwargs,
    ):
        import httpx

        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", "")).rstrip("/")
        if not self.base_url:
            raise RuntimeError("LLM_BASE_URL not set (e.g. http://192.168.0.10:11434/v1)")
        self.model_screen = model_screen or os.environ.get("LLM_MODEL_SCREEN", "")
        if not self.model_screen:
            raise RuntimeError("LLM_MODEL_SCREEN not set (e.g. qwen2.5:7b-instruct)")
        self.model_widget = model_widget or os.environ.get("LLM_MODEL_WIDGET", "") or self.model_screen
        self.model_vision = model_vision or os.environ.get("LLM_MODEL_VISION", "") or self.model_screen
        self.temperature = temperature
        self.max_retries = max_retries
        self.json_mode = os.environ.get("LLM_JSON_MODE", "").lower() in ("1", "true", "yes")
        timeout = timeout_s if timeout_s is not None else float(os.environ.get("LLM_TIMEOUT", "600"))
        key = os.environ.get("LLM_API_KEY", "") or "local"
        self._httpx = httpx
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(timeout, connect=10.0),
        )
        # 2026-09-12: Ollama 는 네이티브 /api/chat 로 호출한다. 이유:
        #   - Qwen3.5 / Gemma 4 같은 thinking 모델은 /v1 경로에서 think:false 가 무시돼
        #     추론 토큰만 쓰고 content 가 비는 문제가 보고됨 (ollama#14809 등).
        #   - /api/chat 은 think:false, format:"json", images:[b64] 를 정식 지원.
        # 감지: base_url 의 /v1 을 뗀 루트에 /api/tags 가 200 이면 Ollama. LLM_OLLAMA_NATIVE=0 로 끌 수 있음.
        self.ollama_root: str | None = None
        native = os.environ.get("LLM_OLLAMA_NATIVE", "auto").lower()
        if native not in ("0", "false", "no", "off"):
            root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
            try:
                r = httpx.get(f"{root}/api/tags", timeout=3.0)
                if r.status_code == 200 and "models" in r.text:
                    self.ollama_root = root
            except Exception:
                pass
        logger.info("LLM mode: OpenAI-compatible (%s, text=%s, vision=%s, ollama_native=%s)",
                    self.base_url, self.model_screen, self.model_vision, bool(self.ollama_root))

    # ── 공용 ──

    _THINK_RE = re.compile(r"<think>[\s\S]*?</think>\s*", re.IGNORECASE)

    # 모델 계열별 샘플링 프리셋 (벤더 권장값, 2026-09). temperature 는 분류/추출 작업이라 낮게 고정하고
    # top_p/top_k/repeat 계열만 권장값을 따른다. 매칭 안 되면 Ollama 기본값.
    _SAMPLING_PRESETS: tuple[tuple[str, dict[str, Any]], ...] = (
        ("qwen3", {"top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5}),   # Qwen3/3.5 non-thinking
        ("gemma", {"top_p": 0.95, "top_k": 64, "repeat_penalty": 1.0}),                    # Gemma 3/4
        ("qwen2", {"top_p": 0.8, "top_k": 20, "repeat_penalty": 1.05}),                    # Qwen2.5 (+VL)
        ("llama", {"top_p": 0.9, "repeat_penalty": 1.1}),
        ("mistral", {"top_p": 0.9}),
    )

    def _sampling_options(self, model: str, max_tokens: int) -> dict[str, Any]:
        opts: dict[str, Any] = {"temperature": self.temperature, "num_predict": max_tokens}
        m = model.lower()
        for prefix, preset in self._SAMPLING_PRESETS:
            if m.startswith(prefix):
                opts.update(preset)
                break
        return opts

    def _chat_ollama(self, messages: list[dict], model: str, max_tokens: int, json_mode: bool) -> str:
        """Ollama /api/chat — OpenAI 형식 messages 를 네이티브 형식으로 변환."""
        native_msgs: list[dict] = []
        for m in messages:
            content = m.get("content")
            if isinstance(content, list):   # 비전: text parts + image_url(data:...;base64,xxx)
                text = "".join(part.get("text", "") for part in content if part.get("type") == "text")
                images = []
                for part in content:
                    if part.get("type") == "image_url":
                        url = (part.get("image_url") or {}).get("url", "")
                        if "base64," in url:
                            images.append(url.split("base64,", 1)[1])
                msg = {"role": m["role"], "content": text}
                if images:
                    msg["images"] = images
                native_msgs.append(msg)
            else:
                native_msgs.append({"role": m["role"], "content": content or ""})
        body: dict[str, Any] = {
            "model": model,
            "messages": native_msgs,
            "stream": False,
            "think": False,
            "options": self._sampling_options(model, max_tokens),
        }
        if json_mode:
            body["format"] = "json"
        r = self._httpx.post(f"{self.ollama_root}/api/chat", json=body,
                             timeout=self.client.timeout)
        if r.status_code == 404:
            raise RuntimeError(f"Ollama: model not found ({model}) — ollama pull {model}")
        if r.status_code >= 400:
            raise RuntimeError(f"Ollama {r.status_code}: {r.text[:200]}")
        data = r.json()
        content = (data.get("message") or {}).get("content") or ""
        return self._THINK_RE.sub("", content).strip()

    def _chat(self, messages: list[dict], model: str, max_tokens: int, json_mode: bool = False) -> str:
        if self.ollama_root:
            return self._chat_ollama(messages, model, max_tokens, json_mode)
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode and self.json_mode:
            body["response_format"] = {"type": "json_object"}
        r = self.client.post("/chat/completions", json=body)
        if r.status_code == 401:
            raise RuntimeError("LLM authentication failed (check LLM_API_KEY)")
        if r.status_code == 429:
            raise _RateLimited(r.headers.get("retry-after"))
        if r.status_code >= 400:
            raise RuntimeError(f"LLM server {r.status_code}: {r.text[:200]}")
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"LLM server returned no choices: {str(data)[:200]}")
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):   # 일부 서버는 content parts 배열
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        # thinking 모델이 content 안에 <think>…</think> 를 섞어 보내는 서버 대응
        return self._THINK_RE.sub("", content or "").strip()

    def _with_retries(self, fn, what: str) -> str:
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return fn(attempt)
            except _RateLimited as e:
                wait = e.retry_after or min(2 ** attempt, 30)
                logger.warning("[%s] rate limited, waiting %ss", what, wait)
                time.sleep(wait)
                last = e
            except RuntimeError as e:
                if "authentication" in str(e):
                    raise
                last = e
                logger.warning("[%s] error (attempt %d): %s", what, attempt, e)
                time.sleep(min(2 ** attempt, 15))
            except Exception as e:  # 연결 실패 / 타임아웃
                last = e
                logger.warning("[%s] error (attempt %d): %s", what, attempt, e)
                time.sleep(min(2 ** attempt, 15))
        raise RuntimeError(f"{what} failed after {self.max_retries} attempts: {last}")

    # ── 인터페이스 ──

    def query_json(self, system_prompt: str, user_prompt: str, model: str | None = None,
                   max_tokens: int = 4096) -> dict[str, Any]:
        model = model or self.model_screen

        def _once(attempt: int) -> str:
            prompt = user_prompt
            if attempt > 1:
                prompt += "\n\n[IMPORTANT: Respond ONLY with valid JSON. No explanations.]"
            text = self._chat(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
                model, max_tokens, json_mode=True,
            )
            try:
                return json.dumps(parse_json_response(text))
            except json.JSONDecodeError as e:
                raise RuntimeError(f"JSON parse failed: {e}") from e

        return json.loads(self._with_retries(_once, "query_json"))

    def query_text(self, system_prompt: str, user_prompt: str, model: str | None = None,
                   max_tokens: int = 4096) -> str:
        model = model or self.model_screen
        return self._with_retries(
            lambda _a: self._chat(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                model, max_tokens),
            "query_text",
        )

    def query_with_image(self, system_prompt: str, user_prompt: str, image_bytes: bytes,
                         image_media_type: str = "image/jpeg", model: str | None = None,
                         max_tokens: int = 2048) -> str:
        import base64
        model = model or self.model_vision
        b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": f"data:{image_media_type};base64,{b64}"}},
            ]},
        ]
        return self._with_retries(lambda _a: self._chat(messages, model, max_tokens), "query_with_image")

    # ── 진단 (대시보드 설정 화면의 '연결 테스트') ──

    def list_models(self) -> list[str]:
        if self.ollama_root:
            r = self._httpx.get(f"{self.ollama_root}/api/tags", timeout=5.0)
            if r.status_code == 200:
                return [m.get("name", "") for m in (r.json().get("models") or []) if m.get("name")]
        r = self.client.get("/models")
        if r.status_code >= 400:
            raise RuntimeError(f"GET /models -> {r.status_code}: {r.text[:120]}")
        data = r.json().get("data") or []
        return [m.get("id", "") for m in data if isinstance(m, dict)]

    def test_connection(self, with_vision: bool = False) -> dict:
        """모델 목록 / JSON 응답 / (선택) 비전 응답을 점검해 결과 dict 반환."""
        out: dict[str, Any] = {"ok": False, "base_url": self.base_url, "model": self.model_screen,
                               "vision_model": self.model_vision, "ollama_native": bool(self.ollama_root)}
        try:
            out["models"] = self.list_models()[:50]
        except Exception as e:  # /models 미지원 서버도 있음 — 치명적 아님
            out["models_error"] = str(e)[:160]
        t0 = time.time()
        try:
            # 2026-09-13: "<your model name>" 을 채우라는 프롬프트는 Gemma 4 가 format:json 아래서
            # 문자열을 열어 둔 채 멈춰(EOS) 파싱 실패했다 → 모델이 답을 지어낼 필요 없는 고정 echo 로.
            resp = self.query_json("You reply with strict JSON only.",
                                   'Reply exactly: {"ok": true, "echo": "pong"}',
                                   max_tokens=64)
            out["json_ok"] = bool(resp.get("ok") is True or "ok" in resp)
            out["latency_ms"] = int((time.time() - t0) * 1000)
            out["sample"] = resp
            out["ok"] = out["json_ok"]
        except Exception as e:
            out["error"] = str(e)[:300]
            return out
        if with_vision:
            try:
                from io import BytesIO
                from PIL import Image
                buf = BytesIO()
                img = Image.new("RGB", (64, 64), (255, 77, 28))
                img.save(buf, format="JPEG")
                t1 = time.time()
                txt = self.query_with_image("Answer in one short word.",
                                            "What is the dominant color of this image?",
                                            buf.getvalue(), max_tokens=16)
                out["vision_ok"] = bool(txt.strip())
                out["vision_latency_ms"] = int((time.time() - t1) * 1000)
                out["vision_sample"] = txt.strip()[:60]
            except Exception as e:
                out["vision_ok"] = False
                out["vision_error"] = str(e)[:200]
        return out


class _RateLimited(Exception):
    def __init__(self, retry_after: str | None):
        super().__init__("rate limited")
        try:
            self.retry_after = float(retry_after) if retry_after else None
        except ValueError:
            self.retry_after = None
