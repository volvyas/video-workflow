"""Images and motion stages."""

from __future__ import annotations

import logging
from pathlib import Path

from shorts.context import Context
from shorts.models import ImageVerdict, Scene
from shorts.prompts import render
from shorts.run import Run

log = logging.getLogger("shorts.stage.visuals")


def scene_file(run: Run, folder: str, index: int) -> Path | None:
    matches = sorted((run.path / folder).glob(f"scene_{index + 1:02d}.*"))
    return matches[0] if matches else None


def image_prompt(app, script, scene: Scene) -> str:
    parts = [scene.image_prompt.strip().rstrip(".")]
    if script.visual_style.strip():
        parts.append(script.visual_style.strip().rstrip("."))
    if app.style_prompt:
        parts.append(app.style_prompt)
    return ", ".join(parts)


def _check(ctx: Context, image: Path, prompt: str, scene: Scene) -> ImageVerdict:
    app = ctx.app
    llm = ctx.llm("image_qa")
    return llm.generate_json(
        system=render("image_qa_system", app.prompts_dir),
        prompt=render("image_qa_user", app.prompts_dir, prompt=prompt, narration=scene.narration),
        schema=ImageVerdict, images=[image],
    )


def generate_images(ctx: Context, run: Run, only: set[int] | None = None) -> None:
    app = ctx.app
    script = run.script()
    provider = ctx.image()
    qa_notes: dict = run.stage("images").notes.setdefault("qa", {})
    qa = app.image_qa_enabled
    if qa and not ctx.llm("image_qa").supports_vision:
        log.warning("image QA disabled: %s has no vision support", ctx.llm("image_qa").describe())
        qa = False
    for i, scene in enumerate(script.scenes):
        out = run.scene_file("images", i, "png")
        if out.exists() and not (only and i in only):
            continue
        prompt = image_prompt(app, script, scene)
        attempts = 1 + (max(0, app.image_qa_max_retries) if qa else 0)
        best: tuple[int, Path] | None = None
        history = []
        passed = False
        for k in range(attempts):
            seed = run.manifest.seed + i + 1000 * k
            cand = out if k == 0 else run.sub("images", "candidates", f"scene_{i + 1:02d}_try{k + 1}.png")
            log.info("[%s] image %d/%d%s", run.id, i + 1, len(script.scenes), f" (re-roll {k})" if k else "")
            provider.generate(prompt=prompt, negative=app.negative_prompt, width=app.image_width,
                              height=app.image_height, seed=seed, out_path=cand)
            if not qa:
                best = (0, cand)
                break
            verdict = _check(ctx, cand, prompt, scene)
            history.append({"try": k + 1, "seed": seed, **verdict.model_dump()})
            if best is None or verdict.score > best[0]:
                best = (verdict.score, cand)
            if verdict.acceptable(app.image_qa_min_score):
                best, passed = (verdict.score, cand), True
                break
            log.info("[%s] image %d rejected (score %d): %s", run.id, i + 1, verdict.score,
                     "; ".join(verdict.problems) or "flags set")
        assert best is not None
        if best[1] != out:  # keep the best candidate as the scene image; the first try moves to candidates/
            if out.exists():
                out.replace(run.sub("images", "candidates", f"scene_{i + 1:02d}_try1.png"))
            best[1].replace(out)
        if qa:
            qa_notes[str(i + 1)] = history
            if not passed:
                log.warning("[%s] scene %d: no candidate passed QA; using the best (score %d)", run.id, i + 1, best[0])


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
