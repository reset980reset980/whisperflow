"""AI chat backend for the Linux port.

Replaces the original Claude Code CLI subprocess with a streaming HTTP
API call (OpenAI-compatible by default, Anthropic optional). Kept
dependency-light: uses stdlib ``urllib`` + SSE parsing so the AI path
works even without the ``openai`` SDK installed.

Public API:
    provider = get_provider()
    for delta in provider.stream(model, messages, system):
        ...   # delta is an incremental text chunk (str)

``messages`` is a list of {"role": "user"|"assistant", "content": str}.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Generator, Iterable

# Alias → concrete model, resolved per provider. The UI sends
# haiku/sonnet/opus; map them to sensible tiers for each backend.
_OPENAI_ALIASES = {
    "haiku": "gpt-4o-mini",
    "sonnet": "gpt-4o",
    "opus": "gpt-4o",
}
_ANTHROPIC_ALIASES = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-20250514",
    "opus": "claude-opus-4-20250514",
}

DEFAULT_SYSTEM_PROMPT = (
    "너는 개인 음성 비서 'Jarvis'다. 한국어로 간결하고 자연스럽게 대답한다. "
    "음성으로 읽히는 답변이므로 마크다운 기호나 코드 블록 없이, "
    "짧고 명확한 문장으로 말하듯이 응답한다."
)


class AIError(Exception):
    pass


# ----------------------------------------------------------------------
# Provider base
# ----------------------------------------------------------------------

class BaseProvider:
    name = "base"

    def resolve_model(self, alias: str) -> str:
        return alias

    def stream(
        self, model: str, messages: list[dict], system: str
    ) -> Generator[str, None, None]:
        raise NotImplementedError


# ----------------------------------------------------------------------
# Echo provider (no API key configured) — keeps the pipeline testable
# ----------------------------------------------------------------------

class EchoProvider(BaseProvider):
    name = "echo"

    def resolve_model(self, alias: str) -> str:
        return "echo"

    def stream(self, model, messages, system):
        last = messages[-1]["content"] if messages else ""
        reply = f"(에코 모드 · API 키 미설정) 방금 이렇게 말씀하셨네요: {last}"
        for word in reply.split(" "):
            yield word + " "


# ----------------------------------------------------------------------
# OpenAI-compatible provider (OpenAI, OpenRouter, ...)
# ----------------------------------------------------------------------

class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self, api_key: str, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")

    def resolve_model(self, alias: str) -> str:
        if alias in _OPENAI_ALIASES:
            return _OPENAI_ALIASES[alias]
        # Non-alias (already a concrete model id) → use as-is
        return alias

    def stream(self, model, messages, system):
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}] + messages,
            "stream": True,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        yield from _stream_sse_openai(req)


def _stream_sse_openai(req) -> Generator[str, None, None]:
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                    delta = obj["choices"][0].get("delta", {}).get("content")
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise AIError(f"OpenAI HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise AIError(f"OpenAI connection error: {e.reason}") from e


# ----------------------------------------------------------------------
# Anthropic provider
# ----------------------------------------------------------------------

class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def resolve_model(self, alias: str) -> str:
        return _ANTHROPIC_ALIASES.get(alias, alias)

    def stream(self, model, messages, system):
        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": model,
            "max_tokens": 1024,
            "system": system,
            "messages": messages,
            "stream": True,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") == "content_block_delta":
                        text = obj.get("delta", {}).get("text")
                        if text:
                            yield text
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            raise AIError(f"Anthropic HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise AIError(f"Anthropic connection error: {e.reason}") from e


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------

_cached_provider: BaseProvider | None = None


def get_provider(force_reload: bool = False) -> BaseProvider:
    """Return the configured provider, honoring env vars.

    AI_PROVIDER=openai|anthropic (default openai). Falls back to
    EchoProvider when the corresponding API key is missing.
    """
    global _cached_provider
    if _cached_provider is not None and not force_reload:
        return _cached_provider

    which = os.environ.get("AI_PROVIDER", "openai").strip().lower()

    if which == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if key:
            _cached_provider = AnthropicProvider(key)
            return _cached_provider

    # default: openai
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        _cached_provider = OpenAIProvider(
            key, os.environ.get("OPENAI_BASE_URL", "").strip() or None
        )
        return _cached_provider

    _cached_provider = EchoProvider()
    return _cached_provider


def default_model_alias() -> str:
    """Concrete default model for the active provider, if the user set one."""
    which = os.environ.get("AI_PROVIDER", "openai").strip().lower()
    if which == "anthropic":
        return os.environ.get("ANTHROPIC_MODEL", "").strip() or "haiku"
    return os.environ.get("OPENAI_MODEL", "").strip() or "haiku"
