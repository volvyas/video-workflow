"""Runtime context: settings + lazily created, cached providers."""

from __future__ import annotations

import logging
import random

from shorts.config import Env
from shorts.media import ffmpeg
from shorts.providers.base import (
    Aligner, ImageProvider, LLMProvider, MotionProvider, MusicProvider, Provider, SearchProvider, SimilarityProvider, TTSProvider,
    Uploader,
)
from shorts.registry import create

log = logging.getLogger("shorts")


class Context:
    def __init__(self, env: Env):
        self.env = env
        self.app = env.app
        self._providers: dict[tuple[str, str], Provider] = {}
        ffmpeg.configure(self.app.ffmpeg_path, self.app.ffprobe_path)

    def provider(self, kind: str, name: str) -> Provider:
        key = (kind, name)
        if key not in self._providers:
            p = create(kind, name, self.env)
            log.info("using %s provider: %s", kind, p.describe())
            self._providers[key] = p
        return self._providers[key]

    # typed shortcuts
    def llm(self, stage: str) -> LLMProvider:
        return self.provider("llm", self.app.llm_for(stage))  # type: ignore[return-value]

    def tts(self) -> TTSProvider:
        return self.provider("tts", self.app.tts_provider)  # type: ignore[return-value]

    def aligner(self) -> Aligner:
        return self.provider("align", self.app.align_provider)  # type: ignore[return-value]

    def image(self) -> ImageProvider:
        return self.provider("image", self.app.image_provider)  # type: ignore[return-value]

    def motion(self, fallback: bool = False) -> MotionProvider | None:
        name = self.app.motion_fallback if fallback else self.app.motion_provider
        if not name or name == "none":
            return None
        return self.provider("motion", name)  # type: ignore[return-value]

    def search(self, name: str | None = None) -> SearchProvider:
        p = self.provider("search", name or self.app.search_provider)
        p.provider_factory = lambda n: self.search(n)  # type: ignore[attr-defined]
        return p  # type: ignore[return-value]

    def music(self) -> MusicProvider:
        return self.provider("music", self.app.music_provider)  # type: ignore[return-value]

    def similarity(self) -> SimilarityProvider | None:
        name = self.app.similarity_provider
        if not name or name == "none":
            return None
        p = self.provider("similarity", name)
        p.llm_factory = lambda: self.llm("similarity")  # type: ignore[attr-defined]
        return p  # type: ignore[return-value]

    def uploader(self) -> Uploader:
        return self.provider("upload", self.app.uploader)  # type: ignore[return-value]

    def new_seed(self) -> int:
        return self.app.seed if self.app.seed is not None else random.randint(0, 2**31 - 1)

    def unload(self, kind: str | None = None) -> None:
        """Unload providers (all, or of one kind) and drop them so they're recreated on next use."""
        if not self.app.unload_between_stages and kind is not None:
            return
        for key in [k for k in self._providers if kind is None or k[0] == kind]:
            p = self._providers.pop(key)
            try:
                p.unload()
            except Exception as e:  # noqa: BLE001 - unload must never break the pipeline
                log.warning("unload of %s failed: %s", p.describe(), e)
