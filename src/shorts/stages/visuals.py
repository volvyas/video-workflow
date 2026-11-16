"""Images and motion stages."""

from __future__ import annotations

import logging
from pathlib import Path

from shorts.context import Context
from shorts.run import Run

log = logging.getLogger("shorts.stage.visuals")


def scene_file(run: Run, folder: str, index: int) -> Path | None:
    matches = sorted((run.path / folder).glob(f"scene_{index + 1:02d}.*"))
    return matches[0] if matches else None


def generate_images(ctx: Context, run: Run, only: set[int] | None = None) -> None:
    app = ctx.app
    script = run.script()
    provider = ctx.image()
    for i, scene in enumerate(script.scenes):
        out = run.scene_file("images", i, "png")
        if out.exists() and not (only and i in only):
            continue
        log.info("[%s] image %d/%d", run.id, i + 1, len(script.scenes))
        prompt = f"{scene.image_prompt.strip().rstrip('.')}, {app.style_prompt}" if app.style_prompt else scene.image_prompt
        provider.generate(prompt=prompt, negative=app.negative_prompt, width=app.image_width,
                          height=app.image_height, seed=run.manifest.seed + i, out_path=out)


def wants_primary(policy: str, index: int) -> bool:
    policy = policy.strip().lower()
    if policy == "all":
        return True
    if policy == "none":
        return False
    kind, _, n = policy.partition(":")
    if kind == "first":
        return index < int(n)
    if kind == "every":
        return index % int(n) == 0
    raise ValueError(f"bad MOTION_SCENES value '{policy}' (all | none | first:N | every:N)")


def generate_motion(ctx: Context, run: Run, only: set[int] | None = None) -> dict[str, str]:
    """Returns {scene_number: provider_used}. Falls back per scene if the primary provider fails."""
    script, timings = run.script(), run.timings()
    used: dict[str, str] = {}
    errors: dict[str, str] = {}
    primary, fallback = ctx.motion(), ctx.motion(fallback=True)
    for i, (scene, t) in enumerate(zip(script.scenes, timings)):
        existing = scene_file(run, "clips", i)
        if existing and not (only and i in only):
            used[str(i + 1)] = run.stage("motion").notes.get("providers", {}).get(str(i + 1), "existing")
            continue
        if existing:
            existing.unlink()
        image = run.scene_file("images", i, "png")
        out = run.scene_file("clips", i, "mp4")
        chain = [p for p in ([primary] if primary and wants_primary(ctx.app.motion_scenes, i) else []) + [fallback] if p]
        if not chain:
            raise RuntimeError("no motion provider configured (MOTION_PROVIDER / MOTION_FALLBACK)")
        for p in dict.fromkeys(chain):  # dedupe, keep order
            try:
                log.info("[%s] motion %d/%d via %s", run.id, i + 1, len(script.scenes), p.describe())
                p.animate(image=image, prompt=scene.motion_prompt, camera=scene.camera, duration=t.slot,
                          seed=run.manifest.seed + i, out_path=out)
                used[str(i + 1)] = p.describe()
                break
            except Exception as e:  # noqa: BLE001 - try the next provider
                log.warning("motion scene %d failed with %s: %s", i + 1, p.describe(), e)
                errors[str(i + 1)] = f"{p.describe()}: {e}"
        else:
            raise RuntimeError(f"all motion providers failed for scene {i + 1}: {errors.get(str(i + 1))}")
    run.stage("motion").notes.update({"providers": used, "errors": errors})
    return used
