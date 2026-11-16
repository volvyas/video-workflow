"""Deterministic offline LLM for tests and dry runs."""

from __future__ import annotations

import re

from shorts.models import Idea, IdeaBatch, Scene, Script
from shorts.providers.base import LLMProvider, ProviderError, ProviderSettings, T

_CAMERAS = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_up", "pan_down"]


class MockSettings(ProviderSettings):
    scenes: int = 5


class MockLLM(LLMProvider):
    name = "mock"
    env_prefix = "LLM_MOCK_"
    Settings = MockSettings
    settings: MockSettings

    def generate_json(self, *, system: str, prompt: str, schema: type[T]) -> T:
        if schema is IdeaBatch:
            m = re.search(r"Generate (\d+)", prompt)
            n = int(m.group(1)) if m else 5
            topic = (re.search(r"Topic: (.+)", prompt) or [None, "things"])[1].strip()
            return IdeaBatch(ideas=[
                Idea(title=f"Mock idea {i + 1} about {topic}", hook=f"You won't believe fact number {i + 1}.",
                     angle="A quick surprising fact.", why_it_works="Curiosity gap.")
                for i in range(n)
            ])
        if schema is Script:
            return Script(
                title="Mock short", description="A mock video. #shorts", tags=["mock", "test"],
                scenes=[
                    Scene(narration=f"This is scene number {i + 1}, and it says something interesting.",
                          image_prompt=f"mock scene {i + 1}", motion_prompt="slow camera push",
                          camera=_CAMERAS[i % len(_CAMERAS)], on_screen_text="")
                    for i in range(self.settings.scenes)
                ],
            )
        raise ProviderError(f"mock LLM has no canned output for {schema.__name__}")
