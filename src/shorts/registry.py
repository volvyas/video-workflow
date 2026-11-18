"""Provider registry.

Built-in providers are referenced by import path strings so heavy optional dependencies
(torch, anthropic, faster-whisper…) are only imported when that provider is selected.

A provider name containing ':' is treated as an external class: `mypkg.my_module:MyProvider`.
"""

from __future__ import annotations

import importlib

from pydantic import ValidationError

from shorts.config import ConfigError, Env
from shorts.providers.base import KIND_BASES, Provider

BUILTINS: dict[str, dict[str, str]] = {
    "llm": {
        "claude": "shorts.providers.llm.claude:ClaudeLLM",
        "ollama": "shorts.providers.llm.ollama:OllamaLLM",
        "openai_compat": "shorts.providers.llm.openai_compat:OpenAICompatLLM",
        "mock": "shorts.providers.llm.mock:MockLLM",
    },
    "tts": {
        "f5": "shorts.providers.tts.f5:F5TTSProvider",
        "comfyui": "shorts.providers.tts.comfyui:ComfyTTS",
        "kokoro": "shorts.providers.tts.kokoro:KokoroTTS",
        "mock": "shorts.providers.tts.mock:MockTTS",
    },
    "align": {
        "faster_whisper": "shorts.providers.align.faster_whisper:FasterWhisperAligner",
        "proportional": "shorts.providers.align.proportional:ProportionalAligner",
    },
    "image": {
        "comfyui": "shorts.providers.image.comfyui:ComfyImage",
        "mock": "shorts.providers.image.mock:MockImage",
    },
    "motion": {
        "kenburns": "shorts.providers.motion.kenburns:KenBurns",
        "comfyui_i2v": "shorts.providers.motion.comfyui_i2v:ComfyI2V",
        "mock": "shorts.providers.motion.mock:MockMotion",
    },
    "search": {
        "ddgs": "shorts.providers.search.ddgs:DDGSSearch",
        "searxng": "shorts.providers.search.searxng:SearXNGSearch",
        "wikipedia": "shorts.providers.search.wikipedia:WikipediaSearch",
        "multi": "shorts.providers.search.multi:MultiSearch",
        "mock": "shorts.providers.search.mock:MockSearch",
    },
    "similarity": {
        "lexical": "shorts.providers.similarity.lexical:LexicalSimilarity",
        "embeddings": "shorts.providers.similarity.embeddings:EmbeddingsSimilarity",
        "llm": "shorts.providers.similarity.llm:LLMSimilarity",
    },
    "music": {
        "comfyui": "shorts.providers.music.comfyui:ComfyMusic",
        "folder": "shorts.providers.music.folder:FolderMusic",
        "none": "shorts.providers.music.folder:NoMusic",
    },
    "upload": {
        "youtube": "shorts.providers.upload.youtube:YouTubeUploader",
        "none": "shorts.providers.upload.none:NoUpload",
    },
}


def _import(target: str) -> type:
    module_name, _, attr = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise ConfigError(
            f"cannot import provider module '{module_name}': {e}. "
            "Is the optional dependency installed? See docs/SETUP.md."
        ) from e
    try:
        return getattr(module, attr)
    except AttributeError as e:
        raise ConfigError(f"'{module_name}' has no attribute '{attr}'") from e


def resolve(kind: str, name: str) -> type[Provider]:
    if kind not in KIND_BASES:
        raise ConfigError(f"unknown provider kind '{kind}'")
    target = BUILTINS[kind].get(name)
    if target is None:
        if ":" not in name:
            known = ", ".join(sorted(BUILTINS[kind]))
            raise ConfigError(f"unknown {kind} provider '{name}'. Built-in: {known}, or use 'module:Class'.")
        target = name
    cls = _import(target)
    if not (isinstance(cls, type) and issubclass(cls, KIND_BASES[kind])):
        raise ConfigError(f"{target} is not a {KIND_BASES[kind].__name__}")
    return cls


def create(kind: str, name: str, env: Env) -> Provider:
    cls = resolve(kind, name)
    raw = env.section(cls.env_prefix) if cls.env_prefix else {}
    try:
        settings = cls.Settings.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"invalid settings for {kind} provider '{name}' (env prefix {cls.env_prefix}):\n{e}") from e
    return cls(settings, env.app)
