"""Dependency-free aligner: spreads the words over the audio, weighted by word length."""

from __future__ import annotations

from pathlib import Path

from shorts.media import ffmpeg
from shorts.models import WordTiming
from shorts.providers.base import Aligner, ProviderSettings


class ProportionalSettings(ProviderSettings):
    lead_in: float = 0.05  # seconds of silence TTS usually adds at the start
    tail: float = 0.1


class ProportionalAligner(Aligner):
    name = "proportional"
    env_prefix = "ALIGN_PROPORTIONAL_"
    Settings = ProportionalSettings
    settings: ProportionalSettings

    def align(self, audio: Path, text: str) -> list[WordTiming]:
        return spread(text.split(), self.settings.lead_in, ffmpeg.duration(audio) - self.settings.tail)


def spread(words: list[str], start: float, end: float) -> list[WordTiming]:
    if not words:
        return []
    end = max(end, start + 0.1 * len(words))
    weights = [len(w) + 2 + (3 if w[-1:] in ".,!?;:" else 0) for w in words]
    total = sum(weights)
    out, t = [], start
    for w, wt in zip(words, weights):
        d = (end - start) * wt / total
        out.append(WordTiming(word=w, start=t, end=t + d))
        t += d
    return out
