"""Prompt templates (string.Template syntax: $name). Override any file by placing it in PROMPTS_DIR."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from string import Template


def render(name: str, override_dir: Path | None = None, **values: object) -> str:
    if override_dir and (Path(override_dir) / f"{name}.txt").exists():
        text = (Path(override_dir) / f"{name}.txt").read_text(encoding="utf-8")
    else:
        text = resources.files(__package__).joinpath(f"{name}.txt").read_text(encoding="utf-8")
    return Template(text).safe_substitute({k: str(v) for k, v in values.items()}).strip()
