"""Mock TTS: writes a quiet tone whose length matches a typical speaking rate."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from shorts.providers.base import ProviderSettings, TTSProvider


class MockTTSSettings(ProviderSettings):
    words_per_second: float = 2.6
    sample_rate: int = 24000


class MockTTS(TTSProvider):
    name = "mock"
    env_prefix = "TTS_MOCK_"
    Settings = MockTTSSettings
    settings: MockTTSSettings

    def synthesize(self, text: str, out_path: Path) -> float:
        sr = self.settings.sample_rate
        dur = max(1.0, len(text.split()) / self.settings.words_per_second)
        n = int(dur * sr)
        frames = b"".join(struct.pack("<h", int(800 * math.sin(2 * math.pi * 220 * i / sr))) for i in range(n))
        with wave.open(str(out_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(frames)
        return n / sr
