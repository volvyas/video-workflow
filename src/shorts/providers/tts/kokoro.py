"""Kokoro TTS (82M params, Apache-2.0 weights): fast, good quality, preset voices, no cloning.

Install with `uv sync --extra kokoro`. Runs fine on CPU; a GPU makes it near-instant.
Voices: https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md. The first letter of the voice is
its language: a=American English, b=British English, e=Spanish, f=French, h=Hindi, i=Italian,
j=Japanese, p=Brazilian Portuguese, z=Mandarin.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from shorts.media.audio import write_wav
from shorts.providers.base import ProviderError, ProviderSettings, TTSProvider

SAMPLE_RATE = 24000


class KokoroSettings(ProviderSettings):
    voice: str = "am_michael"  # e.g. af_heart, af_bella, am_michael, am_fenrir, bm_george; blends: "af_heart,am_michael"
    lang_code: str = ""  # "" = first letter of the voice
    speed: float = 1.0
    device: str | None = None  # cuda | cpu | None (auto)


class KokoroTTS(TTSProvider):
    name = "kokoro"
    env_prefix = "TTS_KOKORO_"
    Settings = KokoroSettings
    settings: KokoroSettings
    license_note = None  # Apache-2.0 weights: commercial use OK

    def __init__(self, settings, app):
        super().__init__(settings, app)
        self._pipeline = None

    def describe(self) -> str:
        return f"kokoro:{self.settings.voice}"

    def verify(self) -> None:
        self._require_module("kokoro", "kokoro")

    def _load(self):
        if self._pipeline is None:
            try:
                from kokoro import KPipeline
            except ImportError as e:
                raise ProviderError("kokoro is not installed: run `uv sync --extra kokoro`") from e
            lang = self.settings.lang_code or self.settings.voice[0]
            kwargs = {"lang_code": lang, "repo_id": "hexgrad/Kokoro-82M", "device": self.settings.device}
            params = inspect.signature(KPipeline.__init__).parameters
            self._pipeline = KPipeline(**{k: v for k, v in kwargs.items() if k in params and v is not None})
        return self._pipeline

    def synthesize(self, text: str, out_path: Path) -> float:
        import numpy as np

        chunks = []
        for result in self._load()(text, voice=self.settings.voice, speed=self.settings.speed, split_pattern=None):
            audio = result[2] if isinstance(result, tuple) else getattr(result, "audio", None)
            if audio is not None:
                chunks.append(np.asarray(audio.cpu() if hasattr(audio, "cpu") else audio, dtype=np.float32))
        if not chunks:
            raise ProviderError("kokoro produced no audio")
        return write_wav(out_path, np.concatenate(chunks), SAMPLE_RATE)

    def unload(self) -> None:
        if self._pipeline is not None:
            self._pipeline = None
            try:
                import gc

                import torch

                gc.collect()
                torch.cuda.empty_cache()
            except ImportError:
                pass
