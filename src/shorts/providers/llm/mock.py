"""Deterministic offline LLM for tests and dry runs."""

from __future__ import annotations

import re

from pathlib import Path

from shorts.models import Idea, IdeaBatch, ImageVerdict, Scene, Script, ScriptReview
from shorts.providers.base import LLMProvider, ProviderError, ProviderSettings, T

_CAMERAS = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_up", "pan_down"]


class MockSettings(ProviderSettings):
    scenes: int = 5
    review_score: int = 8  # score returned by script reviews
    image_score: int = 8  # score returned by image checks (set low to exercise re-rolls)
    web_search: bool = True
    similarity_score: int = 2  # 0-10 returned for every candidate by the llm similarity check
    premise_verdict: str = "true"  # verdict of idea premise checks


class MockLLM(LLMProvider):
    name = "mock"
    env_prefix = "LLM_MOCK_"
    Settings = MockSettings
    settings: MockSettings
    supports_vision = True

    @property
    def supports_web_search(self) -> bool:
        return self.settings.web_search

    def research(self, *, system: str, prompt: str) -> str:
        return "Mock research: all claims look correct. Source: https://example.com"

    def generate_json(self, *, system: str, prompt: str, schema: type[T], images: list[Path] | None = None) -> T:
        if schema.__name__ == "PremiseCheck":
            v = self.settings.premise_verdict
            return schema(premise="mock premise", verdict=v, explanation=f"mock says {v}",
                          corrected_premise="" if v == "true" else "a corrected mock premise", source="")
        if schema.__name__ == "SimilarityJudgement":
            n = len(re.findall(r"^\d+\. ", prompt.split("EARLIER:")[-1], re.M))
            return schema.model_validate({"items": [{"index": i + 1, "score": self.settings.similarity_score,
                                                     "reason": "mock"} for i in range(n)]})
        if schema.__name__ == "SearchQueries":
            return schema(queries=["mock claim one", "mock claim two"])
        if schema is ScriptReview:
            return ScriptReview(claims=[], score=self.settings.review_score, hook_score=self.settings.review_score,
                                problems=[] if self.settings.review_score >= 7 else ["weak hook"])
        if schema is ImageVerdict:
            ok = self.settings.image_score >= 6
            return ImageVerdict(matches_prompt=ok, has_text_or_watermark=False, has_artifacts=not ok,
                                score=self.settings.image_score, problems=[] if ok else ["mock artifact"])
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
                visual_style="flat pastel illustration",
                scenes=[
                    Scene(narration=f"This is scene number {i + 1}, and it says something interesting.",
                          image_prompt=f"mock scene {i + 1}", motion_prompt="slow camera push",
                          camera=_CAMERAS[i % len(_CAMERAS)], on_screen_text="")
                    for i in range(self.settings.scenes)
                ],
            )
        raise ProviderError(f"mock LLM has no canned output for {schema.__name__}")
