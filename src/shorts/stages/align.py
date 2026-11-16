"""Align stage: per-scene word timings -> words.json on the global timeline."""

from __future__ import annotations

from shorts.context import Context
from shorts.models import WordTiming
from shorts.run import Run


def align(ctx: Context, run: Run) -> list[WordTiming]:
    script, timings = run.script(), run.timings()
    aligner = ctx.aligner()
    words: list[WordTiming] = []
    for scene, timing in zip(script.scenes, timings):
        audio = run.scene_file("audio", timing.index, "wav")
        for w in aligner.align(audio, scene.narration.strip()):
            end = min(w.end, timing.duration)
            words.append(WordTiming(word=w.word, start=timing.start + w.start, end=timing.start + max(end, w.start)))
    run.write_json("words.json", words)
    return words
