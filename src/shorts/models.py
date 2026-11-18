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


Key = Literal[
    "C major", "C# major", "D major", "Eb major", "E major", "F major", "F# major", "G major", "Ab major", "A major",
    "Bb major", "B major", "C minor", "C# minor", "D minor", "Eb minor", "E minor", "F minor", "F# minor", "G minor",
    "Ab minor", "A minor", "Bb minor", "B minor",
]


class MusicSpec(BaseModel):
    tags: str = Field(default="", description="Instrumental background score for this video as comma-separated "
                      "music tags: genre, mood, 2-4 instruments, production style; always ends with 'instrumental'")
    bpm: int = Field(default=90, description="Tempo, 60-140")
    key: Key = Field(default="A minor", description="Musical key")


class Script(BaseModel):
    title: str = Field(description="YouTube title, max 70 characters, no clickbait lies")
    description: str = Field(description="YouTube description, 1-3 sentences plus hashtags")
    tags: list[str]
    visual_style: str = Field(
        default="",  # default only for scripts saved before this field existed; LLM schema still requires it
        description="One consistent look for every scene of this video (medium, palette, lighting, lens), "
                    "appended to every image prompt, in English"
    )
    scenes: list[Scene]
    music: MusicSpec = Field(default_factory=MusicSpec,  # default only for scripts saved before this field existed
                             description="Background music that fits this video's topic, mood and pace")

    @property
    def narration(self) -> str:
        return " ".join(s.narration.strip() for s in self.scenes)


Verdict = Literal["correct", "incorrect", "unverifiable", "opinion"]


class ClaimCheck(BaseModel):
    claim: str = Field(description="A factual statement made in the narration")
    verdict: Verdict
    correction: str = Field(description="Accurate replacement wording if not correct; empty string otherwise")
    source: str = Field(description="URL or reference supporting the verdict; empty string if none")


class ScriptReview(BaseModel):
    claims: list[ClaimCheck]
    score: int = Field(description="Overall quality of the reviewed script, 1-10 (hook, pacing, payoff, accuracy)")
    hook_score: int = Field(description="Strength of the first sentence as a scroll-stopper, 1-10")
    problems: list[str] = Field(description="Concrete weaknesses of the reviewed script, each with a fix suggestion")


class ImageVerdict(BaseModel):
    inventory: str = Field(default="", description="FIRST look carefully: list every person, creature and object "
                           "you see, where it is (top/middle/bottom, left/right), and how each one connects to the "
                           "others. Count each person's or creature's arms, legs, hands and heads.")
    fused_objects: bool = Field(default=False, description="Two separate things merged into one: a person or "
                                "animal grown into a machine, building or another body, limbs or body parts coming "
                                "out of objects, objects passing through each other, body parts with no owner")
    matches_prompt: bool = Field(description="The main subject and setting of the prompt are clearly shown")
    has_text_or_watermark: bool = Field(description="Any letters, words, logos, signatures or watermarks visible")
    has_artifacts: bool = Field(description="Deformed anatomy, extra/missing limbs or fingers, melted objects, "
                                            "garbled faces, obvious AI glitches")
    score: int = Field(description="Overall usability as a Shorts frame, 1-10")
    problems: list[str]

    def acceptable(self, min_score: int) -> bool:
        return self.matches_prompt and not self.has_text_or_watermark and not self.has_artifacts \
            and not self.fused_objects and self.score >= min_score


class PremiseCheck(BaseModel):
    premise: str = Field(description="The central factual claim the idea depends on, in one sentence")
    verdict: Literal["true", "misleading", "false", "unverifiable"] = Field(
        description="true: accurate; misleading: partly true or exaggerated, fixable; false: a myth or wrong; "
                    "unverifiable: no reliable evidence either way")
    explanation: str = Field(description="What the evidence says, 1-3 sentences")
    corrected_premise: str = Field(description="An accurate version of the premise that keeps the surprise, "
                                   "or empty string if the premise is true")
    source: str = Field(description="URL of the best supporting source, or empty string")


# ---------------------------------------------------------------- internal
class StoredIdea(Idea):
    id: str
    topic: str
    used_by: str | None = None  # run id that consumed this idea
    premise: PremiseCheck | None = None  # fact-check of the idea's central claim (IDEAS_FACT_CHECK)
    similar_to: list[str] = Field(default_factory=list)  # earlier history entries it resembles (not auto-picked)


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
