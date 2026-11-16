"""Configuration: .env loading, global AppSettings, and per-provider settings extraction.

Precedence (highest first): real environment variables > the .env file > defaults in code.
Empty values (`KEY=`) are treated as "not set".
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ConfigError(RuntimeError):
    pass


class AppSettings(BaseModel):
    """Global settings. Every field maps to the upper-cased env var of the same name."""

    model_config = ConfigDict(extra="ignore")

    # --- provider selection -------------------------------------------------
    llm_provider: str = "mock"
    ideas_llm_provider: str | None = None
    script_llm_provider: str | None = None
    review_llm_provider: str | None = None
    image_qa_llm_provider: str | None = None  # must be vision-capable
    tts_provider: str = "mock"
    align_provider: str = "proportional"
    image_provider: str = "mock"
    motion_provider: str = "kenburns"
    motion_fallback: str | None = "kenburns"
    motion_scenes: str = "all"  # all | none | first:N | every:N
    uploader: str = "none"

    # --- content ------------------------------------------------------------
    content_language: str = "English"
    channel_brief: str = "A faceless YouTube Shorts channel with surprising, accurate, fast-paced facts."
    video_target_seconds: int = 40
    style_prompt: str = "cinematic, highly detailed, dramatic lighting, vertical composition"
    negative_prompt: str = "text, watermark, logo, blurry, lowres, deformed, extra fingers"

    # --- quality gates --------------------------------------------------------
    review_enabled: bool = True
    review_web_search: bool = True  # fact-check with web search when the LLM provider supports it
    review_min_score: int = 7
    review_max_rounds: int = 2
    review_on_low_score: str = "fail"  # fail (stop this run for human review) | continue
    image_qa_enabled: bool = True
    image_qa_min_score: int = 6
    image_qa_max_retries: int = 2

    # --- retention ------------------------------------------------------------
    sfx_dir: Path | None = Path("assets/sfx")  # whooshes/hits played on scene cuts
    sfx_volume: float = 0.35
    sfx_lead: float = 0.12  # start the sound slightly before the cut

    # --- output format ------------------------------------------------------
    video_width: int = 1080
    video_height: int = 1920
    video_fps: int = 30
    image_width: int = 768
    image_height: int = 1344
    motion_width: int = 480
    motion_height: int = 832
    motion_fps: int = 16
    max_slowdown: float = 1.25  # clip shorter than scene: slow down up to this factor, then freeze

    # --- audio --------------------------------------------------------------
    scene_gap_ms: int = 120
    music_dir: Path | None = Path("assets/music")
    music_volume: float = 0.12

    # --- captions -----------------------------------------------------------
    captions_enabled: bool = True
    caption_font: str = "Montserrat ExtraBold"
    caption_fonts_dir: Path | None = Path("assets/fonts")
    caption_font_size: int = 92
    caption_color: str = "#FFFFFF"
    caption_highlight_color: str = "#FFE500"
    caption_outline_color: str = "#000000"
    caption_outline: int = 6
    caption_margin_bottom: int = 560
    caption_words_per_group: int = 3
    caption_uppercase: bool = True

    # --- ComfyUI (shared by comfy image / i2v providers) ---------------------
    comfy_url: str = "http://127.0.0.1:8188"
    comfy_timeout: int = 1800  # seconds per job
    comfy_poll_interval: float = 1.0

    # --- paths & behaviour --------------------------------------------------
    runs_dir: Path = Path("runs")
    ideas_dir: Path = Path("ideas")
    prompts_dir: Path | None = None  # override built-in prompt templates
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
    unload_between_stages: bool = True
    seed: int | None = None  # fixed base seed; random per run when unset
    auto_upload: bool = False

    extra: dict[str, Any] = Field(default_factory=dict, exclude=True)

    def llm_for(self, stage: str) -> str:
        override = getattr(self, f"{stage}_llm_provider", None)
        return override or self.llm_provider


class Env:
    """A merged view of .env + process environment."""

    def __init__(self, values: dict[str, str], source: Path | None = None):
        self.values = {k: v for k, v in values.items() if v is not None and v != ""}
        self.source = source
        self._app: AppSettings | None = None

    # -- loading -------------------------------------------------------------
    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "Env":
        path = Path(env_file) if env_file else Path(os.environ.get("SHORTS_ENV_FILE", ".env"))
        file_values: dict[str, str] = {}
        if path.exists():
            file_values = {k: v for k, v in dotenv_values(path).items() if v is not None}
            # Export to the process so SDKs that read env vars directly
            # (ANTHROPIC_API_KEY, HF_TOKEN, ...) see values from the file too.
            for k, v in file_values.items():
                os.environ.setdefault(k, v)
        elif env_file:
            raise ConfigError(f"env file not found: {path}")
        merged = {**file_values, **os.environ}
        return cls(merged, path if path.exists() else None)

    # -- access --------------------------------------------------------------
    @property
    def app(self) -> AppSettings:
        if self._app is None:
            fields = AppSettings.model_fields
            data = {k.lower(): v for k, v in self.values.items() if k.lower() in fields}
            try:
                self._app = AppSettings.model_validate(data)
            except ValidationError as e:
                raise ConfigError(f"invalid global settings:\n{e}") from e
        return self._app

    def section(self, prefix: str) -> dict[str, str]:
        """All keys starting with `prefix`, prefix stripped and lower-cased."""
        prefix = prefix.upper()
        return {k[len(prefix):].lower(): v for k, v in self.values.items() if k.upper().startswith(prefix)}

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.values.get(key, default)
