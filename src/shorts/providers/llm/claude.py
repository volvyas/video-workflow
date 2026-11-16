"""Claude via the official Anthropic SDK, using structured JSON outputs."""

from __future__ import annotations

from pydantic import SecretStr

from shorts.providers.base import LLMProvider, ProviderError, ProviderSettings, T
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


class ClaudeLLM(LLMProvider):
    name = "claude"
    env_prefix = "LLM_CLAUDE_"
    Settings = ClaudeSettings
    settings: ClaudeSettings

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

    def generate_json(self, *, system: str, prompt: str, schema: type[T]) -> T:
        output_config: dict = {"format": {"type": "json_schema", "schema": strict_schema(schema)}}
        if self.settings.effort != "none":
            output_config["effort"] = self.settings.effort
        kwargs = dict(
            model=self.settings.model,
            max_tokens=self.settings.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config=output_config,
        )
        if self._use_fallbacks():
            resp = self._client.beta.messages.create(betas=[_FALLBACK_BETA], fallbacks="default", **kwargs)
        else:
            resp = self._client.messages.create(**kwargs)

        u = resp.usage
        self.log.info(
            "claude %s: in=%s out=%s cache_read=%s request_id=%s",
            resp.model, u.input_tokens, u.output_tokens, getattr(u, "cache_read_input_tokens", None), resp._request_id,
        )
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            raise ProviderError(f"Claude declined the request: {getattr(details, 'explanation', None) or 'no details'}")
        if resp.stop_reason == "max_tokens":
            raise ProviderError(f"Claude hit max_tokens={self.settings.max_tokens}; raise LLM_CLAUDE_MAX_TOKENS")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if text is None:
            raise ProviderError(f"no text block in Claude response (stop_reason={resp.stop_reason})")
        return schema.model_validate_json(text)
