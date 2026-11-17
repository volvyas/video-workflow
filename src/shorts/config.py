"""Configuration: .env loading, global AppSettings, and per-provider settings extraction.

Precedence (highest first): real environment variables > style profile (STYLE_PROFILE=name loads
styles/<name>.env) > the .env file > defaults in code. Empty values (`KEY=`) are treated as "not set".
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
    similarity_llm_provider: str | None = None  # only for SIMILARITY_PROVIDER=llm
    tts_provider: str = "mock"
    align_provider: str = "proportional"
    image_provider: str = "mock"
    motion_provider: str = "kenburns"
    motion_fallback: str | None = "kenburns"
    motion_scenes: str = "all"  # all | none | first:N | every:N
    uploader: str = "none"
    search_provider: str = "none"  # web search for local-LLM fact-checking: ddgs | searxng | none
    similarity_provider: str = "lexical"  # history repeat check: lexical | embeddings | llm | none

    # --- content ------------------------------------------------------------
    content_language: str = "English"
    channel_brief: str = "A faceless YouTube Shorts channel with surprising, accurate, fast-paced facts."
    video_target_seconds: int = 40
    style_profile: str | None = None  # name of a styles/<name>.env file (see Env.load)
    style_prompt: str = "cinematic, highly detailed, dramatic lighting, vertical composition"
    style_guide: str = ""  # extra visual instructions for the script writer, set by style profiles
    negative_prompt: str = "text, watermark, logo, blurry, lowres, deformed, extra fingers"

    # --- quality gates --------------------------------------------------------
    review_enabled: bool = True
    review_web_search: bool = True  # fact-check with web search when the LLM provider supports it
    review_min_score: int = 7
    review_max_rounds: int = 2
    review_on_low_score: str = "fail"  # fail (stop this run for human review) | continue
    review_search_queries: int = 6  # local research: max search queries per round
    review_search_results: int = 4  # results per query
    review_fetch_pages: int = 3  # also read the full text of this many top pages (0 = snippets only)
    image_qa_enabled: bool = True
    image_qa_min_score: int = 6
    image_qa_max_retries: int = 2

    # --- history (repeat check) ------------------------------------------------
    history_enabled: bool = True
    history_file: Path = Path("history/scripts.json")
    history_on_similar: str = "warn"  # warn (note it and continue) | fail (stop the run before/after the script)
    history_prompt_limit: int = 40  # max history entries per section shown to the ideas LLM

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
    caption_bold: bool = False  # synthetic bold; leave off for fonts that are already heavy (all bundled ones)

    # --- ComfyUI (shared by comfy image / i2v providers) ---------------------
    comfy_url: str = "http://127.0.0.1:8188"
    comfy_timeout: int = 1800  # seconds per job
    comfy_poll_interval: float = 1.0

    # --- paths & behaviour --------------------------------------------------
    runs_dir: Path = Path("runs")
    ideas_dir: Path = Path("ideas")
    styles_dir: Path = Path("styles")
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


_EXPORTED: dict[str, str] = {}  # keys Env.load put into os.environ (not set by the user)


def list_styles(styles_dir: Path) -> list[str]:
    return sorted(p.stem for p in Path(styles_dir).glob("*.env"))


def load_style(styles_dir: Path, name: str) -> dict[str, str]:
    p = Path(styles_dir) / f"{name}.env"
    if not p.exists():
        known = ", ".join(list_styles(styles_dir)) or "none"
        raise ConfigError(f"style profile '{name}' not found: {p} (available: {known})")
    return {k: v for k, v in dotenv_values(p).items() if v is not None}


class Env:
    """A merged view of .env + process environment."""

    def __init__(self, values: dict[str, str], source: Path | None = None):
        self.values = {k: v for k, v in values.items() if v is not None and v != ""}
        self.source = source
        self._app: AppSettings | None = None

    # -- loading -------------------------------------------------------------
    @classmethod
    def load(cls, env_file: str | Path | None = None, style: str | None = None) -> "Env":
        """Load .env (or `env_file`), then the style profile named by `style` / STYLE_PROFILE on top of it."""
        path = Path(env_file) if env_file else Path(os.environ.get("SHORTS_ENV_FILE", ".env"))
        file_values: dict[str, str] = {}
        if path.exists():
            file_values = {k: v for k, v in dotenv_values(path).items() if v is not None}
        elif env_file:
            raise ConfigError(f"env file not found: {path}")
        # Real environment only: ignore values a previous load exported itself (else a reload with another
        # style profile would see the old profile's values as if they came from the shell).
        process = {k: v for k, v in os.environ.items() if _EXPORTED.get(k) != v}
        name = style or process.get("STYLE_PROFILE") or file_values.get("STYLE_PROFILE")
        profile: dict[str, str] = {}
        if name:
            styles_dir = Path(process.get("STYLES_DIR") or file_values.get("STYLES_DIR") or "styles")
            profile = load_style(styles_dir, name)
            profile["STYLE_PROFILE"] = name
        merged = {**file_values, **profile, **process}
        if style:
            merged["STYLE_PROFILE"] = style
        # Export to the process so SDKs that read env vars directly
        # (ANTHROPIC_API_KEY, HF_TOKEN, ...) see values from the files too.
        for k, v in {**file_values, **profile}.items():
            if k not in process:
                os.environ[k] = v
                _EXPORTED[k] = v
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
