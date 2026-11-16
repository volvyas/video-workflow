"""Text-to-image through any ComfyUI workflow (Flux, SDXL, Qwen-Image, ...).

Placeholders available in the workflow: {{prompt}} {{negative}} {{seed}} {{width}} {{height}} {{filename_prefix}}
"""

from __future__ import annotations

import shutil
from pathlib import Path

from shorts.comfy import template
from shorts.comfy.client import ComfyClient
from shorts.providers.base import ImageProvider, ProviderSettings


class ComfyImageSettings(ProviderSettings):
    workflow: Path = Path("workflows/image_sdxl.json")


class ComfyImage(ImageProvider):
    name = "comfyui"
    env_prefix = "IMAGE_COMFY_"
    Settings = ComfyImageSettings
    settings: ComfyImageSettings

    def __init__(self, settings, app):
        super().__init__(settings, app)
        self._wf = template.load(self.settings.workflow)
        self._client = ComfyClient(app.comfy_url, app.comfy_timeout, app.comfy_poll_interval)

    def describe(self) -> str:
        return f"comfyui:{self.settings.workflow.name}"

    def generate(self, *, prompt: str, negative: str, width: int, height: int, seed: int, out_path: Path) -> Path:
        wf = template.fill(self._wf, {
            "prompt": prompt, "negative": negative, "seed": seed, "width": width, "height": height,
            "filename_prefix": "shorts/img",
        })
        got = self._client.run(wf, out_path, kind="image")
        if got.suffix != out_path.suffix:
            from PIL import Image

            Image.open(got).convert("RGB").save(out_path)
            got.unlink()
        elif got != out_path:
            shutil.move(got, out_path)
        return out_path

    def unload(self) -> None:
        self._client.free()
