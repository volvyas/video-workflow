"""Provider interfaces. Stages depend only on these classes."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar, TypeVar

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from shorts.config import AppSettings
    from shorts.models import VideoMeta, WordTiming

T = TypeVar("T", bound=BaseModel)


class NoSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ProviderSettings(BaseModel):
    """Base for provider settings models. Unknown keys under the prefix are an error (catches typos)."""

    model_config = ConfigDict(extra="forbid")


class ProviderError(RuntimeError):
    pass


class Provider(ABC):
    kind: ClassVar[str]
    name: ClassVar[str]
    env_prefix: ClassVar[str] = ""
    Settings: ClassVar[type[BaseModel]] = NoSettings
    # Shown as a warning by `shorts check` when the default model can't be used commercially.
    license_note: ClassVar[str | None] = None
    # True for hosted APIs (no local GPU/RAM), so the pipeline can skip model swapping around it.
    remote: ClassVar[bool] = False

    def __init__(self, settings: BaseModel, app: "AppSettings"):
        self.settings = settings
        self.app = app
        self.log = logging.getLogger(f"shorts.{self.kind}.{self.name}")

    def unload(self) -> None:
        """Free GPU memory / close connections. Called between pipeline stages."""

    def describe(self) -> str:
        return self.name

    def verify(self) -> None:
        """Cheap readiness check for `shorts check` (dependencies installed, files present). Raise on problems."""

    @staticmethod
    def _require_module(module: str, extra: str) -> None:
        import importlib.util

        if importlib.util.find_spec(module) is None:
            raise ProviderError(f"python package '{module}' not installed: run `uv sync --extra {extra}`")


class LLMProvider(Provider):
    kind = "llm"
    supports_vision: ClassVar[bool] = False

    @abstractmethod
    def generate_json(self, *, system: str, prompt: str, schema: type[T], images: list[Path] | None = None) -> T:
        """Return an instance of `schema` produced by the model. `images` requires a vision-capable model."""

    @property
    def supports_web_search(self) -> bool:
        return False

    def research(self, *, system: str, prompt: str) -> str:
        """Free-text answer grounded in a live web search. Only when supports_web_search is True."""
        raise NotImplementedError(f"{self.describe()} has no web search")


class TTSProvider(Provider):
    kind = "tts"

    @abstractmethod
    def synthesize(self, text: str, out_path: Path) -> float:
        """Write a WAV to out_path and return its duration in seconds."""


class Aligner(Provider):
    kind = "align"

    @abstractmethod
    def align(self, audio: Path, text: str) -> list["WordTiming"]:
        """Return word timings (seconds, relative to the start of `audio`)."""


class ImageProvider(Provider):
    kind = "image"

    @abstractmethod
    def generate(
        self, *, prompt: str, negative: str, width: int, height: int, seed: int, out_path: Path
    ) -> Path: ...


class MotionProvider(Provider):
    kind = "motion"

    @abstractmethod
    def animate(
        self,
        *,
        image: Path,
        prompt: str,
        camera: str,
        duration: float,
        seed: int,
        out_path: Path,
    ) -> Path:
        """Turn a still into a video clip of roughly `duration` seconds."""


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    text: str = ""  # the page's relevant text, when the provider has it (then the page isn't fetched)


class SearchProvider(Provider):
    kind = "search"
    # Set by Context: create another search provider by name (used by the combined `multi` provider).
    provider_factory: "Callable[[str], SearchProvider] | None" = None

    @abstractmethod
    def search(self, query: str, max_results: int) -> list[SearchResult]: ...


class MusicProvider(Provider):
    """Background music for one video."""

    kind = "music"

    @abstractmethod
    def generate(self, *, tags: str, bpm: int, key: str, seconds: float, seed: int, out_path: Path) -> Path | None:
        """Write a track of at least `seconds` (any audio format; the real path is returned) or None for no music."""


class SimilarityProvider(Provider):
    """Scores how similar a text is to each of several earlier texts (0 = unrelated, 1 = same story)."""

    kind = "similarity"
    # Set by Context: returns the LLM provider for LLM-based similarity (SIMILARITY_LLM_PROVIDER / LLM_PROVIDER).
    llm_factory: "Callable[[], LLMProvider] | None" = None

    @property
    def threshold(self) -> float:
        """Scores at or above this count as "similar". Each provider exposes it as a THRESHOLD setting."""
        return float(getattr(self.settings, "threshold", 0.5))

    @abstractmethod
    def scores(self, query: str, docs: list[str]) -> list[float]:
        """One score in [0, 1] per doc."""


class Uploader(Provider):
    kind = "upload"

    @abstractmethod
    def upload(self, video: Path, meta: "VideoMeta") -> str:
        """Upload and return a URL or ID."""


KIND_BASES: dict[str, type[Provider]] = {
    "llm": LLMProvider,
    "tts": TTSProvider,
    "align": Aligner,
    "image": ImageProvider,
    "motion": MotionProvider,
    "upload": Uploader,
    "search": SearchProvider,
    "similarity": SimilarityProvider,
    "music": MusicProvider,
}
