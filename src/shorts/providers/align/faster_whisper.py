"""Word timings from faster-whisper, mapped back onto the *script* words (so captions keep exact spelling)."""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from shorts.models import WordTiming
from shorts.providers.align.proportional import spread
from shorts.providers.base import Aligner, ProviderError, ProviderSettings


class FasterWhisperSettings(ProviderSettings):
    model: str = "large-v3-turbo"  # tiny | base | small | medium | large-v3 | large-v3-turbo | distil-large-v3
    device: str = "auto"  # cuda | cpu | auto
    compute_type: str = "default"  # float16 | int8_float16 | int8 | default
    language: str | None = None  # ISO code, e.g. "en", "uk"; None = auto-detect


def _norm(w: str) -> str:
    return re.sub(r"\W+", "", w.lower())


class FasterWhisperAligner(Aligner):
    name = "faster_whisper"
    env_prefix = "ALIGN_WHISPER_"
    Settings = FasterWhisperSettings
    settings: FasterWhisperSettings

    def __init__(self, settings, app):
        super().__init__(settings, app)
        self._model = None

    def verify(self) -> None:
        self._require_module("faster_whisper", "whisper")

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise ProviderError("faster-whisper not installed: run `uv sync --extra whisper`") from e
            s = self.settings
            self._model = WhisperModel(s.model, device=s.device, compute_type=s.compute_type)
        return self._model

    def align(self, audio: Path, text: str) -> list[WordTiming]:
        segments, _ = self._load().transcribe(
            str(audio), language=self.settings.language, word_timestamps=True, initial_prompt=text, beam_size=5
        )
        heard = [(w.word.strip(), w.start, w.end) for seg in segments for w in (seg.words or [])]
        return map_onto_script(text.split(), heard)

    def unload(self) -> None:
        self._model = None


def map_onto_script(script_words: list[str], heard: list[tuple[str, float, float]]) -> list[WordTiming]:
    """Give each script word the timing of the matching recognized word; interpolate the rest."""
    if not heard:
        return spread(script_words, 0.0, 0.4 * len(script_words))
    times: list[tuple[float, float] | None] = [None] * len(script_words)
    sm = difflib.SequenceMatcher(a=[_norm(w) for w in script_words], b=[_norm(h[0]) for h in heard], autojunk=False)
    for block in sm.get_opcodes():
        tag, a0, a1, b0, b1 = block
        if tag == "equal":
            for k in range(a1 - a0):
                times[a0 + k] = (heard[b0 + k][1], heard[b0 + k][2])
        elif tag == "replace" and b1 > b0:
            # spread the replaced script words over the time span of the recognized ones
            for wt, k in zip(spread(script_words[a0:a1], heard[b0][1], heard[b1 - 1][2]), range(a0, a1)):
                times[k] = (wt.start, wt.end)
    # fill gaps by interpolating between known neighbours
    out: list[WordTiming] = []
    i = 0
    while i < len(script_words):
        if times[i] is not None:
            out.append(WordTiming(word=script_words[i], start=times[i][0], end=times[i][1]))
            i += 1
            continue
        j = i
        while j < len(script_words) and times[j] is None:
            j += 1
        start = out[-1].end if out else heard[0][1]
        end = times[j][0] if j < len(script_words) else heard[-1][2]
        out.extend(spread(script_words[i:j], start, max(end, start + 0.15 * (j - i))))
        i = j
    return out
