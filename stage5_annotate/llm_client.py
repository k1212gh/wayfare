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


def create_client(**kwargs) -> "LLMClient":
    """Factory: create the right client based on LLM_MODE env var.

    Default: API mode (recommended for production).
    CLI mode is deprecated — use only for local testing without API key.
    """
    mode = os.environ.get("LLM_MODE", "api").lower()
    if mode == "cli":
        logger.info("Using CLI mode (deprecated — switch to API when key available)")
        return CLIClient(**kwargs)
    return APIClient(**kwargs)


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
        text = text.strip()
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

        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        raise json.JSONDecodeError(f"No valid JSON ({len(text)} chars)", text[:200], 0)
