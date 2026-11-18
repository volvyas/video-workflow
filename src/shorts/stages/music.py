"""Music stage: a background track for the whole video (generated to fit, or picked from MUSIC_DIR)."""

from __future__ import annotations

from shorts.context import Context
from shorts.run import Run

MUSIC_BASENAME = "music"


def music_file(run: Run):
    """The run's music track, whatever its format, or None."""
    return next(iter(sorted(run.path.glob(f"{MUSIC_BASENAME}.*"))), None)


def make_music(ctx: Context, run: Run) -> None:
    for old in run.path.glob(f"{MUSIC_BASENAME}.*"):
        old.unlink()
    seconds = sum(t.slot for t in run.timings())
    spec = run.script().music
    got = ctx.music().generate(tags=spec.tags, bpm=spec.bpm, key=spec.key, seconds=seconds,
                               seed=run.manifest.seed, out_path=run.path / f"{MUSIC_BASENAME}.wav")
    run.stage("music").notes.update({"provider": ctx.app.music_provider, "file": got.name if got else None,
                                     "tags": spec.tags, "bpm": spec.bpm, "key": spec.key})
