"""Any OpenAI-compatible chat completions server: LM Studio, llama.cpp server, vLLM, KoboldCpp…"""

from __future__ import annotations

from pathlib import Path

import httpx
from pydantic import SecretStr, ValidationError

from shorts.providers.base import LLMProvider, ProviderError, ProviderSettings, T
from shorts.providers.llm._images import encode_jpeg_b64
from shorts.providers.llm._schema import extract_json, schema_instructions, strict_schema


class OpenAICompatSettings(ProviderSettings):
    base_url: str = "http://127.0.0.1:1234/v1"  # LM Studio default; llama.cpp: :8080/v1, vLLM: :8000/v1
    model: str = "local-model"
    api_key: SecretStr | None = None
    temperature: float = 0.8
    max_tokens: int = 16000
    response_format: str = "json_schema"  # json_schema | json_object | none
    timeout: float = 900
    retries: int = 2
    extra_body: str = ""  # JSON merged into each request, e.g. {"chat_template_kwargs": {"enable_thinking": false}}
    unload_url: str = ""  # called by the pipeline to free RAM/VRAM, e.g. llama-swap: http://host:8080/api/models/unload
    unload_method: str = "POST"


class OpenAICompatLLM(LLMProvider):
    name = "openai_compat"
    env_prefix = "LLM_OPENAI_"
    Settings = OpenAICompatSettings
    settings: OpenAICompatSettings
    supports_vision = True  # if the served model is a vision model

    def __init__(self, settings, app):
        super().__init__(settings, app)
        headers = {}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key.get_secret_value()}"
        self._http = httpx.Client(base_url=self.settings.base_url.rstrip("/"), headers=headers,
                                  timeout=self.settings.timeout)

    def describe(self) -> str:
        return f"openai_compat:{self.settings.model}"

    def generate_json(self, *, system: str, prompt: str, schema: type[T], images: list[Path] | None = None) -> T:
        text = f"{prompt}\n\n{schema_instructions(schema)}"
        user_content: str | list = text
        if images:
            user_content = [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encode_jpeg_b64(p)}"}}
                            for p in images] + [{"type": "text", "text": text}]
        body: dict = {
            "model": self.settings.model,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        }
        if self.settings.extra_body:
            import json as _json

            body.update(_json.loads(self.settings.extra_body))
        mode = self.settings.response_format
        if mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "strict": True, "schema": strict_schema(schema)},
            }
        elif mode == "json_object":
            body["response_format"] = {"type": "json_object"}

        last_err: Exception | None = None
        for attempt in range(self.settings.retries + 1):
            try:
                r = self._http.post("/chat/completions", json=body)
                r.raise_for_status()
                data = r.json()
                usage = data.get("usage") or {}
                self.log.info("openai_compat %s: in=%s out=%s", self.settings.model,
                              usage.get("prompt_tokens"), usage.get("completion_tokens"))
                return schema.model_validate(extract_json(data["choices"][0]["message"]["content"]))
            except (ValidationError, ValueError, KeyError) as e:
                last_err = e
                self.log.warning("invalid output (attempt %d): %s", attempt + 1, e)
            except httpx.HTTPError as e:
                raise ProviderError(f"request to {self.settings.base_url} failed: {e}") from e
        raise ProviderError(f"invalid output after retries: {last_err}")

    def unload(self) -> None:
        if not self.settings.unload_url:
            return
        try:
            self._http.request(self.settings.unload_method.upper(), self.settings.unload_url, timeout=60)
            self.log.info("unloaded model via %s", self.settings.unload_url)
        except httpx.HTTPError as e:
            self.log.warning("unload request to %s failed: %s", self.settings.unload_url, e)
