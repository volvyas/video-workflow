"""Generated background music through a ComfyUI workflow (default: ACE-Step 1.5 XL Turbo, MIT license).

Placeholders available in the workflow:
  {{tags}} {{lyrics}} {{bpm}} {{key}} {{seconds}} {{seed}} {{filename_prefix}}
"""

from __future__ import annotations

from pathlib import Path

from shorts.comfy import template
from shorts.comfy.client import ComfyClient
from shorts.providers.base import MusicProvider, ProviderSettings


def merge_tags(*groups: str) -> str:
    """Comma-separated tags from several strings, each tag once (case-insensitive), in order."""
    seen, out = set(), []
    for g in groups:
        for t in (x.strip() for x in g.split(",")):
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
    return ", ".join(out)


class ComfyMusicSettings(ProviderSettings):
    workflow: Path = Path("workflows/music_acestep15_turbo.json")
    lyrics: str = "[Instrumental]"  # background score: no vocals over the narration
    default_tags: str = "cinematic ambient, soft synth pads, light percussion, curious, documentary, instrumental"
    tags_suffix: str = "instrumental, no vocals"  # appended to every prompt
    extra_seconds: float = 2.0  # generate a bit longer than the video so the tail never runs out


class ComfyMusic(MusicProvider):
    name = "comfyui"
    env_prefix = "MUSIC_COMFY_"
    Settings = ComfyMusicSettings
    settings: ComfyMusicSettings

    def __init__(self, settings, app):
        super().__init__(settings, app)
        self._wf = template.load(self.settings.workflow)
        self._client = ComfyClient(app.comfy_url, app.comfy_timeout, app.comfy_poll_interval)

    def describe(self) -> str:
        return f"comfyui:{self.settings.workflow.name}"

    def generate(self, *, tags: str, bpm: int, key: str, seconds: float, seed: int, out_path: Path) -> Path | None:
        s = self.settings
        full = merge_tags(tags.strip() or s.default_tags, s.tags_suffix)
        wf = template.fill(self._wf, {
            "tags": full, "lyrics": s.lyrics, "bpm": max(60, min(140, int(bpm))), "key": key,
            "seconds": round(seconds + s.extra_seconds, 2), "seed": seed, "filename_prefix": "shorts/music",
        })
        self.log.info("music: %.0fs, %d bpm, %s: %s", seconds, bpm, key, full)
        return self._client.run(wf, out_path, kind="audio")

    def unload(self) -> None:
        self._client.free()
