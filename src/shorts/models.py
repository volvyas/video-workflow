"""Data models shared by stages and providers.

Models used as LLM structured output (IdeaBatch, Script) deliberately avoid defaults and
validation constraints: strict JSON-schema modes in Claude / Ollama / OpenAI-compatible servers
require every property to be present and support only a subset of JSON Schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Camera = Literal["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"]


# ---------------------------------------------------------------- LLM outputs
class Idea(BaseModel):
    title: str = Field(description="Working title, max ~60 characters")
    hook: str = Field(description="The first spoken sentence; must create curiosity within 2 seconds")
    angle: str = Field(description="What the video is about and the payoff the viewer gets")
    why_it_works: str = Field(description="Why this would be watched to the end and shared")


class IdeaBatch(BaseModel):
    ideas: list[Idea]


class Scene(BaseModel):
    narration: str = Field(description="Exactly what the voice says during this scene")
    image_prompt: str = Field(description="Detailed visual description for a text-to-image model, no text in image")
    motion_prompt: str = Field(description="Short description of motion for an image-to-video model")
    camera: Camera = Field(description="Camera move used if the scene is animated as a still")
    on_screen_text: str = Field(description="Optional short overlay text; empty string if none")


class Script(BaseModel):
    title: str = Field(description="YouTube title, max 70 characters, no clickbait lies")
    description: str = Field(description="YouTube description, 1-3 sentences plus hashtags")
    tags: list[str]
    scenes: list[Scene]

    @property
    def narration(self) -> str:
        return " ".join(s.narration.strip() for s in self.scenes)


# ---------------------------------------------------------------- internal
class StoredIdea(Idea):
    id: str
    topic: str
    used_by: str | None = None  # run id that consumed this idea


class IdeaPool(BaseModel):
    topic: str
    ideas: list[StoredIdea] = Field(default_factory=list)


class WordTiming(BaseModel):
    word: str
    start: float
    end: float


class SceneTiming(BaseModel):
    index: int
    start: float
    duration: float  # audio duration of the scene (without trailing gap)
    slot: float  # time this scene occupies on the timeline (audio + gap)


class VideoMeta(BaseModel):
    title: str
    description: str
    tags: list[str] = Field(default_factory=list)
    privacy: Literal["private", "unlisted", "public"] = "private"
    publish_at: str | None = None  # RFC3339; requires privacy=private
    made_for_kids: bool = False
    synthetic_media: bool = True
