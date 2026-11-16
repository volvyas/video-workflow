"""Local LLM via Ollama's native API (structured outputs through the `format` JSON schema)."""

from __future__ import annotations

import httpx
from pydantic import ValidationError

from shorts.providers.base import LLMProvider, ProviderError, ProviderSettings, T
from shorts.providers.llm._schema import extract_json, schema_instructions, strict_schema


class OllamaSettings(ProviderSettings):
    url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:14b"
    temperature: float = 0.8
    num_ctx: int = 8192
    keep_alive: str = "5m"
    think: bool | None = False  # disable reasoning traces on thinking models (qwen3, deepseek-r1)
    timeout: float = 600
    retries: int = 2  # re-ask when the output fails validation


class OllamaLLM(LLMProvider):
    name = "ollama"
    env_prefix = "LLM_OLLAMA_"
    Settings = OllamaSettings
    settings: OllamaSettings

    def __init__(self, settings, app):
        super().__init__(settings, app)
        self._http = httpx.Client(base_url=self.settings.url, timeout=self.settings.timeout)

    def describe(self) -> str:
        return f"ollama:{self.settings.model}"

    def generate_json(self, *, system: str, prompt: str, schema: type[T]) -> T:
        body = {
            "model": self.settings.model,
            "stream": False,
            "format": strict_schema(schema),
            "keep_alive": self.settings.keep_alive,
            "options": {"temperature": self.settings.temperature, "num_ctx": self.settings.num_ctx},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"{prompt}\n\n{schema_instructions(schema)}"},
            ],
        }
        if self.settings.think is not None:
            body["think"] = self.settings.think
        last_err: Exception | None = None
        for attempt in range(self.settings.retries + 1):
            try:
                r = self._http.post("/api/chat", json=body)
                r.raise_for_status()
                data = r.json()
                self.log.info("ollama %s: in=%s out=%s", self.settings.model,
                              data.get("prompt_eval_count"), data.get("eval_count"))
                return schema.model_validate(extract_json(data["message"]["content"]))
            except (ValidationError, ValueError) as e:
                last_err = e
                self.log.warning("invalid JSON from ollama (attempt %d): %s", attempt + 1, e)
            except httpx.HTTPError as e:
                raise ProviderError(f"ollama request failed: {e}") from e
        raise ProviderError(f"ollama returned invalid output after retries: {last_err}")

    def unload(self) -> None:
        try:
            self._http.post("/api/generate", json={"model": self.settings.model, "keep_alive": 0})
        except httpx.HTTPError as e:
            self.log.debug("ollama unload failed: %s", e)
