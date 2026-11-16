"""Mock motion: static image held for the duration (fast ffmpeg encode)."""

from __future__ import annotations

from pathlib import Path

from shorts.media import ffmpeg
from shorts.providers.base import MotionProvider, ProviderError, ProviderSettings


class MockMotionSettings(ProviderSettings):
    fail: bool = False  # simulate a failing provider to test fallback


class MockMotion(MotionProvider):
    name = "mock"
    env_prefix = "MOTION_MOCK_"
    Settings = MockMotionSettings
    settings: MockMotionSettings

    def animate(self, *, image: Path, prompt: str, camera: str, duration: float, seed: int, out_path: Path) -> Path:
        if self.settings.fail:
            raise ProviderError("mock motion failure (MOTION_MOCK_FAIL=true)")
        ffmpeg.run(["-loop", "1", "-i", image, "-t", f"{duration:.3f}", "-r", "16",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", out_path])
        return out_path
