"""Claude via the official Anthropic SDK, using structured JSON outputs."""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from shorts.providers.base import LLMProvider, ProviderError, ProviderSettings, T
from shorts.providers.llm._images import encode_jpeg_b64
from shorts.providers.llm._schema import strict_schema

# Models that accept the server-side refusal fallback parameter.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-fable-5-2", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeSettings(ProviderSettings):
    model: str = "claude-opus-5-5"
    effort: str = "medium"  # low | medium | high | xhigh | max | none (omit, for models without effort)
    max_tokens: int = 16000
    fallbacks: str = "auto"  # auto | default | off
    api_key: SecretStr | None = None  # falls back to ANTHROPIC_API_KEY / `ant auth login` profile
    timeout: float = 600
    max_retries: int = 3
    web_search: bool = True  # allow the fact-check stage to use Anthropic's server-side web search
    web_search_max_uses: int = 8


class ClaudeLLM(LLMProvider):
    name = "claude"
    env_prefix = "LLM_CLAUDE_"
    Settings = ClaudeSettings
    settings: ClaudeSettings
    supports_vision = True
    remote = True

    def __init__(self, settings, app):
        super().__init__(settings, app)
        import anthropic

        key = self.settings.api_key.get_secret_value() if self.settings.api_key else None
        self._client = anthropic.Anthropic(
            api_key=key, timeout=self.settings.timeout, max_retries=self.settings.max_retries
        )

    def describe(self) -> str:
        return f"claude:{self.settings.model}"

    def _use_fallbacks(self) -> bool:
        mode = self.settings.fallbacks
        return mode == "default" or (mode == "auto" and self.settings.model in _FALLBACK_MODELS)

    @property
    def supports_web_search(self) -> bool:
        return self.settings.web_search

    def _create(self, **kwargs):
        kwargs.setdefault("model", self.settings.model)
        kwargs.setdefault("max_tokens", self.settings.max_tokens)
        output_config = kwargs.pop("output_config", {})
        if self.settings.effort != "none":
            output_config["effort"] = self.settings.effort
        if output_config:
            kwargs["output_config"] = output_config
        if self._use_fallbacks():
            resp = self._client.beta.messages.create(betas=[_FALLBACK_BETA], fallbacks="default", **kwargs)
        else:
            resp = self._client.messages.create(**kwargs)
        u = resp.usage
        self.log.info(
            "claude %s: in=%s out=%s cache_read=%s stop=%s request_id=%s",
            resp.model, u.input_tokens, u.output_tokens, getattr(u, "cache_read_input_tokens", None),
            resp.stop_reason, resp._request_id,
        )
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            raise ProviderError(f"Claude declined the request: {getattr(details, 'explanation', None) or 'no details'}")
        if resp.stop_reason == "max_tokens":
            raise ProviderError(f"Claude hit max_tokens={kwargs['max_tokens']}; raise LLM_CLAUDE_MAX_TOKENS")
        return resp

    def generate_json(self, *, system: str, prompt: str, schema: type[T], images: list[Path] | None = None) -> T:
        content: list[dict] = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": encode_jpeg_b64(p)}}
            for p in images or []
        ]
        content.append({"type": "text", "text": prompt})
        resp = self._create(
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": strict_schema(schema)}},
        )
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if text is None:
            raise ProviderError(f"no text block in Claude response (stop_reason={resp.stop_reason})")
        return schema.model_validate_json(text)

    def research(self, *, system: str, prompt: str) -> str:
        """Answer with live web search (server-side tool). Resumes `pause_turn` until the answer is complete."""
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": self.settings.web_search_max_uses}]
        assistant: list = []
        for _ in range(6):
            messages: list[dict] = [{"role": "user", "content": prompt}]
            if assistant:
                messages.append({"role": "assistant", "content": assistant})
            resp = self._create(system=system, messages=messages, tools=tools)
            assistant = assistant + list(resp.content)
            if resp.stop_reason != "pause_turn":
                break
        else:
            raise ProviderError("web research did not finish after 6 continuations")
        return "\n".join(b.text for b in assistant if getattr(b, "type", None) == "text").strip()
