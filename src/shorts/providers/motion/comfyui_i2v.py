"""Mode B: image-to-video through a ComfyUI workflow (Wan 2.2 I2V, LTX-Video, ...).

Placeholders available in the workflow:
  {{image}} {{prompt}} {{negative}} {{seed}} {{width}} {{height}} {{frames}} {{fps}} {{filename_prefix}}
"""

from __future__ import annotations

from pathlib import Path

from shorts.comfy import template
from shorts.comfy.client import ComfyClient
from shorts.providers.base import MotionProvider, ProviderSettings


class ComfyI2VSettings(ProviderSettings):
    workflow: Path = Path("workflows/i2v_wan22.json")
    max_frames: int = 81  # Wan 2.2: 81 frames = ~5 s @ 16 fps
    min_frames: int = 33
    frame_step: int = 4  # Wan needs 4n+1 frames, LTX needs 8n+1
    negative: str = (
        "static, still image, blurry, low quality, jpeg artifacts, distorted, morphing, deformed, "
        "extra limbs, text, watermark, subtitles"
    )
    prompt_suffix: str = ""  # appended to every motion prompt


class ComfyI2V(MotionProvider):
    name = "comfyui_i2v"
    env_prefix = "MOTION_I2V_"
    Settings = ComfyI2VSettings
    settings: ComfyI2VSettings

    def __init__(self, settings, app):
        super().__init__(settings, app)
        self._wf = template.load(self.settings.workflow)
        self._client = ComfyClient(app.comfy_url, app.comfy_timeout, app.comfy_poll_interval)

    def describe(self) -> str:
        return f"comfyui_i2v:{self.settings.workflow.name}"

    def frames_for(self, duration: float) -> int:
        s = self.settings
        want = round(duration * self.app.motion_fps)
        want = max(s.min_frames, min(s.max_frames, want))
        return ((want - 1 + s.frame_step - 1) // s.frame_step) * s.frame_step + 1 if s.frame_step > 1 else want

    def animate(self, *, image: Path, prompt: str, camera: str, duration: float, seed: int, out_path: Path) -> Path:
        uploaded = self._client.upload_image(image)
        full_prompt = f"{prompt}, {self.settings.prompt_suffix}".strip(", ") if self.settings.prompt_suffix else prompt
        frames = self.frames_for(duration)
        wf = template.fill(self._wf, {
            "image": uploaded, "prompt": full_prompt, "negative": self.settings.negative, "seed": seed,
            "width": self.app.motion_width, "height": self.app.motion_height, "frames": frames,
            "fps": self.app.motion_fps, "filename_prefix": "shorts/clip",
        })
        self.log.info("i2v %s: %d frames @ %dx%d", image.name, frames, self.app.motion_width, self.app.motion_height)
        return self._client.run(wf, out_path, kind="video")

    def unload(self) -> None:
        self._client.free()
