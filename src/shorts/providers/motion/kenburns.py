"""Mode A: smooth Ken Burns pan/zoom over a still image.

Frames are rendered with Pillow using sub-pixel crop rectangles (Image.transform EXTENT), which avoids
the jitter of ffmpeg's zoompan, and piped straight into ffmpeg.
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

from PIL import Image

from shorts.media import ffmpeg
from shorts.providers.base import MotionProvider, ProviderError, ProviderSettings


class KenBurnsSettings(ProviderSettings):
    zoom: float = 1.18  # max zoom factor
    easing: str = "ease_in_out"  # ease_in_out | linear
    crf: int = 17
    preset: str = "veryfast"
    resample: str = "bicubic"  # bicubic (sharper) | bilinear (~2x faster)


def _ease(t: float, mode: str) -> float:
    return t if mode == "linear" else 0.5 - 0.5 * math.cos(math.pi * t)


class KenBurns(MotionProvider):
    name = "kenburns"
    env_prefix = "MOTION_KENBURNS_"
    Settings = KenBurnsSettings
    settings: KenBurnsSettings

    def animate(self, *, image: Path, prompt: str, camera: str, duration: float, seed: int, out_path: Path) -> Path:
        W, H, fps = self.app.video_width, self.app.video_height, self.app.video_fps
        src = Image.open(image).convert("RGB")
        iw, ih = src.size
        # base crop: largest W:H rectangle that fits in the source
        aspect = W / H
        bw, bh = (ih * aspect, ih) if iw / ih > aspect else (iw, iw / aspect)
        Z = max(1.0, self.settings.zoom)
        n = max(1, round(duration * fps))

        def rect(t: float) -> tuple[float, float, float, float]:
            e = _ease(t, self.settings.easing)
            if camera == "zoom_in":
                z, fx, fy = 1 + (Z - 1) * e, 0.5, 0.5
            elif camera == "zoom_out":
                z, fx, fy = Z - (Z - 1) * e, 0.5, 0.5
            else:
                z = Z
                fx, fy = {
                    "pan_left": (1 - e, 0.5), "pan_right": (e, 0.5),
                    "pan_up": (0.5, 1 - e), "pan_down": (0.5, e),
                }.get(camera, (0.5, 0.5))
            cw, ch = bw / z, bh / z
            x0 = (iw - cw) * fx
            y0 = (ih - ch) * fy
            return (x0, y0, x0 + cw, y0 + ch)

        cmd = [ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
               "-c:v", "libx264", "-preset", self.settings.preset, "-crf", str(self.settings.crf),
               "-pix_fmt", "yuv420p", str(out_path)]
        resample = Image.Resampling.BILINEAR if self.settings.resample == "bilinear" else Image.Resampling.BICUBIC
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for i in range(n):
                frame = src.transform((W, H), Image.Transform.EXTENT, rect(i / max(1, n - 1)),
                                      resample=resample)
                proc.stdin.write(frame.tobytes())
        except BrokenPipeError:
            pass
        finally:
            proc.stdin.close()
            err = proc.stderr.read().decode(errors="replace")
            proc.wait()
        if proc.returncode != 0:
            raise ProviderError(f"ken burns encode failed: {err[-1000:]}")
        return out_path
