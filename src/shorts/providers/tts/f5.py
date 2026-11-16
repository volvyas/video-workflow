"""F5-TTS: zero-shot voice cloning from a short reference clip. Runs in-process on the GPU.

Install with `uv sync --extra f5` (pulls torch; on RTX 50xx use a CUDA 12.8+ torch build, see docs/SETUP.md).
"""

from __future__ import annotations

import inspect
from pathlib import Path

from shorts.media import ffmpeg
from shorts.providers.base import ProviderError, ProviderSettings, TTSProvider


class F5Settings(ProviderSettings):
    model: str = "F5TTS_v1_Base"  # or F5TTS_Base / E2TTS_Base, or a finetune's config name
    ckpt_file: str = ""  # custom checkpoint (e.g. a finetune for another language); "" = download default
    vocab_file: str = ""  # vocab.txt matching ckpt_file, if the finetune has its own
    ref_audio: Path = Path("assets/voices/narrator.wav")  # 5-12 s of clean speech
    ref_text: str = ""  # exact transcript of ref_audio; "" = auto-transcribe (slower, loads whisper)
    speed: float = 1.0
    nfe_step: int = 32  # quality/speed tradeoff; 16 is ~2x faster
    cfg_strength: float = 2.0
    cross_fade_duration: float = 0.15
    seed: int | None = None  # fixed seed = same intonation every regeneration
    remove_silence: bool = False
    device: str | None = None  # cuda | cpu | None (auto)


class F5TTSProvider(TTSProvider):
    name = "f5"
    env_prefix = "TTS_F5_"
    Settings = F5Settings
    settings: F5Settings

    def __init__(self, settings, app):
        super().__init__(settings, app)
        self._model = None
        if not self.settings.ref_audio.exists():
            raise ProviderError(
                f"F5 reference audio not found: {self.settings.ref_audio} (set TTS_F5_REF_AUDIO, see docs/SETUP.md)"
            )

    def describe(self) -> str:
        return f"f5:{self.settings.model}"

    def verify(self) -> None:
        self._require_module("f5_tts", "f5")

    def _load(self):
        if self._model is None:
            try:
                from f5_tts.api import F5TTS
            except ImportError as e:
                raise ProviderError("f5-tts is not installed: run `uv sync --extra f5`") from e
            kwargs = {"model": self.settings.model, "ckpt_file": self.settings.ckpt_file,
                      "vocab_file": self.settings.vocab_file, "device": self.settings.device}
            self._model = F5TTS(**_supported(F5TTS.__init__, kwargs))
        return self._model

    def synthesize(self, text: str, out_path: Path) -> float:
        model = self._load()
        kwargs = dict(
            ref_file=str(self.settings.ref_audio), ref_text=self.settings.ref_text, gen_text=text,
            file_wave=str(out_path), speed=self.settings.speed, nfe_step=self.settings.nfe_step,
            cfg_strength=self.settings.cfg_strength, cross_fade_duration=self.settings.cross_fade_duration,
            seed=self.settings.seed, remove_silence=self.settings.remove_silence,
            show_info=lambda *a, **k: None,
        )
        model.infer(**_supported(model.infer, kwargs))
        if not Path(out_path).exists():
            raise ProviderError("F5-TTS produced no output file")
        return ffmpeg.duration(out_path)

    def unload(self) -> None:
        if self._model is not None:
            self._model = None
            try:
                import gc

                import torch

                gc.collect()
                torch.cuda.empty_cache()
            except ImportError:
                pass


def _supported(fn, kwargs: dict) -> dict:
    """Drop kwargs the installed f5-tts version doesn't accept (its API changed between releases)."""
    params = inspect.signature(fn).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in params and v is not None}
