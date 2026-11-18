"""TTS through a ComfyUI workflow, so the voice model runs on the ComfyUI machine (e.g. F5-TTS, Chatterbox, IndexTTS
custom nodes).

Placeholders available in the workflow:
  {{text}} {{ref_audio}} {{ref_text}} {{seed}} {{speed}} {{exaggeration}} {{cfg_weight}} {{filename_prefix}}
The reference clip (only needed when the workflow uses {{ref_audio}}, i.e. clones a voice) is uploaded to ComfyUI's
input folder once per run of the pipeline.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from shorts.comfy import template
from shorts.comfy.client import ComfyClient
from shorts.media import ffmpeg
from shorts.providers.base import ProviderError, ProviderSettings, TTSProvider


class ComfyTTSSettings(ProviderSettings):
    workflow: Path = Path("workflows/tts_f5_comfy.json")
    ref_audio: Path = Path("assets/voices/narrator.wav")
    ref_text: str = ""  # exact transcript of ref_audio
    speed: float = 1.0
    seed: int = 1  # fixed seed keeps intonation stable between regenerations
    exaggeration: float = 0.5  # Chatterbox: emotion intensity (0.25 flat - 1.0+ dramatic)
    cfg_weight: float = 0.5  # Chatterbox: lower = slower, more deliberate delivery


class ComfyTTS(TTSProvider):
    name = "comfyui"
    env_prefix = "TTS_COMFY_"
    Settings = ComfyTTSSettings
    settings: ComfyTTSSettings

    def __init__(self, settings, app):
        super().__init__(settings, app)
        self._wf = template.load(self.settings.workflow)
        self._needs_ref = "ref_audio" in template.placeholders(self._wf)
        if self._needs_ref and not self.settings.ref_audio.exists():
            raise ProviderError(f"reference audio not found: {self.settings.ref_audio} (set TTS_COMFY_REF_AUDIO; "
                                f"{self.settings.workflow.name} clones a voice)")
        self._client = ComfyClient(app.comfy_url, app.comfy_timeout, app.comfy_poll_interval)
        self._ref_name: str | None = None

    def describe(self) -> str:
        return f"comfyui:{self.settings.workflow.name}"

    def verify(self) -> None:
        if "ref_text" in template.placeholders(self._wf) and not self.settings.ref_text.strip():
            raise ProviderError("TTS_COMFY_REF_TEXT is empty but the workflow uses {{ref_text}}")

    def _ref(self) -> str:
        if self._ref_name is None:
            data = self.settings.ref_audio.read_bytes()
            digest = hashlib.sha1(data).hexdigest()[:10]
            name = f"shorts_ref_{digest}{self.settings.ref_audio.suffix.lower()}"
            self._ref_name = self._client.upload_image(self.settings.ref_audio, name=name)
        return self._ref_name

    def synthesize(self, text: str, out_path: Path) -> float:
        wf = template.fill(self._wf, {
            "text": text, "ref_audio": self._ref() if self._needs_ref else "", "ref_text": self.settings.ref_text,
            "seed": self.settings.seed, "speed": self.settings.speed, "exaggeration": self.settings.exaggeration,
            "cfg_weight": self.settings.cfg_weight, "filename_prefix": "shorts/voice",
        })
        got = self._client.run(wf, out_path, kind="audio")
        if got != out_path:  # e.g. SaveAudio writes FLAC: convert to the WAV the pipeline expects
            ffmpeg.run(["-i", got, "-ac", "1", "-c:a", "pcm_s16le", out_path])
            got.unlink()
        return ffmpeg.duration(out_path)

    def unload(self) -> None:
        self._client.free()
