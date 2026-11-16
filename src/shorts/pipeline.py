"""Orchestration: run stages for one or many runs, stage by stage, unloading GPU models in between."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable

from shorts.context import Context
from shorts.media.assemble import assemble
from shorts.models import StoredIdea
from shorts.run import STAGES, Run
from shorts.stages import align, ideas, review, script, visuals, voice

log = logging.getLogger("shorts.pipeline")

# stages that run during `make`; upload is separate (review gate)
BUILD_STAGES = ["script", "review", "voice", "align", "images", "motion", "assemble"]


def _provider_name(ctx: Context, stage: str) -> str:
    a = ctx.app
    return {
        "script": a.llm_for("script"), "review": a.llm_for("review"), "voice": a.tts_provider, "align": a.align_provider,
        "images": a.image_provider, "motion": f"{a.motion_provider}->{a.motion_fallback}",
        "assemble": "ffmpeg", "upload": a.uploader,
    }.get(stage, "")


def _runner(stage: str) -> Callable[[Context, Run, set[int] | None], object]:
    return {
        "script": lambda c, r, o: script.write_script(c, r),
        "review": lambda c, r, o: review.review_script(c, r),
        "voice": voice.synthesize,
        "align": lambda c, r, o: align.align(c, r),
        "motion": visuals.generate_motion,
        "assemble": lambda c, r, o: assemble(c.app, r, [visuals.scene_file(r, "clips", t.index) for t in r.timings()]),
    }[stage]


# stages that process all runs together (stage function gets the list of runs, returns {run_id: error})
_BATCH = {"images": visuals.generate_images_batch}

# stage -> provider kind to unload after the stage finishes for the whole batch
_UNLOAD_AFTER = {"review": "llm", "voice": "tts", "align": "align", "images": "image", "motion": "motion"}
# the image stage may also have loaded the QA LLM
_UNLOAD_ALSO = {"images": "llm"}


def create_runs(ctx: Context, topic: str, picked: list[StoredIdea]) -> list[Run]:
    runs = []
    for idea in picked:
        run = Run.create(ctx.app.runs_dir, topic, idea, ctx.new_seed(), idea_id=idea.id)
        ideas.mark_used(ctx.app, topic, idea.id, run.id)
        runs.append(run)
    return runs


def execute(ctx: Context, runs: list[Run], stages: list[str] | None = None, *, force: set[str] | None = None,
            only_scenes: set[int] | None = None) -> dict[str, str]:
    """Run the given stages over all runs, stage-major. Returns {run_id: 'ok' | error}."""
    stages = stages or BUILD_STAGES
    force = force or set()
    for s in sorted(force, key=STAGES.index):
        for r in runs:
            r.invalidate(s)
    status: dict[str, str] = {r.id: "ok" for r in runs}
    for stage in stages:
        active = [r for r in runs if status[r.id] == "ok" and (not r.is_done(stage) or only_scenes)]
        if not active:
            continue
        log.info("=== stage %s (%d run%s)", stage, len(active), "s" if len(active) > 1 else "")
        if stage in _BATCH:
            for r in active:
                r.start(stage, _provider_name(ctx, stage))
            try:
                errs = _BATCH[stage](ctx, active, only_scenes)
            except Exception as e:  # noqa: BLE001
                log.debug(traceback.format_exc())
                errs = {r.id: f"{type(e).__name__}: {e}" for r in active}
            for r in active:
                if r.id in errs:
                    log.error("[%s] stage %s failed: %s", r.id, stage, errs[r.id])
                    r.fail(stage, errs[r.id])
                    status[r.id] = f"{stage}: {errs[r.id]}"
                else:
                    r.finish(stage)
            for kind in (_UNLOAD_AFTER.get(stage), _UNLOAD_ALSO.get(stage)):
                if kind:
                    ctx.unload(kind)
            continue
        for r in active:
            r.start(stage, _provider_name(ctx, stage))
            try:
                _runner(stage)(ctx, r, only_scenes)
                r.finish(stage)
            except Exception as e:  # noqa: BLE001 - one bad run must not stop the batch
                log.error("[%s] stage %s failed: %s", r.id, stage, e)
                log.debug(traceback.format_exc())
                r.fail(stage, f"{type(e).__name__}: {e}")
                status[r.id] = f"{stage}: {e}"
        if stage in _UNLOAD_AFTER:
            ctx.unload(_UNLOAD_AFTER[stage])
    return status
