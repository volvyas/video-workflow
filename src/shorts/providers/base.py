"""Provider interfaces. Stages depend only on these classes."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
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
}
