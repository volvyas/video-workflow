"""Voice stage: per-scene TTS -> audio/scene_XX.wav, audio/voice.wav, timings.json."""

from __future__ import annotations

import logging

from shorts.context import Context
from shorts.media import ffmpeg
from shorts.models import SceneTiming
from shorts.run import Run

log = logging.getLogger("shorts.stage.voice")


def synthesize(ctx: Context, run: Run, only: set[int] | None = None) -> list[SceneTiming]:
    """Generate missing scene audio (or the scenes in `only`), then rebuild voice.wav and timings."""
    script = run.script()
    tts = ctx.tts()
    gap = ctx.app.scene_gap_ms / 1000
    files, timings, t = [], [], 0.0
    for i, scene in enumerate(script.scenes):
        out = run.scene_file("audio", i, "wav")
        if not out.exists() or (only is not None and i in only):
            log.info(f"[{run.id}] voice scene {i + 1}/{len(script.scenes)}")
            dur = tts.synthesize(scene.narration.strip(), out)
        else:
            dur = ffmpeg.duration(out)
        files.append(out)
        timings.append(SceneTiming(index=i, start=t, duration=dur, slot=dur + gap))
        t += dur + gap
    ffmpeg.concat_audio(files, [gap] * len(files), run.sub("audio", "voice.wav"))
    run.write_json("timings.json", timings)
    return timings

